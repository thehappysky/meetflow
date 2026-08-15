"""Real local accounts, one-use team-bound invitations and revocable per-tab sessions."""
import hashlib
import hmac
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Request
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .database import AccessSession, Account, Company, Invitation, Project, Team, row_dict
from .domain import Contract, DomainError, now, uid


def token_digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_digest(password, salt=None):
    salt = salt or secrets.token_hex(16)
    value = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"scrypt:{salt}:{value}"


def verify_password(password, encoded):
    try:
        _, salt, _ = encoded.split(":")
        return hmac.compare_digest(password_digest(password, salt), encoded)
    except (ValueError, TypeError):
        return False


def future(hours):
    return (datetime.now(UTC) + timedelta(hours=hours)).isoformat()


class LoginInput(Contract):
    username: str = Field(min_length=3, max_length=120, pattern=r"^[a-zA-Z0-9_.@+-]+$")
    password: str = Field(min_length=1, max_length=128)


class RegisterInput(LoginInput):
    invitation: str = Field(min_length=20, max_length=200)
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=128)


class TeamInput(Contract):
    name: str = Field(min_length=1, max_length=80)


class InviteInput(Contract):
    team_id: str
    valid_hours: int = Field(default=168, ge=1, le=720)


class StaffInput(LoginInput):
    password: str = Field(min_length=12, max_length=128)
    name: str = Field(min_length=1, max_length=80)
    role: Literal["boss", "manager"]
    team_id: str | None = None


class AuthService:
    def __init__(self, store):
        self.store, self.sessions = store, store.sessions
        self.attempts, self.lock = {}, threading.Lock()
        self.dummy_hash = password_digest("not-a-real-account-password")

    @staticmethod
    def public(session, user):
        team = session.get(Team, user.team_id) if user.team_id else None
        company = session.get(Company, user.company_id)
        return {"id": user.id, "username": user.username, "name": user.name, "role": user.role,
                "team_id": user.team_id, "team": team.name if team else None,
                "company_id": user.company_id, "company": company.name}

    def identity(self, request: Request):
        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer ") or len(header) > 500:
            raise DomainError("请先登录", 401)
        with self.sessions() as session:
            login = session.get(AccessSession, token_digest(header[7:]))
            user = session.get(Account, login.account_id) if login else None
            if not login or login.expires_at <= now() or not user or not user.active:
                raise DomainError("登录已失效，请重新登录", 401)
            return self.public(session, user)

    def administrator(self, user):
        if user["role"] != "admin":
            raise DomainError("仅管理员可以管理账号和邀请码", 403)

    @staticmethod
    def team(session, team_id, company_id):
        team = session.get(Team, team_id)
        if not team or team.company_id != company_id:
            raise DomainError("团队不存在", 404)
        return team

    def issue_session(self, session, user):
        token = secrets.token_urlsafe(32)
        session.add(AccessSession(token_hash=token_digest(token), account_id=user.id, expires_at=future(8)))
        return {"token": token, "user": self.public(session, user)}

    def login(self, value, ip):
        bucket = ip + ":" + value.username.lower()
        with self.lock:
            recent = [stamp for stamp in self.attempts.get(bucket, []) if stamp > time.monotonic() - 60]
            if len(recent) >= 12:
                raise DomainError("登录尝试过多，请稍后重试", 429)
            self.attempts[bucket] = recent
        with self.sessions.begin() as session:
            user = session.scalar(select(Account).where(Account.username == value.username.lower()))
            checked = verify_password(value.password, user.password_hash if user else self.dummy_hash)
            if not user or not checked or not user.active:
                with self.lock:
                    self.attempts[bucket].append(time.monotonic())
                raise DomainError("账号或密码不正确", 401)
            self.store.audit(session, "auth.login", user.id)
            return self.issue_session(session, user)

    def invitation(self, code):
        with self.sessions() as session:
            invite = session.scalar(select(Invitation).where(Invitation.token_hash == token_digest(code)))
            if not invite or invite.revoked or invite.used_by or invite.expires_at <= now():
                raise DomainError("邀请码无效、已使用、已撤销或已过期", 410)
            team = session.get(Team, invite.team_id)
            return {"team": team.name, "company": session.get(Company, team.company_id).name,
                    "role": "sales", "expires_at": invite.expires_at}

    def register(self, value):
        try:
            with self.sessions.begin() as session:
                if self.store.settings.db_url.startswith("sqlite"):
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                invite = session.scalar(select(Invitation).where(
                    Invitation.token_hash == token_digest(value.invitation)))
                if not invite or invite.revoked or invite.used_by or invite.expires_at <= now():
                    raise DomainError("邀请码无效、已使用、已撤销或已过期", 410)
                self.team(session, invite.team_id, invite.company_id)
                project = Project(id=uid("p_"), name="个人工作区 · " + value.name)
                session.add(project)
                session.flush()
                user = Account(id=uid("u_"), company_id=invite.company_id, team_id=invite.team_id,
                    username=value.username.lower(), name=value.name, role="sales", project_id=project.id,
                    password_hash=password_digest(value.password))
                session.add(user)
                session.flush()
                invite.used_by = user.id
                self.store.audit(session, "auth.registered", user.id, {"team_id": user.team_id})
                return self.issue_session(session, user)
        except IntegrityError:
            raise DomainError("账号已存在或邀请码已被使用；请检查后重试", 409) from None


def auth_routers(service):
    current_user = Depends(service.identity)
    auth = APIRouter(prefix="/api/auth", tags=["accounts"])
    admin = APIRouter(prefix="/api/admin", tags=["administration"])

    @auth.post("/login")
    def login(value: LoginInput, request: Request):
        return service.login(value, request.client.host if request.client else "local")

    @auth.get("/me")
    def me(user=current_user):
        return user

    @auth.post("/invitation")
    def preview(value: dict):
        code = value.get("code")
        if not isinstance(code, str) or len(code) > 200:
            raise DomainError("无效邀请码", 410)
        return service.invitation(code)

    @auth.post("/register", status_code=201)
    def register(value: RegisterInput):
        return service.register(value)

    @auth.post("/logout")
    def logout(request: Request, user=current_user):
        with service.sessions.begin() as session:
            login = session.get(AccessSession, token_digest(request.headers["authorization"][7:]))
            session.delete(login)
        return {"status": "signed_out"}

    @admin.get("/teams")
    def teams(user=current_user):
        service.administrator(user)
        with service.sessions() as session:
            return [row_dict(t) for t in session.scalars(select(Team).where(Team.company_id == user["company_id"]))]

    @admin.post("/teams", status_code=201)
    def create_team(value: TeamInput, user=current_user):
        service.administrator(user)
        with service.sessions.begin() as session:
            team = Team(id=uid("team_"), company_id=user["company_id"], name=value.name)
            session.add(team)
            session.flush()
            service.store.audit(session, "team.created", team.id)
            return row_dict(team)

    @admin.get("/accounts")
    def accounts(user=current_user):
        service.administrator(user)
        with service.sessions() as session:
            return [service.public(session, row) for row in session.scalars(select(Account).where(
                Account.company_id == user["company_id"]))]

    @admin.post("/staff", status_code=201)
    def staff(value: StaffInput, user=current_user):
        service.administrator(user)
        try:
            with service.sessions.begin() as session:
                if value.role == "manager":
                    service.team(session, value.team_id, user["company_id"])
                elif value.team_id:
                    raise DomainError("老板账号无需绑定单个团队")
                account = Account(id=uid("u_"), company_id=user["company_id"], team_id=value.team_id,
                    username=value.username.lower(), name=value.name, role=value.role,
                    password_hash=password_digest(value.password))
                session.add(account)
                session.flush()
                service.store.audit(session, "account.created", account.id, {"role": account.role})
                return service.public(session, account)
        except IntegrityError:
            raise DomainError("账号名已存在", 409) from None

    @admin.post("/invitations", status_code=201)
    def invite(value: InviteInput, user=current_user):
        service.administrator(user)
        with service.sessions.begin() as session:
            service.team(session, value.team_id, user["company_id"])
            code = secrets.token_urlsafe(24)
            row = Invitation(id=uid("inv_"), token_hash=token_digest(code), company_id=user["company_id"],
                team_id=value.team_id, created_by=user["id"], expires_at=future(value.valid_hours))
            session.add(row)
            session.flush()
            service.store.audit(session, "invitation.created", row.id, {"team_id": row.team_id})
            return {"id": row.id, "code": code, "team_id": row.team_id, "expires_at": row.expires_at}

    @admin.get("/invitations")
    def invitations(user=current_user):
        service.administrator(user)
        with service.sessions() as session:
            return [{"id": row.id, "team_id": row.team_id, "expires_at": row.expires_at,
                     "used": bool(row.used_by), "revoked": row.revoked} for row in session.scalars(
                         select(Invitation).where(Invitation.company_id == user["company_id"]))]

    @admin.post("/invitations/{key}/revoke")
    def revoke(key: str, user=current_user):
        service.administrator(user)
        with service.sessions.begin() as session:
            row = session.get(Invitation, key)
            if not row or row.company_id != user["company_id"]:
                raise DomainError("邀请码不存在", 404)
            row.revoked = True
            service.store.audit(session, "invitation.revoked", key)
        return {"revoked": True}

    @admin.post("/accounts/{key}/disable")
    def disable(key: str, user=current_user):
        service.administrator(user)
        if key == user["id"]:
            raise DomainError("不能停用当前管理员")
        with service.sessions.begin() as session:
            result = session.execute(update(Account).where(Account.id == key,
                Account.company_id == user["company_id"]).values(active=False))
            if not result.rowcount:
                raise DomainError("账号不存在", 404)
            service.store.audit(session, "account.disabled", key)
        return {"disabled": True}

    return auth, admin
