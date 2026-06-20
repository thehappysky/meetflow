import pytest

from meetflow.config import Settings
from meetflow.database import Source, make_database
from meetflow.domain import DomainError, MeetingInput, SourceInput
from meetflow.store import Store
from meetflow.tools import ToolGateway, normalize_date


@pytest.fixture
def store(tmp_path):
    settings = Settings(data_dir=tmp_path, start_worker=False, max_model_calls=2)
    engine, sessions = make_database(settings)
    yield Store(sessions, settings)
    engine.dispose()


def make_run(store, **overrides):
    project = store.create_project("测试项目")
    value = {"project_id": project["id"], "transcript": "李明：下周三交付。", **overrides}
    return store.create_meeting(MeetingInput(**value))["run"]


def test_relative_date_uses_meeting_time_and_unknown_is_preserved():
    assert normalize_date("下周三", "2026-10-23T15:00:00+08:00", "Asia/Shanghai")["date"] == "2026-10-28"
    assert normalize_date("下周三", None, "Asia/Shanghai")["date"] is None


def test_tool_cannot_read_another_project_or_future_source(store):
    run = make_run(store)
    other = store.create_project("另一个项目")
    with store.sessions.begin() as session:
        source = store.add_source(session, other["id"], SourceInput(title="私有资料", kind="history", text="秘密"))
        source_id = source.id
    gateway = ToolGateway(store, run["id"], "context", {})
    with pytest.raises(DomainError):
        gateway.call("read_source", {"source_id": source_id})
    with store.sessions.begin() as session:
        future = store.add_source(session, run["project_id"], SourceInput(title="后来资料", kind="history", text="新安排"))
        future_id = future.id
    with pytest.raises(DomainError):
        gateway.call("read_source", {"source_id": future_id})


def test_model_call_budget_is_enforced(store):
    run = make_run(store)
    store.reserve(run["id"], "model")
    store.reserve(run["id"], "model")
    with pytest.raises(DomainError, match="预算"):
        store.reserve(run["id"], "model")


def test_artifacts_do_not_silently_overwrite(store):
    run = make_run(store)
    first = store.commit_artifact(run["id"], "0:meeting", "meeting", {"summary": "A"})
    assert store.commit_artifact(run["id"], "0:meeting", "meeting", {"summary": "A"}) == first
    with pytest.raises(DomainError, match="冲突"):
        store.commit_artifact(run["id"], "0:meeting", "meeting", {"summary": "B"})


def test_live_without_configuration_does_not_fall_back_to_demo(store):
    with pytest.raises(DomainError, match="真实模型"):
        make_run(store, mode="live")
