import pytest
from meetflow.agents import validate_result
from meetflow.domain import ROLE_NAMES, DomainError
from meetflow.providers import DemoProvider
from role_schema_probe import constrain_evidence, role_schema


@pytest.mark.parametrize("role", ROLE_NAMES)
def test_candidate_schema_has_no_dangling_refs_and_keeps_still_legal_fields(role):
    schema = role_schema(role)
    assert {"summary", "claims", "risks", "questions"} <= set(schema["properties"])
    def visit(value):
        if isinstance(value, dict):
            if "$ref" in value:
                assert value["$ref"].split("/")[-1] in schema["$defs"]
            for child in value.values(): visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    visit(schema)
    value = {"summary": "依据", "claims": [{"text": "依据", "kind": "fact", "evidence": ["s1"]}]}
    if role == 'coordinator':
        value['delegations'] = DemoProvider.plan([])['delegations']
    result = validate_result(value,
                             role, {"s1": {"source_kind": "transcript"}}, [])
    assert result["claims"][0]["text"] == "依据"


@pytest.mark.parametrize("role", [r for r in ROLE_NAMES if r != "planner"])
def test_action_field_removed_only_where_server_already_disallows_actions(role):
    assert "actions" not in role_schema(role)["properties"]
    with pytest.raises(DomainError, match="执行规划"):
        validate_result({"actions": [{"id": "a", "title": "安排", "evidence": ["s1"]}]},
                        role, {"s1": {"source_kind": "transcript"}}, [])


def test_product_evidence_enum_does_not_constrain_human_readable_action_conditions():
    schema = constrain_evidence(role_schema("planner"), ["s1"])
    action = schema["$defs"]["Action"]["properties"]
    assert action["evidence"]["items"]["enum"] == ["s1"]
    assert "enum" not in action["conditions"]["items"]


def test_probe_wire_adapter_preserves_client_timeout(tmp_path, monkeypatch):
    import json

    import httpx
    import role_schema_probe
    def handle(request):
        assert request.extensions["timeout"]["read"] == 0.125
        assert json.loads(request.content)["options"] == {"num_predict": 512, "temperature": 0, "seed": 2026}
        return httpx.Response(200, json={"ok": True})
    monkeypatch.setattr(role_schema_probe.httpx, "HTTPTransport", lambda: httpx.MockTransport(handle))
    with httpx.Client(transport=role_schema_probe.DeterministicTransport(tmp_path), timeout=0.125) as client:
        assert client.post("http://local-test/api/chat", json={"options": {"num_predict": 512}}).json() == {"ok": True}
