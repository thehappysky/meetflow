import json

import pytest
from fastapi.testclient import TestClient
from meetflow.api import create_app
from meetflow.config import Settings
from meetflow.domain import DomainError
from meetflow.sales_domain import score
from meetflow.sales_workflow import SalesDemoProvider, validate_sales_result


@pytest.fixture
def app_client(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, start_worker=False))
    with TestClient(app) as client:
        project_id = client.get("/api/projects").json()[0]["id"]
        yield app, client, project_id


def analyze_demo(app, client, project_id):
    response = client.post("/api/sales/demo", params={"project_id": project_id})
    assert response.status_code == 201, response.text
    key = response.json()["analysis"]["id"]
    app.state.sales_driver.process(key)
    detail = client.get(f"/api/sales/analyses/{key}").json()
    assert detail["analysis"]["status"] == "waiting_review", detail
    return key, detail


def review_input(detail, **changes):
    row = detail["analysis"]
    return {"report_hash": row["report_hash"], "conversation_version": row["snapshot"]["conversation"]["version"],
            "idempotency_key": "review-" + row["id"], **changes}


def test_sales_full_flow_evidence_review_tasks_dashboard_and_export(app_client):
    app, client, project = app_client
    key, detail = analyze_demo(app, client, project)
    row = detail["analysis"]
    assert set(row["artifacts"]) == {"0:discovery", "0:process", "0:product", "0:auditor"}
    assert row["report"]["metrics"] == {"score": 50.0, "coverage": 70.0, "earned_weight": 35.0,
                                         "evaluated_weight": 70, "applicable_weight": 100,
                                         "unknown_count": 1, "rule_count": 3}
    dashboard = "/api/sales/dashboard"
    params = {"project_id": project, "mode": "demo"}
    assert client.get(dashboard, params=params).json()["reviewed_conversations"] == 0
    payload = review_input(detail, followup_rule_ids=["next"], note="核对下一步时间")
    first = client.post(f"/api/sales/analyses/{key}/review", json=payload)
    assert first.status_code == 200, first.text
    assert client.post(f"/api/sales/analyses/{key}/review", json=payload).json()["id"] == first.json()["id"]
    assert len(client.get("/api/tasks", params={"project_id": project}).json()) == 1
    result = client.get(dashboard, params=params).json()
    assert result["reviewed_conversations"] == 1 and result["people"][0]["samples"] == 1
    assert result["topics"] == [{"topic": "price", "conversations": 1}]
    assert client.get(dashboard, params={"project_id": project}).json()["reviewed_conversations"] == 0
    exported = client.get(f"/api/sales/analyses/{key}/export")
    assert "主管已复核" in exported.text and "规则演示" in exported.text and "原文依据" in exported.text
    assert any(item["action"] == "sales.review_saved" for item in client.get("/api/audit").json())


def test_score_unknown_and_not_applicable_have_explicit_denominators():
    rules = [{"id": "a", "weight": 20}, {"id": "b", "weight": 50}, {"id": "c", "weight": 30}]
    result = score(rules, [{"rule_id": "a", "status": "met"}, {"rule_id": "b", "status": "unknown"},
                           {"rule_id": "c", "status": "not_applicable"}])
    assert result["score"] == 100 and result["coverage"] == 28.6
    assert score(rules[:1], [{"rule_id": "a", "status": "unknown"}])["score"] is None
    with pytest.raises(DomainError):
        score(rules, [{"rule_id": "a", "status": "met"}])


def test_versions_are_immutable_and_stage_project_isolation(app_client):
    app, client, project = app_client
    _, detail = analyze_demo(app, client, project)
    snapshot = detail["analysis"]["snapshot"]
    old = snapshot["playbook"]
    value = {**old["body"], "project_id": project, "base_id": old["id"], "name": "修订版"}
    value["rules"][0]["weight"] = 80
    newer = client.post("/api/sales/playbooks", json=value)
    assert newer.status_code == 201 and newer.json()["version"] == 2
    assert app.state.sales.get(detail["analysis"]["id"])["snapshot"]["playbook"]["body"]["rules"][0]["weight"] == 30
    assert client.post("/api/sales/playbooks", json=value).status_code == 409
    other = client.post("/api/projects", json={"name": "另一团队"}).json()["id"]
    assert client.post("/api/sales/playbooks", json={**value, "project_id": other}).status_code == 404
    conversation = client.post("/api/sales/conversations", json={"project_id": other, "title": "报价", "stage": "报价",
        "salesperson": "甲", "customer": "乙", "transcript": "销售：报价已发送。"}).json()
    assert client.post(f"/api/sales/conversations/{conversation['id']}/analyze",
        json={"playbook_id": old["id"]}).status_code == 404
    conversation = client.post("/api/sales/conversations", json={"project_id": project, "title": "报价", "stage": "报价",
        "salesperson": "甲", "customer": "乙", "transcript": "销售：报价已发送。"}).json()
    assert client.post(f"/api/sales/conversations/{conversation['id']}/analyze",
        json={"playbook_id": old["id"]}).status_code == 400


def test_review_requires_reason_stale_binding_and_idempotency(app_client):
    app, client, project = app_client
    key, detail = analyze_demo(app, client, project)
    endpoint = f"/api/sales/analyses/{key}/review"
    assert client.post(endpoint, json=review_input(detail, decisions=[{"rule_id": "next", "status": "met"}])).status_code == 400
    assert client.post(endpoint, json=review_input(detail, report_hash="old")).status_code == 409
    payload = review_input(detail, decisions=[{"rule_id": "next", "status": "met", "reason": "主管补充确认"}])
    result = client.post(endpoint, json=payload)
    assert result.status_code == 200
    assert result.json()["body"]["metrics"]["score"] == 78.6
    assert client.post(endpoint, json={**payload, "note": "different"}).status_code == 409
    assert client.post(endpoint, json={**payload, "idempotency_key": "another-key-000"}).status_code == 409


def test_transcript_revision_invalidates_pending_review(app_client):
    app, client, project = app_client
    key, detail = analyze_demo(app, client, project)
    conversation = detail["analysis"]["snapshot"]["conversation"]
    result = client.patch(f"/api/sales/conversations/{conversation['id']}/spans", json={
        "version": conversation["version"], "spans": conversation["body"]["spans"], "complete": False})
    assert result.status_code == 200
    assert client.get(f"/api/sales/analyses/{key}").json()["stale"]
    response = client.post(f"/api/sales/analyses/{key}/review", json=review_input(detail, followup_rule_ids=["next"]))
    assert response.status_code == 409
    assert client.get("/api/tasks").json() == []


def test_unknown_speakers_and_incomplete_text_do_not_become_negative_scores(app_client):
    app, client, project = app_client
    _, detail = analyze_demo(app, client, project)
    snapshot = detail["analysis"]["snapshot"]
    snapshot["conversation"]["body"]["complete"] = False
    value = {"decisions": [{"rule_id": "next", "status": "unmet", "reason": "没看到", "evidence": []}]}
    assert validate_sales_result(value, "process", snapshot)["decisions"][0]["status"] == "unknown"
    snapshot['conversation']['body']['complete'] = True
    normalized = validate_sales_result(value, 'process', snapshot)['decisions'][0]
    assert normalized['status'] == 'unknown' and normalized['reason_title'] == '依据不足，暂不评价'
    snapshot['conversation']['body']['complete'] = False
    value["decisions"][0].update(status="met", evidence=["s2"])
    with pytest.raises(DomainError, match="销售身份"):
        validate_sales_result(value, "process", snapshot)
    value["decisions"][0]["evidence"] = ["other-project-span"]
    with pytest.raises(DomainError, match="冻结会话"):
        validate_sales_result(value, "process", snapshot)


def test_directed_repair_is_bounded_and_persistent_challenge_becomes_unknown(app_client):
    app, client, project = app_client

    class ChallengingProvider(SalesDemoProvider):
        def complete(self, role, messages, tools, info):
            message, usage = super().complete(role, messages, tools, info)
            call = message["tool_calls"][0]
            if role == "auditor" and call["function"]["name"] == "submit_result":
                call["function"]["arguments"] = json.dumps({"challenges": [
                    {"rule_id": "next", "reason": "客户未确认时间", "evidence": ["s6"]}]})
            return message, usage

    app.state.sales_driver.provider = ChallengingProvider()
    _, detail = analyze_demo(app, client, project)
    artifacts = detail["analysis"]["artifacts"]
    assert "1:process" in artifacts and "1:auditor" in artifacts
    assert "1:discovery" not in artifacts and "1:product" not in artifacts
    assert next(item for item in detail["analysis"]["report"]["decisions"] if item["rule_id"] == "next")["status"] == "unknown"


def test_failed_provider_never_fakes_success_and_checkpoint_retry_reuses_artifacts(app_client):
    app, client, project = app_client

    class FailsOnce(SalesDemoProvider):
        failed = False

        def complete(self, role, *args):
            if role == "auditor" and not self.failed:
                self.failed = True
                raise DomainError("模型服务临时失败", 502)
            return super().complete(role, *args)

    app.state.sales_driver.provider = FailsOnce()
    result = client.post("/api/sales/demo", params={"project_id": project}).json()
    key = result["analysis"]["id"]
    app.state.sales_driver.process(key)
    failed = app.state.sales.get(key)
    assert failed["status"] == "failed" and failed["report"] is None
    assert len(failed["artifacts"]) == 3
    assert client.post(f"/api/sales/analyses/{key}/retry").status_code == 200
    app.state.sales_driver.process(key)
    recovered = app.state.sales.get(key)
    assert recovered["status"] == "waiting_review"
    assert recovered["model_calls"] - failed["model_calls"] == 3


def test_budget_is_persisted_cancel_and_unconfigured_live_audio(app_client):
    app, client, project = app_client
    result = client.post("/api/sales/demo", params={"project_id": project}).json()
    key, conversation = result["analysis"]["id"], result["conversation"]["id"]
    app.state.sales.settings.max_model_calls = 2
    app.state.sales_driver.process(key)
    assert app.state.sales.get(key)["model_calls"] == 2
    assert app.state.sales.get(key)["status"] == "failed"
    assert client.post(f"/api/sales/analyses/{key}/retry").status_code == 409
    assert client.post(f"/api/sales/conversations/{conversation}/analyze",
        json={"playbook_id": result["playbook"]["id"], "mode": "live"}).status_code == 422
    assert client.post("/api/sales/audio", files={"file": ("test.wav", b"notreal", "audio/wav")},
                       data={"metadata": "{}"}).status_code == 422


def test_dashboard_deduplicates_reanalysis_and_filters_dates(app_client):
    app, client, project = app_client
    key, detail = analyze_demo(app, client, project)
    assert client.post(f"/api/sales/analyses/{key}/review", json=review_input(detail)).status_code == 200
    original = detail["analysis"]
    row = client.post(f"/api/sales/conversations/{original['conversation_id']}/analyze",
        json={"playbook_id": original["playbook_id"]}).json()
    app.state.sales_driver.process(row["id"])
    detail = client.get(f"/api/sales/analyses/{row['id']}").json()
    client.post(f"/api/sales/analyses/{row['id']}/review", json=review_input(detail))
    params = {"project_id": project, "mode": "demo"}
    assert client.get("/api/sales/dashboard", params=params).json()["reviewed_conversations"] == 1
    assert client.get("/api/sales/dashboard", params={**params, "start": "2026-01-01"}).json()["reviewed_conversations"] == 0
    assert client.get("/api/sales/dashboard", params={**params, "start": "invalid"}).status_code == 422
    assert client.get("/api/sales/dashboard", params={**params, "salesperson": "其他人"}).json()["reviewed_conversations"] == 0


def test_audio_import_requires_identity_confirmation_and_preserves_timed_words(app_client, monkeypatch):
    from types import SimpleNamespace

    from meetflow import audio, sales_api

    app, client, project = app_client
    monkeypatch.setattr(audio, "audio_available", lambda _: True)
    monkeypatch.setattr(sales_api, "audio_available", lambda _: True)
    fake_model = SimpleNamespace(transcribe=lambda *a, **k: ([
        SimpleNamespace(text="您目前怎么处理？", start=0.5, end=3),
        SimpleNamespace(text="价格比较贵。", start=3.5, end=6)], SimpleNamespace(language="zh")))
    monkeypatch.setattr(audio, "load_model", lambda *a: fake_model)
    metadata = {"project_id": project, "title": "测试音频适配器", "salesperson": "甲", "customer": "乙", "stage": "需求访谈"}
    result = client.post("/api/sales/audio", files={"file": ("test.wav", b"fake-audio", "audio/wav")},
                         data={"metadata": json.dumps(metadata)}).json()
    assert "audio_path" not in result and result["status"] == "transcribing"
    app.state.sales_driver.transcribe(result["id"])
    conversation = client.get(f"/api/sales/conversations/{result['id']}").json()
    assert conversation["status"] == "needs_speakers" and conversation["body"]["spans"][0]["start"] == 0.5
    assert client.get(f"/api/sales/conversations/{result['id']}/audio").content == b"fake-audio"
    card = client.post("/api/sales/playbooks", json={"project_id": project,
        **client.get("/api/sales/template").json()}).json()
    endpoint = f"/api/sales/conversations/{result['id']}"
    assert client.post(endpoint + "/analyze", json={"playbook_id": card["id"]}).status_code == 409
    spans = conversation["body"]["spans"]
    spans[0]["speaker"], spans[1]["speaker"] = "sales", "customer"
    payload = {"version": conversation["version"], "spans": spans, "complete": True}
    bad = json.loads(json.dumps(payload))
    bad["spans"][0]["text"] = "改写原文"
    assert client.patch(endpoint + "/spans", json=bad).status_code == 400
    assert client.patch(endpoint + "/spans", json=payload).status_code == 200
    analysis = client.post(endpoint + "/analyze", json={"playbook_id": card["id"]}).json()
    app.state.sales_driver.process(analysis["id"])
    assert app.state.sales.get(analysis["id"])["status"] == "waiting_review"


def test_checkpoint_reopens_in_new_app_and_unknown_product_has_no_positive_score(app_client):
    app, client, project = app_client
    key, detail = analyze_demo(app, client, project)
    snapshot = detail["analysis"]["snapshot"]
    result = validate_sales_result({"decisions": [{"rule_id": "promise", "status": "met",
        "reason": "模型假定支持", "evidence": ["s1"]}]}, "product", snapshot)
    assert result["decisions"][0]["status"] == "unknown"
    other = create_app(Settings(data_dir=app.state.sales.settings.data_dir, start_worker=False))
    with TestClient(other) as second:
        assert second.get(f"/api/sales/analyses/{key}").json()["analysis"]["report_hash"] == detail["analysis"]["report_hash"]
        response = second.post(f"/api/sales/analyses/{key}/review", json=review_input(detail))
        assert response.status_code == 200
        assert second.get("/api/sales/dashboard", params={"project_id": project, "mode": "demo"}).json()["reviewed_conversations"] == 1


def test_live_sales_uses_http_tool_protocol_without_demo_fallback(app_client):
    import httpx
    from meetflow.providers import LiveProvider
    from meetflow.sales_workflow import PROMPTS

    app, client, project = app_client
    settings = app.state.sales.settings
    settings.model_api_key, settings.model_name = "test-only-key", "mock-tools"
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        role = next(role for role, prompt in PROMPTS.items() if payload["messages"][0]["content"].startswith(prompt))
        tool_replies = [m for m in payload["messages"] if m["role"] == "tool"]
        call_ids = {call["id"] for m in payload["messages"] for call in m.get("tool_calls", [])}
        assert all(reply["tool_call_id"] in call_ids for reply in tool_replies)
        message, _ = SalesDemoProvider().complete(role, payload["messages"], payload["tools"], {})
        if role == 'auditor' and any(call['function']['name'] == 'submit_result' for call in message.get('tool_calls', [])):
            upstream = json.loads(next(m['content'] for m in tool_replies if m.get('name') == 'read_upstream'))
            decisions = [item for key, result in upstream.items() if not key.endswith(':auditor')
                         for item in result.get('decisions', [])]
            message['tool_calls'][0]['function']['arguments'] = json.dumps({'checks': [
                {'rule_id': d['rule_id'], 'suggested_status': d['status'],
                 'reason': '测试适配器核对上游判断', 'evidence': d['evidence']}
                for d in decisions]})
        return httpx.Response(200, json={"choices": [{"message": message}],
                                       "usage": {"prompt_tokens": 100, "completion_tokens": 20}})

    app.state.sales_driver.provider = LiveProvider(settings, transport=httpx.MockTransport(handler))
    demo = client.post("/api/sales/demo", params={"project_id": project}).json()
    app.state.sales.change(demo["analysis"]["id"], status="cancelled")
    row = client.post(f"/api/sales/conversations/{demo['conversation']['id']}/analyze",
                     json={"playbook_id": demo["playbook"]["id"], "mode": "live"}).json()
    app.state.sales_driver.process(row["id"])
    final = app.state.sales.get(row["id"])
    assert final["status"] == "waiting_review" and final["mode"] == "live"
    assert final["input_tokens"] == len(requests) * 100
    assert {key.split(":")[1] for key in final["artifacts"]} == set(PROMPTS)


def test_cancelled_sales_never_publishes_report(app_client):
    app, client, project = app_client
    demo = client.post("/api/sales/demo", params={"project_id": project}).json()
    key = demo["analysis"]["id"]
    assert client.post(f"/api/sales/analyses/{key}/cancel").status_code == 200
    app.state.sales_driver.process(key)
    assert app.state.sales.get(key)["status"] == "cancelled"
    assert app.state.sales.get(key)["report"] is None
