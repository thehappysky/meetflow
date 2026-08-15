"""2x2 internal-protocol ablation: reference decoding and valid handoff deduplication."""
import argparse
import copy
import json

from internal_cases import INTERNAL_CASES
from topology_experiment import ROLES, differences, run

POLICIES = ("baseline", "id_enum", "compact", "id_enum_compact")
ROLE_SET = {"stage1": ROLES["current_evidence"], "stage2": ROLES["history_state"],
            "reviewer": ROLES["evidence_auditor"]}


def compact(value):
    if isinstance(value, list):
        return [compact(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {key: compact(item) for key, item in value.items()
            if not (key == "raw" and value.get("prediction") is not None)}


def transform_request(policy, role, case, request):
    if policy not in POLICIES:
        raise ValueError("unknown internal protocol")
    result = copy.deepcopy(request)
    if "id_enum" in policy:
        ids = [s["id"] for s in case["spans"]]
        for name in ("evidence", "conditions"):
            result["format"]["properties"][name]["items"]["enum"] = ids
    if "compact" in policy:
        body = json.loads(result["messages"][-1]["content"])
        body["upstream"] = compact(body["upstream"])
        result["messages"][-1]["content"] = json.dumps(body, ensure_ascii=False)
    return result


def handoff(policy, call):
    if policy not in POLICIES:
        raise ValueError("unknown internal protocol")
    first = call("stage1", {})
    second = call("stage2", {"first_agent": first})
    disputes = differences(first, second)
    final = call("reviewer", {"first_agent": first, "second_agent": second, "disputed_fields": disputes})
    return {"drafts": [first, second], "final": final, "review_triggered": True, "disputed_fields": disputes}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=90)
    run(parser.parse_args(), role_sets={p: ROLE_SET for p in POLICIES}, collaboration=handoff,
        output_group="internals", repair_reference="stage2 before review", cases=INTERNAL_CASES,
        request_transform=transform_request, comparison="2x2 reference decoding and valid handoff deduplication, fixed roles/topology")
