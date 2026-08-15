from datetime import datetime

from meetflow.database import Submission
from test_access import access as access  # noqa: PLC0414 -- shared fixture
from test_access import recording


def call(a, method, path, actor="sales.a", **kwargs):
    return getattr(a.client, method)("/api/workspace/business" + path, headers=a.actors[actor], **kwargs)


def custom_record(a, text="销售：我负责下周五准备演示，需要主管协助协调技术支持。", day="2026-11-10"):
    response = a.client.post("/api/workspace/recordings", headers=a.actors["sales.a"], json={
        "title": "客户需求会谈（合成）", "opportunity_id": a.opportunities["sales.a"],
        "meeting_date": day, "transcript": text})
    assert response.status_code == 201, response.text
    row = response.json()
    a.app.state.driver.process(row["run_id"])
    return row


def publish(a, row, key="business-submit-key"):
    response = a.client.post(f"/api/workspace/recordings/{row['id']}/submit", headers=a.actors["sales.a"],
        json={"version": row["version"], "opportunity_id": a.opportunities["sales.a"], "request_key": key})
    assert response.status_code == 201, response.text
    return response.json()


def test_customer_identity_privacy_and_submission_requires_stable_link(access):
    a = access
    assert len(call(a, "get", "/opportunities").json()) == 1
    assert call(a, "get", "/opportunities", "manager.a").json() == []
    original = call(a, "get", "/opportunities").json()[0]
    duplicate = call(a, "post", "/opportunities", json={"customer_name": original["customer"], "title": original["title"]}).json()
    assert duplicate["customer_id"] != original["customer_id"]
    assert call(a, "post", "/opportunities", "sales.b", json={"customer_id": original["customer_id"], "title": "其他项目"}).status_code == 404
    row = recording(a)
    assert a.client.post(f"/api/workspace/recordings/{row['id']}/submit", headers=a.actors["sales.a"],
        json={"version": 1, "customer": "文字不等于选择商机", "request_key": "missing-opportunity"}).status_code == 400
    assert a.client.post(f"/api/workspace/recordings/{row['id']}/submit", headers=a.actors["sales.a"],
        json={"version": 1, "opportunity_id": a.opportunities["sales.b"], "request_key": "foreign-opportunity"}).status_code == 404


def test_cross_meeting_timeline_and_role_scope(access):
    a = access
    first = custom_record(a, day="2026-11-09")
    second = custom_record(a, "销售：我负责准备报价。\n客户：报价范围还要确认。", day="2026-11-10")
    publish(a, first)
    publish(a, second, "second-business-submit")
    key = a.opportunities["sales.a"]
    result = call(a, "get", f"/opportunities/{key}", "manager.a").json()
    assert len(result["timeline"]) == 2 and result["timeline"][0]["metadata"]["meeting_date"] == "2026-11-10"
    assert result["timeline"][0]["new_records"] and len(result["stage_sources"]) == 2
    personal = call(a, "get", f"/opportunities/{key}").json()
    assert len(personal["timeline"]) == 2 and len(personal["stage_sources"]) == 2
    assert call(a, "get", f"/opportunities/{key}", "manager.b").status_code == 404
    assert call(a, "get", f"/opportunities/{key}", "sales.a2").status_code == 404
    assert len(call(a, "get", "/opportunities", "boss").json()) == 1


def test_stage_confirmation_requires_visible_submitted_evidence_and_version(access):
    a = access
    row = custom_record(a)
    key = a.opportunities["sales.a"]
    payload = {"version": 1, "stage": "产品演示", "source_id": row["id"], "reason": "客户确认下一次演示安排"}
    assert call(a, "post", f"/opportunities/{key}/stage", json=payload).status_code == 404
    source = publish(a, row)
    own = call(a, "get", "/followups?scope=mine").json()
    assert len(own) == 1 and own[0]["source_kind"] == "center"
    payload["source_id"] = source["id"]
    assert call(a, "post", f"/opportunities/{key}/stage", "boss", json=payload).status_code == 403
    assert call(a, "post", f"/opportunities/{key}/stage", "manager.a", json=payload).status_code == 200
    assert call(a, "post", f"/opportunities/{key}/stage", json=payload).status_code == 409
    result = call(a, "get", f"/opportunities/{key}").json()
    assert result["stage"] == "产品演示" and result["history"][0]["source_revision"] == 1
    second = custom_record(a, "销售：我负责补充第二场需求材料。")
    publish(a, second, "second-stage-evidence")
    a.client.post(f"/api/workspace/center/{source['id']}/delete", headers=a.actors["manager.a"], json={"reason":"撤下第一场合成证据"})
    result = call(a, "get", f"/opportunities/{key}", "manager.a").json()
    assert result["stage_basis"] == "依据来源待核对"
    assert "暂不展示" in result["history"][0]["reason"]


def test_followup_feedback_carries_across_date_revision_and_remains_scoped(access):
    a = access
    row = custom_record(a)
    private = call(a, "get", "/followups?scope=personal").json()[0]
    assert private["body"]["due"] == "2026-11-20"
    payload = {"version": 1, "status": "done", "note": "销售确认已将资料发给客户", "responsible": "销售甲"}
    assert call(a, "patch", f"/followups/{private['id']}", "boss", json=payload).status_code == 403
    assert call(a, "patch", f"/followups/{private['id']}", json=payload).status_code == 200
    patch = a.client.patch(f"/api/workspace/recordings/{row['id']}", headers=a.actors["sales.a"], json={
        "version": 1, "title": row["body"]["title"], "customer": "", "meeting_date": "2026-11-17"})
    assert patch.status_code == 200, patch.text
    current = a.client.get(f"/api/workspace/recordings/{row['id']}", headers=a.actors["sales.a"]).json()
    a.app.state.driver.process(current["run_id"])
    followups = call(a, "get", "/followups?scope=personal").json()
    assert len(followups) == 1 and followups[0]["body"]["status"] == "done"
    assert followups[0]["body"]["due"] == "2026-11-27"
    assert followups[0]["body"]["responsible"] == "销售甲"
    assert followups[0]["history"]
    assert call(a, "patch", f"/followups/{private['id']}", json={**payload,"version":2}).status_code == 409
    assert call(a, "get", "/followups?scope=personal", "manager.a").status_code == 403


def test_center_date_correction_recalculates_relative_date_without_rewriting_history(access):
    a = access
    row = custom_record(a)
    source = publish(a, row)
    endpoint = f"/api/workspace/center/{source['id']}"
    original = a.client.get(endpoint, headers=a.actors["sales.a"]).json()
    assert original["report"]["actions"][0]["due"] == "2026-11-20"
    request = a.client.post(endpoint + "/corrections", headers=a.actors["sales.a"], json={
        "base_revision": 1, "title": "更正合成会谈日期", "customer": "合成客户", "meeting_date": "2026-11-17", "reason": "核对合成场景日期"}).json()
    assert a.client.post(f"/api/workspace/corrections/{request['id']}/review", headers=a.actors["manager.a"], json={"approve":True}).status_code == 200
    new = a.client.get(endpoint, headers=a.actors["sales.a"]).json()
    assert new["report"]["actions"][0]["due"] == "2026-11-27"
    with a.app.state.store.sessions() as session:
        assert session.get(Submission, source["id"]).history[0]["snapshot"]["report"]["actions"][0]["due"] == "2026-11-20"
    assert call(a, "get", "/followups?scope=center", "manager.a").json()[0]["body"]["due"] == "2026-11-27"


def test_dashboard_never_uses_upload_date_as_occurrence_or_reads_private_sources(access, monkeypatch):
    import meetflow.business as module
    a = access
    row = custom_record(a, "销售：主管需要协助协调公司资源，我负责准备资料。", day="")
    assert call(a, "get", "/dashboard", "manager.a").json()["sources"] == []
    source = publish(a, row)
    with a.app.state.store.sessions.begin() as session:
        session.get(Submission, source["id"]).created_at = "2026-11-10T16:30:00+00:00"
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026,11,11,12,tzinfo=tz)
    monkeypatch.setattr(module, "datetime", Clock)
    result = call(a, "get", "/dashboard", "manager.a").json()
    assert result["received"] == 1 and result["occurred_today"] == 0 and result["unknown_dates"] == 1
    assert result["suggestions"]
    assert call(a, "get", "/dashboard", "manager.b").json()["sources"] == []
    assert call(a, "get", "/dashboard?days=2", "manager.a").status_code == 400


def test_explicit_followup_revision_only_links_same_visible_opportunity(access):
    a = access
    first = custom_record(a)
    second = custom_record(a,"销售：我负责补充演示资料。")
    publish(a, first)
    publish(a, second,"followup-second-key")
    items = call(a, "get", "/followups?scope=center").json()
    old = next(f for f in items if f["source_id"] != items[0]["source_id"])
    current = items[0]
    response = call(a,"patch",f"/followups/{current['id']}",json={"version":1,"status":"in_progress",
        "note":"本次新约定替代上一场准备事项", "supersedes_id":old["id"]})
    assert response.status_code==200,response.text
    items=call(a,"get","/followups?scope=center").json()
    assert next(f for f in items if f["id"]==old["id"])["body"]["status"]=="paused"
