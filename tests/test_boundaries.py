import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from meetflow.api import create_app
from meetflow.config import Settings
from meetflow.domain import DomainError
from meetflow.providers import LiveProvider


def test_live_transport_preserves_tool_ids_and_usage():
    def respond(request):
        body = json.loads(request.content)
        assert body["model"] == "configured-test-model"
        assert request.headers["authorization"] == "Bearer test-only-secret"
        return httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": None,
            "tool_calls": [{"id": "call-123", "type": "function", "function": {"name": "read_tasks", "arguments": "{}"}}]}}],
            "usage": {"prompt_tokens": 15, "completion_tokens": 7}})
    provider = LiveProvider(Settings(model_api_key="test-only-secret", model_name="configured-test-model"),
                            transport=httpx.MockTransport(respond))
    message, usage = provider.complete("context", [], [], {})
    assert message["tool_calls"][0]["id"] == "call-123"
    assert usage["completion_tokens"] == 7


def test_live_http_error_is_error_not_demo_result():
    provider = LiveProvider(Settings(model_api_key="test-only-secret", model_name="test-model"),
                            transport=httpx.MockTransport(lambda request: httpx.Response(429)))
    with pytest.raises(DomainError, match="429"):
        provider.complete("meeting", [], [], {})


def test_unconfigured_audio_is_rejected_and_never_creates_transcript(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, start_worker=False))
    with TestClient(app) as client:
        project = client.get("/api/projects").json()[0]
        assert client.get("/api/health").json()["audio_available"] is False
        response = client.post("/api/meetings/audio", data={"project_id": project["id"]},
                               files={"file": ("clip.wav", b"test-only-not-an-audio", "audio/wav")})
        assert response.status_code == 422
        assert client.get("/api/meetings").json() == []


def test_audio_worker_uses_adapter_segments_and_preserves_timestamps(tmp_path, monkeypatch):
    import meetflow.api as api_module
    import meetflow.audio as audio_module
    monkeypatch.setattr(api_module, "audio_available", lambda _: True)
    monkeypatch.setattr(audio_module, "audio_available", lambda _: True)
    segments = [SimpleNamespace(text="李明：我来准备测试用例，下周三交付。", start=1.5, end=8.0)]
    monkeypatch.setattr(audio_module, "load_model", lambda *_: SimpleNamespace(
        transcribe=lambda *args, **kwargs: (iter(segments), SimpleNamespace(language="zh"))))
    app = create_app(Settings(data_dir=tmp_path, start_worker=False, asr_model="test-adapter"))
    with TestClient(app) as client:
        project = client.get("/api/projects").json()[0]
        response = client.post("/api/meetings/audio", data={"project_id": project["id"],
            "meeting_time": "2026-10-23T15:00:00+08:00"}, files={"file": ("clip.wav", b"fixture-bytes", "audio/wav")})
        assert response.status_code == 201
        run_id = response.json()["run"]["id"]
        app.state.driver.process(run_id)
        detail = client.get(f"/api/runs/{run_id}").json()
        assert detail["run"]["status"] == "waiting_review", detail["run"]["error"]
        evidence = list(detail["evidence"].values())
        assert evidence[0]["start"] == 1.5 and evidence[0]["text"] == segments[0].text
        assert client.get(f"/api/meetings/{detail['meeting']['id']}/audio").content == b"fixture-bytes"


def test_sse_reconnect_only_returns_events_after_cursor(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, start_worker=False))
    with TestClient(app) as client:
        run_id = client.post("/api/demo").json()["run"]["id"]
        app.state.driver.process(run_id)
        events = client.get(f"/api/runs/{run_id}/event-log").json()
        response = client.get(f"/api/runs/{run_id}/events", headers={"Last-Event-ID": str(events[-1]["id"])})
        assert response.status_code == 200
        assert '"kind": "snapshot"' in response.text
        assert "id:" not in response.text
