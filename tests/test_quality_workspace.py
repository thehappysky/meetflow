import copy
from concurrent.futures import ThreadPoolExecutor

import pytest
from meetflow.auth import password_digest
from meetflow.database import Account, QualityCase, QualityReview, SalesAnalysis
from meetflow.domain import DomainError
from sqlalchemy import func, select
from test_access import PASSWORD, recording, submitted
from test_access import access as access  # noqa: PLC0414 -- expose the shared pytest fixture


def request(a, method, path, actor="sales.a", **kwargs):
    return getattr(a.client, method)("/api/workspace/quality" + path, headers=a.actors[actor], **kwargs)


def listing(a, actor="sales.a", scope="personal"):
    response = request(a, "get", f"/cases?scope={scope}", actor)
    assert response.status_code == 200, response.text
    return response.json()["cases"]


def detail(a, key, actor="sales.a"):
    response = request(a, "get", "/cases/" + key, actor)
    assert response.status_code == 200, response.text
    return response.json()


def finish(a, key, actor="sales.a"):
    with a.app.state.store.sessions() as session:
        active = session.get(QualityCase, key).active_analysis_id
    if active:
        a.app.state.sales_driver.process(active)
    return detail(a, key, actor)


def test_one_transcript_auto_quality_and_permissions(access):
    a = access
    rec = recording(a)
    cases = listing(a)
    assert len(cases) == 1 and cases[0]["source_id"] == rec["id"]
    case = finish(a, cases[0]["id"])
    assert case["status"] == "waiting_review" and case["mode"] == "demo"
    assert len(case["rules"]) == 5 and sum(r["weight"] for r in case["rules"]) == 100
    assert all(s["source_ref"] in a.app.state.store.source_map(rec["run_id"]) for s in case["spans"])
    assert case["report"]["metrics"]["unknown_count"] > 0
    for actor in ("manager.a", "manager.b", "boss", "sales.a2", "sales.b"):
        assert request(a, "get", "/cases/" + case["id"], actor).status_code == 404
        assert listing(a, actor, "center") == []
    assert request(a, "get", "/cases?scope=personal", "manager.a").status_code == 403
    assert a.client.get("/api/sales/template", headers=a.actors["boss"]).status_code == 403
    with a.app.state.store.sessions() as session:
        assert session.scalar(select(func.count()).select_from(QualityCase)) == 1
    assert a.app.state.quality.ensure("personal", rec["id"]) == case["id"]


def test_unknown_speakers_do_not_grade_then_context_auto_analyzes_without_upload(access):
    a = access
    with pytest.raises(DomainError, match="单段 4000"):
        a.app.state.quality.spans({"too-long": {"text": "字" * 4001}}, {})
    response = a.client.post("/api/workspace/recordings", headers=a.actors["sales.a"], json={
        "transcript": "我们先讨论目前遇到的问题。\n采购时间还不能确认。"})
    rec = response.json()
    a.app.state.driver.process(rec["run_id"])
    case = detail(a, listing(a)[0]["id"])
    assert case["status"] == "needs_speakers" and not case["report"]
    payload = {"version": case["version"], "speakers": {"s1": "sales", "s2": "customer"}}
    assert request(a, "post", f"/cases/{case['id']}/context", json={**payload, "speakers": {"s99": "sales"}}).status_code == 400
    assert request(a, "post", f"/cases/{case['id']}/context", json=payload).status_code == 200
    changed = finish(a, case["id"])
    assert changed["spans"][0]["text"] == case["spans"][0]["text"]
    assert changed["spans"][0]["source_ref"] == case["spans"][0]["source_ref"]
    assert changed["report"] and not changed["complete"]
    assert len(a.client.get("/api/workspace/recordings", headers=a.actors["sales.a"]).json()) == 1
    # Removing all identity confirmations clears the active grade; old report remains readable as history.
    payload = {"version": changed["version"], "speakers": {"s1": "unknown", "s2": "unknown"}}
    assert request(a, "post", f"/cases/{case['id']}/context", json=payload).status_code == 200
    output = detail(a, case["id"])
    assert output["status"] == "needs_speakers" and output["report"] is None
    old = request(a, "get", f"/cases/{case['id']}?analysis_id={changed['history'][0]['id']}").json()
    assert old["historical"] and old["spans"][0]["speaker"] == "sales"


def test_center_freezes_quality_background_and_survives_private_changes_and_delete(access):
    a = access
    rec, sub = submitted(a)
    private = finish(a, listing(a)[0]["id"])
    central = finish(a, listing(a, scope="center")[0]["id"])
    assert len(listing(a, "manager.a", "center")) == 1 and len(listing(a, "boss", "center")) == 1
    assert listing(a, "manager.b", "center") == [] and listing(a, "sales.a2", "center") == []
    payload = {"version": private["version"], "stage": "方案报价", "speakers": {"s1": "customer"}}
    assert request(a, "post", f"/cases/{private['id']}/context", json=payload).status_code == 200
    assert detail(a, central["id"])["stage"] == "通用复盘"
    assert request(a, "post", f"/cases/{central['id']}/context", json={"version": 1}, actor="sales.a").status_code == 403
    a.client.delete(f"/api/workspace/recordings/{rec['id']}", headers=a.actors["sales.a"])
    assert request(a, "get", f"/cases/{private['id']}").status_code == 404
    assert detail(a, central["id"], "manager.a")["report"] == central["report"]
    a.client.post(f"/api/workspace/center/{sub['id']}/delete", headers=a.actors["manager.a"], json={"reason": "撤下合成记录"})
    assert request(a, "get", f"/cases/{central['id']}", "boss").status_code == 404


def test_center_metadata_revision_creates_new_case_and_rejects_old_review(access):
    a = access
    _, sub = submitted(a)
    old = finish(a, listing(a, scope="center")[0]["id"])
    assert request(a, "post", f"/cases/{old['id']}/feedback", json={"version": old["version"],
        "report_hash": old["report_hash"], "rule_id": "needs", "reason": "来源日期核对前保留此反馈"}).status_code == 201
    change = a.client.post(f"/api/workspace/center/{sub['id']}/corrections", headers=a.actors["sales.a"], json={
        "base_revision": 1, "title": "日期核实后的会谈", "customer": "客户A", "meeting_date": "2026-11-10", "reason": "核对日历"}).json()
    response = a.client.post(f"/api/workspace/corrections/{change['id']}/review", headers=a.actors["manager.a"], json={"approve": True})
    assert response.status_code == 200
    current = listing(a, "manager.a", "center")
    assert len(current) == 1 and current[0]["source_revision"] == 2 and current[0]["id"] != old["id"]
    assert detail(a, old["id"], "manager.a")["stale"]
    assert detail(a, old["id"], "manager.a")["feedback"][0]["status"] == "superseded"
    fresh = detail(a, current[0]["id"], "manager.a")
    assert fresh["source_history"][1]["feedback_count"] == 1
    assert request(a, "post", f"/cases/{old['id']}/review", "manager.a", json={"version": old["version"], "report_hash": old["report_hash"]}).status_code == 409


def test_policy_versions_are_company_scoped_and_do_not_rewrite_old_analyses(access):
    a = access
    recording(a)
    original = finish(a, listing(a)[0]["id"])
    cards = request(a, "get", "/policy", "admin").json()["cards"]
    assert len(cards) == 5
    assert len({c["rules"][0]["criterion"] for c in cards}) == 5
    card = next(c for c in cards if c["stage"] == "通用复盘")
    needs = next(r for r in card["rules"] if r["id"] == "needs")
    assert "没有异议" not in needs["applicability"]
    payload = {key: copy.deepcopy(card[key]) for key in ("name", "rules", "product_notes")}
    payload.update(base_id=card["id"], name="本企业通用培训 v2")
    assert request(a, "post", "/policy", "manager.a", json=payload).status_code == 403
    wrong = copy.deepcopy(payload)
    wrong["rules"][0]["weight"] = 24
    assert request(a, "post", "/policy", "admin", json=wrong).status_code == 400
    assert request(a, "post", "/policy", "admin", json=payload).status_code == 201
    assert request(a, "post", "/policy", "admin", json=payload).status_code == 409
    assert detail(a, original["id"])["policy"] == original["policy"]
    assert request(a, "post", f"/cases/{original['id']}/context", json={"version": original["version"]}).status_code == 200
    new = finish(a, original["id"])
    assert new["policy"]["version"] == 2 and len(new["history"]) == 2
    old = request(a, "get", f"/cases/{original['id']}?analysis_id={original['history'][0]['id']}").json()
    assert old["policy"]["version"] == 1 and old["historical"]
    assert request(a, "get", f"/cases/{original['id']}?analysis_id=sa_not-in-this-case").status_code == 404


def test_feedback_review_authorization_idempotency_history_and_reopened_dispute(access):
    a = access
    submitted(a)
    case = finish(a, listing(a, scope="center")[0]["id"])
    payload = {"version": case["version"], "report_hash": case["report_hash"], "rule_id": "needs",
               "reason": "请主管核对需求核查这条依据", "evidence": ["s1"]}
    assert request(a, "post", f"/cases/{case['id']}/feedback", "manager.a", json=payload).status_code == 403
    assert request(a, "post", f"/cases/{case['id']}/feedback", json={**payload, "evidence": ["s999"]}).status_code == 400
    assert request(a, "post", f"/cases/{case['id']}/feedback", json=payload).status_code == 201
    case = detail(a, case["id"])
    review = {"version": case["version"], "report_hash": case["report_hash"], "note": "核对依据后保留自动判断，身份不明不扣分"}
    for actor, status in [("sales.a", 403), ("boss", 403), ("manager.b", 404)]:
        assert request(a, "post", f"/cases/{case['id']}/review", actor, json=review).status_code == status
    assert request(a, "post", f"/cases/{case['id']}/review", "manager.a", json={**review, "note": ""}).status_code == 400
    assert request(a, "post", f"/cases/{case['id']}/review", "manager.a", json=review).status_code == 200
    assert request(a, "post", f"/cases/{case['id']}/review", "manager.a", json=review).status_code == 200
    changed = detail(a, case["id"])
    assert changed["feedback"][0]["status"] == "resolved" and len(changed["review_history"]) == 1
    assert changed["report"] == case["report"]  # Raw machine judgment remains immutable.
    assert request(a, "post", f"/cases/{case['id']}/feedback", json={**payload, "version": changed["version"], "reason": "补充依据后仍希望再次核对"}).status_code == 201
    again = detail(a, case["id"])
    assert again["pending_feedback"] == 1
    assert request(a, "post", f"/cases/{case['id']}/review", "manager.a", json={**review, "version": again["version"], "note": "再次核对，保留理由并记录本次说明"}).status_code == 200
    assert len(detail(a, case["id"])["review_history"]) == 2


def test_review_rejects_unsupported_grading_and_serializes_concurrent_review(access):
    a = access
    submitted(a)
    case = finish(a, listing(a, scope="center")[0]["id"])
    payload = {"version": case["version"], "report_hash": case["report_hash"], "decisions": [
        {"rule_id": "needs", "status": "met", "reason": "", "evidence": ["s1"]}]}
    assert request(a, "post", f"/cases/{case['id']}/review", "manager.a", json=payload).status_code == 400
    payload["decisions"][0].update(reason="人工核对后确认", evidence=["s2"])
    assert request(a, "post", f"/cases/{case['id']}/review", "manager.a", json=payload).status_code == 400
    payload["decisions"][0].update(status="unmet", evidence=["s1"])
    assert request(a, "post", f"/cases/{case['id']}/review", "manager.a", json=payload).status_code == 400
    payload["decisions"][0].update(status="unknown", reason="材料不完整，保留无法判断")
    def do(_):
        return request(a, "post", f"/cases/{case['id']}/review", "manager.a", json=payload).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(do, range(2))) == [200, 200]
    with a.app.state.store.sessions() as session:
        assert session.scalar(select(func.count()).select_from(QualityReview)) == 1


def test_retry_keeps_cumulative_budget_and_is_scoped(access):
    a = access
    recording(a)
    case = listing(a)[0]
    with a.app.state.store.sessions.begin() as session:
        analysis = session.get(SalesAnalysis, session.get(QualityCase, case["id"]).active_analysis_id)
        analysis.status = "failed"
        analysis.model_calls = a.app.state.store.settings.max_model_calls
    assert request(a, "post", f"/cases/{case['id']}/retry", "manager.a").status_code == 404
    assert request(a, "post", f"/cases/{case['id']}/retry").status_code == 409


def test_quality_failure_is_explicit_and_retry_does_not_reset_budget(access):
    a = access
    recording(a)
    key = listing(a)[0]["id"]
    class FailingProvider:
        def complete(self, *_):
            raise RuntimeError("test-only upstream unavailable")
    a.app.state.sales_driver.provider = FailingProvider()
    failed = finish(a, key)
    assert failed["status"] == "failed" and failed["report"] is None
    assert "upstream unavailable" in failed["error"]
    with a.app.state.store.sessions() as session:
        analysis_id = session.get(QualityCase, key).active_analysis_id
        before = session.get(SalesAnalysis, analysis_id).model_calls
    assert request(a, "post", f"/cases/{key}/retry").status_code == 200
    a.app.state.sales_driver.provider = None
    assert finish(a, key)["status"] == "waiting_review"
    with a.app.state.store.sessions() as session:
        assert session.get(SalesAnalysis, analysis_id).model_calls > before


def test_foreign_company_cannot_read_cases_history_or_publish_borrowed_rules(access):
    a = access
    submitted(a)
    key = listing(a, scope="center")[0]["id"]
    with a.app.state.store.sessions.begin() as session:
        session.add(Account(id="external-admin", company_id="c2", username="external.admin", name="其他公司管理员",
                            role="admin", password_hash=password_digest(PASSWORD)))
    outsider = a.login("external.admin")
    assert a.client.get(f"/api/workspace/quality/cases/{key}", headers=outsider).status_code == 404
    assert a.client.get("/api/workspace/quality/cases?scope=center", headers=outsider).json()["cases"] == []
    card = request(a, "get", "/policy", "admin").json()["cards"][0]
    assert a.client.post("/api/workspace/quality/policy", headers=outsider, json={
        "base_id": card["id"], "name": card["name"], "product_notes": "", "rules": card["rules"]}).status_code == 409


def test_question_answers_create_versioned_run_without_mutating_center_or_transcript(access):
    a = access
    rec = recording(a)
    store = a.app.state.store
    body = copy.deepcopy(store.latest_report(rec["run_id"])["body"])
    body["questions"] = [{"key": "date", "text": "实际会谈日期是什么？", "evidence": [], "affected_actions": [], "options": []}]
    store.save_report(rec["run_id"], body)
    submitted = a.client.post(f"/api/workspace/recordings/{rec['id']}/submit", headers=a.actors["sales.a"], json={
        "version": 1, "customer": "合成客户", "request_key": "clarify-test-submit"}).json()
    original = store.source_map(rec["run_id"])
    path = f"/api/workspace/recordings/{rec['id']}/clarifications"
    assert a.client.post(path, headers=a.actors["manager.a"], json={"version": 1, "answers": {"date": "11月10日"}}).status_code == 404
    assert a.client.post(path, headers=a.actors["sales.a"], json={"version": 1, "answers": {"foreign": "伪造其他问题"}}).status_code == 400
    response = a.client.post(path, headers=a.actors["sales.a"], json={"version": 1, "answers": {"date": "核对后为11月10日"}})
    assert response.status_code == 201, response.text
    assert response.json()["version"] == 2 and response.json()["run_id"] != rec["run_id"]
    assert store.source_map(rec["run_id"]) == original
    central = a.client.get(f"/api/workspace/center/{submitted['id']}", headers=a.actors["manager.a"]).json()
    assert central["clarifications"] == []
    run = store.get_run(response.json()["run_id"])
    assert any(s["title"] == "销售补充（未经客户确认）" for s in run["manifest"]["sources"])
    assert a.client.post(path, headers=a.actors["sales.a"], json={"version": 1, "answers": {"date": "网络重试"}}).status_code == 409
