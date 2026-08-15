import io
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from meetflow.api import create_app
from meetflow.auth import password_digest
from meetflow.config import Settings
from meetflow.database import AccessSession, Account, Company, Invitation, Team
from meetflow.domain import now
from sqlalchemy import select

PASSWORD = "Local-test-password-123!"


@pytest.fixture
def access(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'meetflow.db'}",
                              auth_enabled=True, start_worker=False, asr_model="", model_name="", model_api_key=""))
    with app.state.store.sessions.begin() as session:
        session.add_all([Company(id="c1", name="测试公司"), Company(id="c2", name="其他公司")])
        session.flush()
        session.add_all([Team(id="ta", company_id="c1", name="一组"), Team(id="tb", company_id="c1", name="二组"),
                         Team(id="tx", company_id="c2", name="外公司")])
        session.flush()
        session.add(Account(id="admin", company_id="c1", team_id=None, username="admin", name="管理员",
                            role="admin", password_hash=password_digest(PASSWORD)))
    with TestClient(app) as client:
        def login(username):
            result = client.post("/api/auth/login", json={"username": username, "password": PASSWORD})
            assert result.status_code == 200, result.text
            return {"Authorization": "Bearer " + result.json()["token"]}
        admin = login("admin")
        actors = {"admin": admin}
        for username, role, team in [("boss", "boss", None), ("manager.a", "manager", "ta"),
                                     ("manager.b", "manager", "tb")]:
            response = client.post("/api/admin/staff", headers=admin, json={"username": username,
                "name": username, "password": PASSWORD, "role": role, "team_id": team})
            assert response.status_code == 201, response.text
            actors[username] = login(username)
        for username, team in [("sales.a", "ta"), ("sales.a2", "ta"), ("sales.b", "tb")]:
            invite = client.post("/api/admin/invitations", headers=admin, json={"team_id": team}).json()
            result = client.post("/api/auth/register", json={"invitation": invite["code"], "username": username,
                "name": username, "password": PASSWORD})
            assert result.status_code == 201, result.text
            actors[username] = {"Authorization": "Bearer " + result.json()["token"]}
        opportunities = {}
        for username in ("sales.a", "sales.a2", "sales.b"):
            response = client.post("/api/workspace/business/opportunities", headers=actors[username],
                                   json={"customer_name": "合成客户", "title": "项目"})
            assert response.status_code == 201, response.text
            opportunities[username] = response.json()["id"]
        yield SimpleNamespace(app=app, client=client, actors=actors, login=login, opportunities=opportunities)


def invite(a, team="ta"):
    return a.client.post("/api/admin/invitations", headers=a.actors["admin"], json={"team_id": team}).json()


def recording(a, actor="sales.a", title="合成客户需求会谈"):
    result = a.client.post("/api/workspace/recordings", headers=a.actors[actor], json={"title": title,
        "transcript": "销售：您目前怎么整理采购？\n客户：表格比较慢，预算没定。\n销售：我来准备资料。"})
    assert result.status_code == 201, result.text
    row = result.json()
    a.app.state.driver.process(row["run_id"])
    return row


def submitted(a, actor="sales.a", key="submission-key-a"):
    row = recording(a, actor)
    response = a.client.post(f"/api/workspace/recordings/{row['id']}/submit", headers=a.actors[actor],
        json={"version": 1, "customer": "合成客户／项目", "opportunity_id": a.opportunities[actor], "request_key": key})
    assert response.status_code == 201, response.text
    return row, response.json()


def test_register_is_bound_to_invitation_and_rejects_role_and_team_injection(access):
    a = access
    token = invite(a)
    body = {"invitation": token["code"], "username": "new.sales", "name": "新销售", "password": PASSWORD}
    assert a.client.post("/api/auth/register", json={**body, "role": "boss", "team_id": "tb"}).status_code == 422
    response = a.client.post("/api/auth/register", json=body)
    assert response.status_code == 201
    assert response.json()["user"]["role"] == "sales" and response.json()["user"]["team_id"] == "ta"
    assert a.client.post("/api/auth/register", json={**body, "username": "another"}).status_code == 410


def test_expired_revoked_invalid_invites_and_foreign_team(access):
    a = access
    revoked = invite(a)
    assert a.client.post(f"/api/admin/invitations/{revoked['id']}/revoke", headers=a.actors["admin"]).status_code == 200
    assert a.client.post("/api/auth/invitation", json={"code": revoked["code"]}).status_code == 410
    expired = invite(a)
    with a.app.state.store.sessions.begin() as session:
        session.get(Invitation, expired["id"]).expires_at = "2000-01-01T00:00:00+00:00"
    for code in (expired["code"], "invalid-invitation-token"):
        assert a.client.post("/api/auth/register", json={"invitation": code, "username": "bad.invite",
            "password": PASSWORD, "name": "测试"}).status_code == 410
    assert a.client.post("/api/admin/invitations", headers=a.actors["admin"], json={"team_id": "tx"}).status_code == 404


def test_invitation_duplicate_registration_race_has_one_winner(access):
    a = access
    token = invite(a)
    def register(index):
        return a.client.post("/api/auth/register", json={"invitation": token["code"],
            "username": f"racing-{index}", "name": "竞态测试", "password": PASSWORD}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = sorted(pool.map(register, range(2)))
    assert statuses == [201, 410]


def test_username_conflict_does_not_consume_invitation(access):
    a = access
    token = invite(a)
    body = {"invitation": token["code"], "username": "sales.a", "name": "重复", "password": PASSWORD}
    assert a.client.post("/api/auth/register", json=body).status_code == 409
    assert a.client.post("/api/auth/register", json={**body, "username": "unique.sales"}).status_code == 201


def test_sessions_are_independent_logout_revokes_and_expiry_and_disabled_users(access):
    a = access
    one, two = a.login("sales.a"), a.login("sales.a")
    assert a.client.post("/api/auth/logout", headers=one).status_code == 200
    assert a.client.get("/api/auth/me", headers=one).status_code == 401
    assert a.client.get("/api/auth/me", headers=two).status_code == 200
    with a.app.state.store.sessions.begin() as session:
        user = session.scalar(select(Account).where(Account.username == "sales.a"))
        user.active = False
    assert a.client.get("/api/auth/me", headers=two).status_code == 401
    with a.app.state.store.sessions.begin() as session:
        for login in session.scalars(select(AccessSession)):
            login.expires_at = "2000-01-01T00:00:00+00:00"
    assert a.client.get("/api/auth/me", headers=a.actors["boss"]).status_code == 401


def test_anonymous_forged_tokens_and_nonadmin_management_denied(access):
    a = access
    assert a.client.get("/api/workspace/center").status_code == 401
    assert a.client.get("/api/auth/me", headers={"Authorization": "Bearer forged"}).status_code == 401
    for actor in ("sales.a", "manager.a", "boss"):
        assert a.client.get("/api/admin/accounts", headers=a.actors[actor]).status_code == 403
        assert a.client.post("/api/admin/invitations", headers=a.actors[actor], json={"team_id": "ta"}).status_code == 403
    assert a.client.post("/api/auth/login", json={"username": "sales.a", "password": "wrong"}).status_code == 401
    assert a.client.get("/api/meetings", headers=a.actors["boss"]).status_code == 403


def test_private_recording_invisible_to_same_team_manager_boss_and_other_sales(access):
    a = access
    row = recording(a)
    for actor in ("manager.a", "boss", "sales.a2", "sales.b", "admin"):
        assert a.client.get("/api/workspace/recordings", headers=a.actors[actor]).json() == []
        assert a.client.get(f"/api/workspace/recordings/{row['id']}", headers=a.actors[actor]).status_code == 404
        assert a.client.delete(f"/api/workspace/recordings/{row['id']}", headers=a.actors[actor]).status_code == 404
    assert a.client.get("/api/workspace/center", headers=a.actors["boss"]).json() == []


def test_center_scope_search_export_and_cross_company(access):
    a = access
    _, first = submitted(a)
    _, second = submitted(a, "sales.b", "submission-key-b")
    for actor, expected in [("boss", 2), ("manager.a", 1), ("manager.b", 1), ("sales.a", 1), ("sales.a2", 0)]:
        response = a.client.get("/api/workspace/center?q=合成", headers=a.actors[actor])
        assert len(response.json()) == expected
    for suffix in ("", "/export", "/audio"):
        assert a.client.get(f"/api/workspace/center/{first['id']}{suffix}", headers=a.actors["manager.b"]).status_code == 404
    assert a.client.get(f"/api/workspace/center/{first['id']}/export", headers=a.actors["boss"]).status_code == 200
    with a.app.state.store.sessions.begin() as session:
        outsider = Account(id="outsider", username="outside", company_id="c2", team_id=None, name="外公司老板",
            role="boss", password_hash=password_digest(PASSWORD))
        session.add(outsider)
    outside = a.login("outside")
    assert a.client.get("/api/workspace/center", headers=outside).json() == []
    assert a.client.get(f"/api/workspace/center/{second['id']}", headers=outside).status_code == 404


def test_personal_changes_deletion_leave_center_snapshot_and_idempotent_submit(access):
    a = access
    row, central = submitted(a)
    detail = a.client.get(f"/api/workspace/recordings/{row['id']}", headers=a.actors["sales.a"]).json()
    assert detail["submission"] == {"id": central["id"], "deleted": False}
    payload = {"version": 1, "customer": "合成客户／项目", "opportunity_id": a.opportunities["sales.a"], "request_key": "submission-key-a"}
    repeat = a.client.post(f"/api/workspace/recordings/{row['id']}/submit", headers=a.actors["sales.a"], json=payload)
    assert repeat.json()["id"] == central["id"]
    assert a.client.patch(f"/api/workspace/recordings/{row['id']}", headers=a.actors["sales.a"], json={
        "version": 1, "title": "个人新标题", "customer": "新客户", "meeting_date": "2026-11-01"}).status_code == 200
    assert a.client.delete(f"/api/workspace/recordings/{row['id']}", headers=a.actors["sales.a"]).status_code == 200
    output = a.client.get(f"/api/workspace/center/{central['id']}", headers=a.actors["manager.a"]).json()
    assert output["metadata"]["title"] == "合成客户需求会谈" and output["revision"] == 1


def test_center_correction_manager_only_version_conflicts_and_delete(access):
    a = access
    _, central = submitted(a)
    request = {"base_revision": 1, "title": "更正后的合成会谈", "customer": "合成客户", "meeting_date": "2026-11-10", "reason": "根据日历核对"}
    endpoint = f"/api/workspace/center/{central['id']}/corrections"
    first = a.client.post(endpoint, headers=a.actors["sales.a"], json=request).json()
    second = a.client.post(endpoint, headers=a.actors["sales.a"], json=request).json()
    for actor, status in [("sales.a", 403), ("boss", 403), ("manager.b", 404)]:
        assert a.client.post(f"/api/workspace/corrections/{first['id']}/review", headers=a.actors[actor], json={"approve": True}).status_code == status
    assert a.client.post(f"/api/workspace/corrections/{first['id']}/review", headers=a.actors["manager.a"], json={"approve": True}).status_code == 200
    assert a.client.post(f"/api/workspace/corrections/{second['id']}/review", headers=a.actors["manager.a"], json={"approve": True}).status_code == 409
    output = a.client.get(f"/api/workspace/center/{central['id']}", headers=a.actors["boss"]).json()
    assert output["revision"] == 2 and output["history"][0]["metadata"]["title"] == "合成客户需求会谈"
    requests = a.client.get("/api/workspace/corrections", headers=a.actors["manager.a"]).json()
    assert requests[0]["before"]["title"] == "合成客户需求会谈"
    for actor in ("sales.a", "boss"):
        assert a.client.post(f"/api/workspace/center/{central['id']}/delete", headers=a.actors[actor], json={"reason": "测试"}).status_code == 403
    assert a.client.post(f"/api/workspace/center/{central['id']}/delete", headers=a.actors["manager.a"], json={"reason": "合成记录撤下"}).status_code == 200
    assert a.client.get(f"/api/workspace/center/{central['id']}/export", headers=a.actors["boss"]).status_code == 404


def test_audio_permission_survives_personal_deletion(access, monkeypatch):
    import meetflow.workspace as module
    a = access
    monkeypatch.setattr(module, "audio_available", lambda _: True)
    response = a.client.post("/api/workspace/audio", headers=a.actors["sales.a"], files={"file": ("test.wav", io.BytesIO(b"test-only"), "audio/wav")})
    assert response.status_code == 201
    row = response.json()
    # Real recognition is separately exercised with downloaded audio; this test verifies access and snapshot lifetime.
    monkeypatch.setattr("meetflow.audio.ensure_transcript", lambda *_: None)
    a.app.state.driver.process(row["run_id"])
    # Add a minimal valid report because this fake audio has no transcription.
    if not a.app.state.store.latest_report(row["run_id"]):
        a.app.state.store.save_report(row["run_id"], {"mode": "demo", "summary": "测试", "claims": [], "actions": [], "risks": [], "questions": [], "challenges": []})
        a.app.state.store.update_run(row["run_id"], status="waiting_review")
    data = a.client.post(f"/api/workspace/recordings/{row['id']}/submit", headers=a.actors["sales.a"], json={"version": 1, "customer": "测试客户", "opportunity_id": a.opportunities["sales.a"], "request_key": "audio-submit-key"})
    assert data.status_code == 201, data.text
    key = data.json()["id"]
    assert a.client.get(f"/api/workspace/recordings/{row['id']}/audio", headers=a.actors["boss"]).status_code == 404
    assert a.client.get(f"/api/workspace/center/{key}/audio", headers=a.actors["manager.b"]).status_code == 404
    a.client.delete(f"/api/workspace/recordings/{row['id']}", headers=a.actors["sales.a"])
    assert a.client.get(f"/api/workspace/center/{key}/audio", headers=a.actors["manager.a"]).content == b"test-only"


def test_submission_requires_customer_and_current_version(access):
    a = access
    row = recording(a)
    path = f"/api/workspace/recordings/{row['id']}/submit"
    assert a.client.post(path, headers=a.actors["sales.a"], json={"version": 1, "customer": "   ", "request_key": "blank-customer"}).status_code == 400
    assert a.client.post(path, headers=a.actors["sales.a"], json={"version": 2, "customer": "合成客户", "request_key": "stale-version"}).status_code == 409
    with a.app.state.store.sessions() as session:
        user = session.scalar(select(Account).where(Account.username == "sales.a"))
        assert user.created_at <= now()
