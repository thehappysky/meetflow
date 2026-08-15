"""Owner-private recordings and fixed team/company evidence submissions."""
import json
from datetime import date, datetime
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .audio import audio_available
from .database import Account, Correction, Meeting, PersonalRecording, Submission, row_dict
from .domain import Contract, DomainError, MeetingInput, digest, now, uid


class RecordingInput(Contract):
    title: str = Field(default="", max_length=200)
    customer: str = Field(default="", max_length=200)
    meeting_date: str = ""
    transcript: str = Field(min_length=2, max_length=200000)


class RecordingEdit(Contract):
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    customer: str = Field(default="", max_length=200)
    meeting_date: str = ""


class SubmitInput(Contract):
    version: int = Field(ge=1)
    customer: str = Field(min_length=1, max_length=200)
    request_key: str = Field(min_length=8, max_length=150)


class CorrectionInput(Contract):
    base_revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    customer: str = Field(min_length=1, max_length=200)
    meeting_date: str = ""
    reason: str = Field(min_length=1, max_length=1000)


class DecisionInput(Contract):
    approve: bool


class DeleteInput(Contract):
    reason: str = Field(min_length=1, max_length=1000)


def valid_date(value):
    if value:
        try:
            date.fromisoformat(value)
        except ValueError:
            raise DomainError("会议日期格式应为 YYYY-MM-DD") from None
    return value


class WorkspaceService:
    def __init__(self, store, driver, auth):
        self.store, self.driver, self.auth = store, driver, auth
        self.sessions, self.settings = store.sessions, store.settings

    def private(self, session, key, user):
        row = session.get(PersonalRecording, key)
        if not row or row.deleted or row.owner_id != user["id"] or row.company_id != user["company_id"]:
            raise DomainError("个人录音不存在或无权访问", 404)
        return row

    @staticmethod
    def visible(row, user):
        if row.company_id != user["company_id"]:
            return False
        return (user["role"] in {"admin", "boss"} or
                user["role"] == "manager" and row.team_id == user["team_id"] or
                user["role"] == "sales" and row.owner_id == user["id"])

    def central(self, session, key, user):
        row = session.get(Submission, key)
        if not row or row.deleted or not self.visible(row, user):
            raise DomainError("中心记录不存在或无权访问", 404)
        return row

    def recording_dict(self, row):
        run = self.store.get_run(row.run_id)
        return {"id": row.id, "body": row.body, "version": row.version, "uploaded_at": row.created_at,
                "run_id": row.run_id, "status": run["status"], "phase": run["phase"], "error": run["error"]}

    def create(self, user, value):
        if user["role"] != "sales":
            raise DomainError("请使用销售账号上传到个人区", 403)
        valid_date(value.meeting_date)
        title = value.title.strip() or datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%m月%d日 %H:%M 上传的会议录音")
        with self.sessions() as session:
            account = session.get(Account, user["id"])
            project_id = account.project_id
        # Each owner has an isolated project; another salesperson's private context is never included.
        result = self.store.create_meeting(MeetingInput(project_id=project_id, title=title,
            salesperson=user["name"], customer=value.customer, transcript=value.transcript,
            mode="live" if self.settings.live_available else "demo"))
        return self.attach(user, result, title, value.customer, value.meeting_date)

    def attach(self, user, result, title, customer, meeting_date):
        with self.sessions.begin() as session:
            row = PersonalRecording(id=uid("rec_"), owner_id=user["id"], company_id=user["company_id"],
                team_id=user["team_id"], run_id=result["run"]["id"],
                body={"title": title, "customer": customer, "meeting_date": valid_date(meeting_date)})
            session.add(row)
            session.flush()
            self.store.audit(session, "workspace.recording_created", row.id, {"owner": user["id"]})
            output = self.recording_dict(row)
        if self.settings.start_worker:
            self.driver.submit(row.run_id)
        return output

    def submit(self, user, key, value):
        if not value.customer.strip():
            raise DomainError("上报前请选择客户／商机")
        request_hash = digest({"recording": key, **value.model_dump()})
        try:
            with self.sessions.begin() as session:
                if self.settings.db_url.startswith("sqlite"):
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                prior = session.scalar(select(Submission).where(Submission.request_key == value.request_key))
                if prior:
                    if prior.owner_id != user["id"] or prior.request_hash != request_hash:
                        raise DomainError("重复请求标识对应其他内容", 409)
                    return {"id": prior.id, "revision": prior.revision}
                row = self.private(session, key, user)
                if row.version != value.version:
                    raise DomainError("个人记录已变化，请刷新后上报", 409)
                if session.scalar(select(Submission.id).where(Submission.recording_id == key)):
                    raise DomainError("已上报；如需修改中心记录请申请更正", 409)
                report = self.store.latest_report(row.run_id)
                run = self.store.get_run(row.run_id)
                if not report or run["status"] not in {"waiting_review", "completed"}:
                    raise DomainError("请等待整理完成后上报", 409)
                meeting = session.get(Meeting, run["meeting_id"])
                snapshot = {"metadata": {**row.body, "customer": value.customer.strip()},
                    "owner_name": user["name"], "report": report["body"],
                    "evidence": self.store.source_map(row.run_id), "run_id": row.run_id,
                    "audio_path": meeting.audio_path, "recording_version": row.version}
                result = Submission(id=uid("sub_"), recording_id=key, owner_id=user["id"],
                    company_id=user["company_id"], team_id=row.team_id, snapshot=snapshot,
                    request_key=value.request_key, request_hash=request_hash)
                session.add(result)
                session.flush()
                self.store.audit(session, "workspace.submitted", result.id,
                                 {"actor": user["id"], "recording_id": key})
                return {"id": result.id, "revision": result.revision}
        except IntegrityError:
            raise DomainError("已上报或请求冲突，请刷新查看", 409) from None

    @staticmethod
    def central_dict(row, detail=False):
        result = {"id": row.id, "recording_id": row.recording_id, "revision": row.revision,
                  "team_id": row.team_id, "owner": row.snapshot["owner_name"],
                  "metadata": row.snapshot["metadata"], "submitted_at": row.created_at}
        if detail:
            result.update(report=row.snapshot["report"], evidence=row.snapshot["evidence"],
                          has_audio=bool(row.snapshot.get("audio_path")),
                          history=[{"revision": h["revision"], "metadata": h["snapshot"]["metadata"],
                                    "at": h["at"]} for h in row.history])
        return result

    def audio(self, path):
        path = Path(path or "").resolve()
        if not path.is_relative_to(self.settings.data_dir.resolve() / "audio") or not path.is_file():
            raise DomainError("录音不可用", 404)
        return FileResponse(path)


def workspace_router(service):
    router = APIRouter(prefix="/api/workspace", tags=["workspace"])
    current_user = Depends(service.auth.identity)

    @router.get("/recordings")
    def recordings(user=current_user):
        with service.sessions() as session:
            rows = session.scalars(select(PersonalRecording).where(PersonalRecording.owner_id == user["id"],
                PersonalRecording.company_id == user["company_id"], PersonalRecording.deleted.is_(False))
                .order_by(PersonalRecording.created_at.desc())).all()
            return [service.recording_dict(row) for row in rows]

    @router.post("/recordings", status_code=201)
    def create(value: RecordingInput, user=current_user):
        return service.create(user, value)

    @router.post("/audio", status_code=201)
    async def audio_upload(file: Annotated[UploadFile, File()], metadata: Annotated[str, Form()] = "{}",
                           user=current_user):
        if user["role"] != "sales":
            raise DomainError("请使用销售账号上传", 403)
        if not audio_available(service.settings):
            raise DomainError("ASR 未配置，可先导入已有转写", 422)
        try:
            value = RecordingInput.model_validate({**json.loads(metadata), "transcript": "等待真实转写"})
        except (ValueError, TypeError):
            raise DomainError("会谈信息格式错误") from None
        title = value.title.strip() or datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%m月%d日 %H:%M 上传的会议录音")
        valid_date(value.meeting_date)
        extension = Path(file.filename or "").suffix.lower()
        if extension not in {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm"}:
            raise DomainError("录音格式不支持", 422)
        directory = service.settings.data_dir.resolve() / "audio"
        directory.mkdir(exist_ok=True)
        path, size = directory / (uid("audio_") + extension), 0
        try:
            with path.open("wb") as destination:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > service.settings.max_upload_mb * 1024 * 1024:
                        raise DomainError("录音超过上传上限", 413)
                    destination.write(chunk)
            if not size:
                raise DomainError("录音为空", 422)
            with service.sessions.begin() as session:
                account = session.get(Account, user["id"])
                meeting = Meeting(id=uid("m_"), project_id=account.project_id, title=title,
                                  timezone="Asia/Shanghai", audio_path=str(path))
                session.add(meeting)
                session.flush()
                run = service.store.create_run(session, meeting,
                    "live" if service.settings.live_available else "demo")
                result = {"run": row_dict(run)}
            return service.attach(user, result, title, value.customer, value.meeting_date)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @router.get("/recordings/{key}")
    def detail(key: str, user=current_user):
        with service.sessions() as session:
            row = service.private(session, key, user)
            run = service.store.get_run(row.run_id)
            meeting = session.get(Meeting, run["meeting_id"])
            submitted = session.scalar(select(Submission).where(Submission.recording_id == key))
            return {**service.recording_dict(row), "report": service.store.latest_report(row.run_id),
                    "evidence": service.store.source_map(row.run_id), "has_audio": bool(meeting.audio_path),
                    "submission": {"id": submitted.id, "deleted": submitted.deleted} if submitted else None}

    @router.patch("/recordings/{key}")
    def edit(key: str, value: RecordingEdit, user=current_user):
        valid_date(value.meeting_date)
        if not value.title.strip():
            raise DomainError("录音名称不能为空")
        with service.sessions.begin() as session:
            row = service.private(session, key, user)
            changed = session.execute(update(PersonalRecording).where(PersonalRecording.id == row.id,
                PersonalRecording.version == value.version).values(
                    body=value.model_dump(exclude={"version"}), version=value.version + 1))
            if changed.rowcount != 1:
                raise DomainError("记录已变化，请刷新", 409)
            service.store.audit(session, "workspace.personal_edited", key, {"actor": user["id"]})
        return {"version": value.version + 1}

    @router.delete("/recordings/{key}")
    def delete(key: str, user=current_user):
        with service.sessions.begin() as session:
            row = service.private(session, key, user)
            row.deleted = True
            service.store.audit(session, "workspace.personal_deleted", key, {"actor": user["id"]})
        return {"deleted": True, "notice": "个人记录已移除，中心上报证据不受影响"}

    @router.get("/recordings/{key}/audio")
    def private_audio(key: str, user=current_user):
        with service.sessions() as session:
            row = service.private(session, key, user)
            run = service.store.get_run(row.run_id)
            path = session.get(Meeting, run["meeting_id"]).audio_path
        return service.audio(path)

    @router.post("/recordings/{key}/submit", status_code=201)
    def submit(key: str, value: SubmitInput, user=current_user):
        return service.submit(user, key, value)

    @router.get("/center")
    def center(user=current_user, q: str = ""):
        with service.sessions() as session:
            rows = session.scalars(select(Submission).where(Submission.company_id == user["company_id"],
                Submission.deleted.is_(False)).order_by(Submission.created_at.desc())).all()
            return [service.central_dict(row) for row in rows if service.visible(row, user)
                    and q.lower() in (row.snapshot["metadata"]["title"] + row.snapshot["metadata"]["customer"]).lower()]

    @router.get("/center/{key}")
    def center_detail(key: str, user=current_user):
        with service.sessions() as session:
            return service.central_dict(service.central(session, key, user), detail=True)

    @router.get("/center/{key}/audio")
    def center_audio(key: str, user=current_user):
        with service.sessions() as session:
            path = service.central(session, key, user).snapshot.get("audio_path")
        return service.audio(path)

    @router.get("/center/{key}/export")
    def export(key: str, user=current_user):
        with service.sessions() as session:
            row = service.central(session, key, user)
            lines = ["# " + row.snapshot["metadata"]["title"], f"中心版本：{row.revision}",
                     "分析模式：" + row.snapshot["report"]["mode"], row.snapshot["report"]["summary"], "## 原文依据"]
            lines.extend("- " + s["text"] for s in row.snapshot["evidence"].values())
        return Response("\n\n".join(lines), media_type="text/markdown; charset=utf-8")

    @router.post("/center/{key}/delete")
    def remove_center(key: str, value: DeleteInput, user=current_user):
        with service.sessions.begin() as session:
            row = service.central(session, key, user)
            if user["role"] != "manager" or row.team_id != user["team_id"]:
                raise DomainError("中心删除需由负责团队的主管处理", 403)
            row.deleted = True
            service.store.audit(session, "workspace.center_deleted", key,
                                {"actor": user["id"], "reason": value.reason})
        return {"deleted": True, "notice": "中心记录已撤下，历史与个人副本保留"}

    @router.post("/center/{key}/corrections", status_code=201)
    def correction(key: str, value: CorrectionInput, user=current_user):
        valid_date(value.meeting_date)
        if not all(item.strip() for item in (value.title, value.customer, value.reason)):
            raise DomainError("更正名称、客户及原因不能为空")
        with service.sessions.begin() as session:
            row = service.central(session, key, user)
            if row.owner_id != user["id"] or user["role"] != "sales":
                raise DomainError("仅来源销售可以提出更正", 403)
            if row.revision != value.base_revision:
                raise DomainError("中心已更新，请刷新后提出更正", 409)
            request = Correction(id=uid("cor_"), submission_id=key, requester_id=user["id"],
                base_revision=value.base_revision, proposed=value.model_dump(exclude={"base_revision", "reason"}),
                reason=value.reason)
            session.add(request)
            session.flush()
            service.store.audit(session, "workspace.correction_requested", request.id)
            return {"id": request.id}

    @router.get("/corrections")
    def corrections(user=current_user):
        with service.sessions() as session:
            rows = session.execute(select(Correction, Submission).join(
                Submission, Correction.submission_id == Submission.id).where(
                    Submission.company_id == user["company_id"], Submission.deleted.is_(False))).all()
            return [{**row_dict(c), "before": next((h["snapshot"]["metadata"] for h in s.history
                      if h["revision"] == c.base_revision), s.snapshot["metadata"])} for c, s in rows
                    if service.visible(s, user)]

    @router.post("/corrections/{key}/review")
    def review(key: str, value: DecisionInput, user=current_user):
        with service.sessions.begin() as session:
            if service.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            request = session.get(Correction, key)
            if not request:
                raise DomainError("更正申请不存在", 404)
            row = service.central(session, request.submission_id, user)
            if user["role"] != "manager" or row.team_id != user["team_id"]:
                raise DomainError("更正需由负责团队的主管确认", 403)
            if request.status != "pending":
                raise DomainError("申请已处理", 409)
            if request.base_revision != row.revision:
                raise DomainError("中心版本已变化，请重新申请", 409)
            if value.approve:
                row.history = [*row.history, {"revision": row.revision, "snapshot": row.snapshot, "at": now()}]
                row.snapshot = {**row.snapshot, "metadata": request.proposed}
                row.revision += 1
            request.status = "approved" if value.approve else "rejected"
            request.reviewed_by = user["id"]
            service.store.audit(session, "workspace.correction_reviewed", key,
                                {"actor": user["id"], "approve": value.approve})
        return {"status": request.status, "revision": row.revision}

    return router
