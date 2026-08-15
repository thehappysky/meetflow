import copy
import json

import pytest
from agent_experiment import Outcome
from internal_cases import INTERNAL_CASES
from internal_experiment import POLICIES, compact, handoff, transform_request


def test_compaction_keeps_invalid_raw_and_does_not_mutate_valid_proposals():
    original = {"valid": {"prediction": {"owner": "甲"}, "raw": '{"owner":"甲"}', "error": None},
                "invalid": {"prediction": None, "raw": "repair this", "error": "invalid"}}
    saved = copy.deepcopy(original)
    result = compact(original)
    assert original == saved
    assert result["valid"] == {"prediction": {"owner": "甲"}, "error": None}
    assert result["invalid"] == original["invalid"]


def test_enum_is_case_specific_and_keeps_system_prompt_and_base_schema_unchanged():
    request = {"format": Outcome.model_json_schema(), "messages": [{"role": "system", "content": "same"},
        {"role": "user", "content": json.dumps({"upstream": {}})}]}
    saved = copy.deepcopy(request)
    result = transform_request("id_enum", "stage1", INTERNAL_CASES[0], request)
    assert request == saved
    assert result["messages"] == request["messages"]
    for field in ("conditions", "evidence"):
        assert result["format"]["properties"][field]["items"]["enum"] == ["s1"]


@pytest.mark.parametrize("policy", POLICIES)
def test_internal_factors_never_change_three_stage_collaboration(policy):
    calls = []
    def call(role, upstream):
        calls.append(role)
        return {"prediction": {"action_present": False, "owner": None, "due": None, "status": "none",
                               "conditions": [], "evidence": []}, "raw": "saved", "error": None}
    assert handoff(policy, call)["review_triggered"]
    assert calls == ["stage1", "stage2", "reviewer"]


def test_development_labels_are_well_formed_and_reference_existing_spans():
    assert len(INTERNAL_CASES) == len({c["id"] for c in INTERNAL_CASES}) == 20
    for case in INTERNAL_CASES:
        gold = Outcome.model_validate(case["gold"])
        assert set(gold.conditions + gold.evidence) <= {s["id"] for s in case["spans"]}
        assert gold.action_present == (gold.status != "none")
