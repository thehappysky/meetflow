import pytest


@pytest.fixture(autouse=True)
def isolate_local_configuration(monkeypatch):
    # Historical tests use the explicitly isolated legacy mode; access tests opt in via Settings(auth_enabled=True).
    monkeypatch.setenv("MEETFLOW_AUTH_ENABLED", "false")
    monkeypatch.setenv("MEETFLOW_ASR_MODEL", "")
    monkeypatch.setenv("MEETFLOW_MODEL_API_KEY", "")
    monkeypatch.setenv("MEETFLOW_MODEL_NAME", "")
