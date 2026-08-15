import copy

import pytest
from role_partition_experiment import ROLE_SETS, fixed_handoff

VALUE = {"prediction": {"action_present": True, "owner": "甲", "due": None, "status": "todo",
                        "conditions": [], "evidence": ["s1"]}, "error": None, "raw": "saved"}


def test_role_partition_comparison_keeps_reviewer_and_stage_order_identical():
    assert len({roles["reviewer"] for roles in ROLE_SETS.values()}) == 1
    assert all(tuple(roles) == ("stage1", "stage2", "reviewer") for roles in ROLE_SETS.values())
    assert len({roles["stage1"] for roles in ROLE_SETS.values()}) == 3


@pytest.mark.parametrize("policy", ROLE_SETS)
def test_all_partitions_have_identical_handoff_and_three_calls(policy):
    calls = []
    def call(role, upstream):
        calls.append((role, copy.deepcopy(upstream)))
        return copy.deepcopy(VALUE)
    result = fixed_handoff(policy, call)
    assert [r for r, _ in calls] == ["stage1", "stage2", "reviewer"]
    assert calls[0][1] == {}
    assert calls[1][1] == {"first_agent": VALUE}
    assert calls[2][1] == {"first_agent": VALUE, "second_agent": VALUE, "disputed_fields": []}
    assert result["review_triggered"]
