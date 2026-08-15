import copy

import pytest
from topology_experiment import collaborate, differences, evaluate, metrics, parse_proposal

VALUE = {"action_present": True, "owner": "甲", "due": None, "status": "todo", "conditions": [], "evidence": ["s1", "s2"]}


def proposal(**changes):
    return {"prediction": {**copy.deepcopy(VALUE), **changes}, "error": None, "raw": "retained"}


def test_agreement_uses_sets_but_compares_all_fields():
    assert differences(proposal(), proposal(evidence=["s2", "s1", "s1"])) == []
    assert differences(proposal(), proposal(conditions=["s1"])) == ["conditions"]
    assert differences(proposal(), {"prediction": None}) == ["validation_error"]


@pytest.mark.parametrize("policy,expected", [("serial_handoff", 3), ("independent_review", 3), ("dispute_review", 2)])
def test_each_topology_uses_at_least_two_agents_and_correct_dependencies(policy, expected):
    calls = []
    def call(role, upstream):
        calls.append((role, upstream))
        return proposal()
    result = collaborate(policy, call)
    assert len(calls) == expected
    assert bool(calls[1][1]) == (policy == "serial_handoff")
    assert result["review_triggered"] == (expected == 3)


@pytest.mark.parametrize("invalid", [False, True])
def test_disagreement_or_invalid_draft_always_routes_to_auditor(invalid):
    calls = []
    def call(role, upstream):
        calls.append(role)
        if role == "history_state":
            return {"prediction": None, "error": "invalid", "raw": "unmodified"} if invalid else proposal(status="paused")
        return proposal()
    assert collaborate("dispute_review", call)["review_triggered"]
    assert calls == ["current_evidence", "history_state", "evidence_auditor"]


def test_contradictory_or_foreign_evidence_is_not_valid_consensus():
    case = {"spans": [{"id": "s1"}, {"id": "s2"}]}
    import json
    assert parse_proposal(json.dumps({**VALUE, "action_present": False}), case)["prediction"] is None
    assert parse_proposal(json.dumps({**VALUE, "evidence": ["foreign"]}), case)["prediction"] is None
    assert parse_proposal(json.dumps(VALUE), case)["prediction"] == VALUE


def test_agreeing_but_wrong_experts_are_counted_as_unchecked_errors():
    row = {"policy": "dispute_review", "case_id": "same-wrong", "wall_seconds": 1,
        "input_tokens": 20, "output_tokens": 10, "model_calls": 2, "status": "completed",
        "evaluation": evaluate(VALUE, {**VALUE, "owner": "乙"}), "fully_exact": False,
        "review_triggered": False, "drafts": [proposal(), proposal()], "repaired_core_slots": 0,
        "damaged_core_slots": 0, "completion_cap_violations": 0, "call_seconds": [0.5, 0.5]}
    result = metrics([row])["dispute_review"]
    assert result["unchecked_consensus"] == result["unchecked_core_errors"] == result["unchecked_full_errors"] == 1
    assert result["all_slots_correct"] == 0
