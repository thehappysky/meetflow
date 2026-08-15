
import pytest
from fastapi.testclient import TestClient
from meetflow.api import create_app
from meetflow.config import Settings
from meetflow.domain import DomainError
from meetflow.orchestration import validate_plan
from meetflow.providers import DemoProvider


@pytest.fixture
def application(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, start_worker=False))
    with TestClient(app) as client:
        yield app, client


def analyzed(app, client):
    response = client.post("/api/demo")
    assert response.status_code == 201
    run_id = response.json()["run"]["id"]
    app.state.driver.process(run_id)
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["run"]["status"] == "waiting_review", detail["run"]["error"]
    return run_id, detail


def approval_request(detail, **changes):
    report = detail["report"]
    return {"revision": report["revision"], "report_hash": report["content_hash"],
            "selected_actions": [item["id"] for item in report["body"]["actions"]],
            "edits": {}, "answers": {}, "idempotency_key": "approval-test-001", **changes}


def test_end_to_end_demo_review_tasks_and_checkpoint_resume(application):
    app, client = application
    run_id, detail = analyzed(app, client)
    assert {item["role"] for item in detail["artifacts"]} == {
        "coordinator", "meeting", "context", "planner", "risk", "auditor"}
    assert detail["report"]["body"]["mode"] == "demo"
    assert detail["report"]["body"]["questions"]
    payload = approval_request(detail, answers={"owner:老王": "王宁"})
    result = client.post(f"/api/runs/{run_id}/review", json=payload)
    assert result.status_code == 200, result.text
    duplicate = client.post(f"/api/runs/{run_id}/review", json=payload)
    assert duplicate.json()["id"] == result.json()["id"]
    app.state.driver.process(run_id, {"approval_id": result.json()["id"]})
    assert client.get(f"/api/runs/{run_id}").json()["run"]["status"] == "completed"
    task_ids = [item["task_id"] for item in result.json()["body"]["applied"]]
    tasks = client.get("/api/tasks", params={"project_id": detail["run"]["project_id"]}).json()
    assert len(task_ids) == len(set(task_ids))
    assert all(any(task["id"] == key for task in tasks) for key in task_ids)
    exported = client.get(f"/api/runs/{run_id}/export")
    assert exported.status_code == 200 and "离线规则演示" in exported.text
    assert "王宁" in exported.text and "已确认任务变更" in exported.text and "原文依据" in exported.text
    events = client.get(f"/api/runs/{run_id}/event-log").json()
    assert any(event["kind"] == "tool" for event in events)
    assert client.get("/api/audit").json()[0]["action"] == "review.approved"


def test_stale_report_approval_does_not_write_tasks(application):
    app, client = application
    run_id, detail = analyzed(app, client)
    before = client.get("/api/tasks").json()
    response = client.post(f"/api/runs/{run_id}/review", json=approval_request(detail, report_hash="old"))
    assert response.status_code == 409
    assert client.get("/api/tasks").json() == before


def test_stale_target_version_rolls_back_entire_batch(application):
    app, client = application
    run_id, detail = analyzed(app, client)
    target = next(item for item in detail["report"]["body"]["actions"] if item["target_task_id"])
    assert client.patch(f"/api/tasks/{target['target_task_id']}",
        json={"version": target["expected_version"], "status": "in_progress"}).status_code == 200
    before = client.get("/api/tasks").json()
    response = client.post(f"/api/runs/{run_id}/review", json=approval_request(detail))
    assert response.status_code == 409
    assert client.get("/api/tasks").json() == before


def test_idempotency_key_cannot_be_reused_for_different_payload(application):
    app, client = application
    run_id, detail = analyzed(app, client)
    payload = approval_request(detail, selected_actions=[])
    assert client.post(f"/api/runs/{run_id}/review", json=payload).status_code == 200
    payload["answers"] = {"owner:老王": "其他人"}
    assert client.post(f"/api/runs/{run_id}/review", json=payload).status_code == 409


def test_plain_text_without_project_context_can_complete(application):
    app, client = application
    project = client.get("/api/projects").json()[0]
    response = client.post("/api/meetings", json={"project_id": project["id"],
        "transcript": "小李：我来准备测试用例，下周三交付。", "mode": "demo"})
    run_id = response.json()["run"]["id"]
    app.state.driver.process(run_id)
    detail = client.get(f"/api/runs/{run_id}").json()
    assert detail["run"]["status"] == "waiting_review"
    assert not any(item["role"] == "context" for item in detail["artifacts"])
    assert all(item["due"] is None for item in detail["report"]["body"]["actions"])


def test_restart_graph_and_checkpoint_waiting_review(tmp_path):
    settings = Settings(data_dir=tmp_path, start_worker=False)
    app1 = create_app(settings)
    with TestClient(app1) as client:
        run_id, detail = analyzed(app1, client)
    app2 = create_app(settings)
    with TestClient(app2) as client:
        response = client.post(f"/api/runs/{run_id}/review", json=approval_request(detail, selected_actions=[]))
        assert response.status_code == 200
        app2.state.driver.process(run_id, {"approval_id": response.json()["id"]})
        assert client.get(f"/api/runs/{run_id}").json()["run"]["status"] == "completed"


def test_plan_rejects_cycles_and_unaudited_work():
    with pytest.raises(DomainError, match="依赖环"):
        validate_plan([{"id": "a", "role": "planner", "goal": "x", "depends_on": ["a"]}])
    with pytest.raises(DomainError, match="全部"):
        validate_plan([{"id": "a", "role": "planner", "goal": "x", "depends_on": []},
                       {"id": "b", "role": "auditor", "goal": "x", "depends_on": []}])


def test_failure_does_not_fabricate_report_and_can_resume(tmp_path):
    class FailOnce(DemoProvider):
        failed = False

        def complete(self, role, messages, tools, info):
            if role == "planner" and not self.failed:
                self.failed = True
                raise DomainError("模拟暂时不可用", 502)
            return super().complete(role, messages, tools, info)

    app = create_app(Settings(data_dir=tmp_path, start_worker=False), provider=FailOnce())
    with TestClient(app) as client:
        run_id = client.post("/api/demo").json()["run"]["id"]
        app.state.driver.process(run_id)
        failed = client.get(f"/api/runs/{run_id}").json()
        assert failed["run"]["status"] == "failed" and failed["report"] is None
        assert client.post(f"/api/runs/{run_id}/retry").status_code == 200
        app.state.driver.process(run_id)
        recovered = client.get(f"/api/runs/{run_id}").json()
        assert recovered["run"]["status"] == "waiting_review", recovered["run"]["error"]
        assert len([item for item in recovered["artifacts"] if item["role"] == "meeting"]) == 1


def test_counterevidence_triggers_only_planner_and_auditor_rework(tmp_path):
    class FaultInjectedDemo(DemoProvider):
        plans = 0

        def analyze(self, role, spans, tasks, dates, upstream):
            result = super().analyze(role, spans, tasks, dates, upstream)
            if role == "planner":
                self.plans += 1
                if self.plans == 1:
                    source = next(span for span in spans if "只是候选" in span["text"])
                    result["actions"].append({"id": "bad-window", "title": "发布", "due": "2026-10-30",
                        "evidence": [source["id"]], "conditions": [], "status": "todo"})
            return result

    app = create_app(Settings(data_dir=tmp_path, start_worker=False), provider=FaultInjectedDemo())
    with TestClient(app) as client:
        _, detail = analyzed(app, client)
        roles = [item["role"] for item in detail["artifacts"]]
        assert roles.count("planner") == 2 and roles.count("auditor") == 2
        assert roles.count("meeting") == 1 and roles.count("context") == 1
        assert all(action["id"] != "bad-window" for action in detail["report"]["body"]["actions"])
