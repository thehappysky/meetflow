"""R1 changes role focus only; topology, evidence, output schema and budgets stay fixed."""
import argparse

from topology_experiment import ROLES, differences, run

REVIEWER = ROLES["evidence_auditor"]
ROLE_SETS = {
    "evidence_time": {
        "stage1": ROLES["current_evidence"],
        "stage2": ROLES["history_state"],
        "reviewer": REVIEWER,
    },
    "field_specialists": {
        "stage1": "你负责事项身份和责任核对：检查目标是否存在、销售是否明确承诺、谁实际负责、说话人能否映射姓名，并检查支持原文；给出全部字段的最佳判断。",
        "stage2": "你负责日期、状态和条件核对：检查日期是否可靠，执行状态及有效前置条件，结合责任信息给出全部字段的最佳判断。",
        "reviewer": REVIEWER,
    },
    "generalists": {
        "stage1": "你是综合抽取专家：同时核对目标事项的存在性、负责人、日期、状态、条件和支持证据，给出全部字段的最佳判断。",
        "stage2": "你是综合复核专家：重新阅读所有原话，逐项检查前一专家的存在性、负责人、日期、状态、条件和支持证据，给出全部字段的最佳判断。",
        "reviewer": REVIEWER,
    },
}


def fixed_handoff(policy, call):
    if policy not in ROLE_SETS:
        raise ValueError("unknown role partition")
    first = call("stage1", {})
    second = call("stage2", {"first_agent": first})
    disputes = differences(first, second)
    final = call("reviewer", {"first_agent": first, "second_agent": second, "disputed_fields": disputes})
    return {"drafts": [first, second], "final": final, "review_triggered": True, "disputed_fields": disputes}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=90)
    run(parser.parse_args(), role_sets=ROLE_SETS, collaboration=fixed_handoff,
        output_group="role-partitions", repair_reference="stage2 draft before review")
