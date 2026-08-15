"""Create a separate local test company. Passwords stay in ignored data/access-demo.json.

Never resets existing accounts. No invitations are emailed or sent to real people.
"""
import json
import secrets

from meetflow.auth import password_digest
from meetflow.config import Settings
from meetflow.database import Account, Company, Team, make_database
from meetflow.domain import uid
from sqlalchemy import select


def main():
    settings = Settings()
    path = settings.data_dir / "access-demo.json"
    if path.exists():
        print("Existing test account sheet preserved:", path.resolve())
        return
    engine, sessions = make_database(settings)
    with sessions.begin() as session:
        if session.scalar(select(Account.id).where(Account.username == "qa.admin")):
            raise SystemExit("Test account already exists; refusing to replace credentials")
        company = Company(id=uid("co_"), name="MeetFlow 权限测试公司（合成）")
        session.add(company)
        session.flush()
        teams = [Team(id=uid("team_"), company_id=company.id, name=name)
                 for name in ("企业服务一组", "企业服务二组")]
        session.add_all(teams)
        session.flush()
        accounts = []
        for username, name, role, team in [
            ("qa.admin", "测试管理员", "admin", None),
            ("qa.boss", "测试老板", "boss", None),
            ("qa.manager.a", "一组主管", "manager", teams[0]),
            ("qa.manager.b", "二组主管", "manager", teams[1]),
        ]:
            password = secrets.token_urlsafe(16)
            user = Account(id=uid("u_"), company_id=company.id, team_id=team.id if team else None,
                username=username, name=name, role=role, password_hash=password_digest(password))
            session.add(user)
            accounts.append({"username": username, "password": password, "role": role, "name": name})
        session.flush()
        output = {"company_id": company.id, "teams": [{"id": t.id, "name": t.name} for t in teams],
                  "accounts": accounts, "notice": "Local synthetic test accounts only. Do not publish this file."}
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    engine.dispose()
    print("Created local test company; credentials saved to", path.resolve())


if __name__ == "__main__":
    main()
