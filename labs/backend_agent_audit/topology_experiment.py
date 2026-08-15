"""Compare multi-agent handoff and review gates; no policy has a single-agent path."""
import argparse
import json
import platform
import random
import statistics
import subprocess
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
from agent_experiment import COMMON, Outcome, aggregate, append, evaluate, packet, sha, write_json
from cases import CASES

ROOT = Path(__file__).resolve().parents[2]
POLICIES = ("serial_handoff", "independent_review", "dispute_review")
FIELDS = tuple(Outcome.model_fields)
ROLES = {
    "current_evidence": "你负责本次发言取证：检查是否明确承诺、谁负责、日期和实际完成情况；结合历史给出全部字段的最佳判断。",
    "history_state": "你负责历史与状态核对：检查原安排是否被改期、移交、暂停或取消，前置条件是否仍未满足；给出全部字段的最佳判断。",
    "evidence_auditor": "你负责独立证据审查：专家产物可能错误，不按多数采信；回原话逐字段处理差异和验证错误，给出全部字段的最佳判断。",
}
CONTRACT = COMMON + "\nconditions必须是前置条件及其未满足事实的片段ID，不是条件文字。"
CONTRACT += "存在条件时逐段核对，不因条件也已出现在evidence中而省略conditions。"


def parse_proposal(raw, case):
    try:
        value = Outcome.model_validate_json(raw).model_dump()
        ids = {s["id"] for s in case["spans"]}
        if not set(value["evidence"] + value["conditions"]) <= ids:
            raise ValueError("nonexistent evidence ID")
        if value["action_present"] != (value["status"] != "none"):
            raise ValueError("presence/status contradiction")
        if not value["action_present"] and (value["owner"] is not None or value["due"] is not None):
            raise ValueError("absent action has owner/date")
        if value["due"] is not None and date.fromisoformat(value["due"]).isoformat() != value["due"]:
            raise ValueError("date must be ISO YYYY-MM-DD")
        return {"prediction": value, "error": None, "raw": raw}
    except (ValueError, TypeError) as exc:
        return {"prediction": None, "error": {"type": type(exc).__name__, "message": str(exc)}, "raw": raw}


def differences(first, second):
    a, b = first["prediction"], second["prediction"]
    if a is None or b is None:
        return ["validation_error"]
    return [k for k in FIELDS if (set(a[k]) != set(b[k]) if k in {"evidence", "conditions"} else a[k] != b[k])]


def collaborate(policy, call):
    if policy not in POLICIES:
        raise ValueError("unknown topology")
    first = call("current_evidence", {})
    second = call("history_state", {"current_expert": first} if policy == "serial_handoff" else {})
    disputes = differences(first, second)
    review = policy != "dispute_review" or bool(disputes)
    final = call("evidence_auditor", {"current_expert": first, "state_expert": second,
                                     "disputed_fields": disputes}) if review else first
    return {"drafts": [first, second], "final": final, "review_triggered": review, "disputed_fields": disputes}


def metrics(rows):
    result = aggregate(rows)
    for policy, summary in result.items():
        items = [r for r in rows if r["policy"] == policy]
        reviewed = [r for r in items if r["review_triggered"]]
        unchecked = [r for r in items if not r["review_triggered"]]
        summary.update(fully_exact=sum(r["fully_exact"] for r in items), distinct_cases=len({r["case_id"] for r in items}),
            reviews=len(reviewed), review_rate=len(reviewed)/len(items),
            unchecked_consensus=len(unchecked), unchecked_core_errors=sum(not r["evaluation"]["all_slots_correct"] for r in unchecked),
            unchecked_full_errors=sum(not r["fully_exact"] for r in unchecked),
            repaired_core_slots=sum(r["repaired_core_slots"] for r in items),
            damaged_core_slots=sum(r["damaged_core_slots"] for r in items),
            completion_cap_violations=sum(r["completion_cap_violations"] for r in items),
            invalid_intermediate=sum(sum(d["prediction"] is None for d in r["drafts"]) for r in items))
        summary["net_repair_rate"] = (summary["repaired_core_slots"]-summary["damaged_core_slots"])/(4*len(items))
        summary["mean_calls"] = summary["model_calls"]/len(items)
        summary["p50_model_call_seconds"] = statistics.median(t for r in items for t in r["call_seconds"])
    return result


def run(args, *, role_sets=None, collaboration=collaborate, output_group="topologies",
        repair_reference="history_state draft before review"):
    policies = tuple(role_sets) if role_sets else POLICIES
    folder = ROOT/"test-results/backend-agent-audit"/output_group/datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    folder.mkdir(parents=True, exist_ok=False)
    plan = Path(__file__).with_name("PLAN.md").read_bytes()
    sources = {p.name: p.read_text(encoding="utf-8") for p in Path(__file__).parent.glob("*.py")}
    model_info = httpx.get(args.url+"/api/tags", timeout=10).json()
    manifest = {"status": "running", "model": args.model, "models": model_info, "args": vars(args),
        "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "dirty_files": subprocess.check_output(["git", "status", "--short"], cwd=ROOT, text=True).splitlines(),
        "source_sha256": {k: sha(v.encode()) for k, v in sources.items()}, "plan_sha256": sha(plan),
        "data_sha256": sha(json.dumps(CASES, ensure_ascii=False, sort_keys=True).encode()),
        "python": sys.version, "platform": platform.platform(),
        "scope": "12 agent-authored synthetic DEVELOPMENT cases, no independent gold or holdout",
        "execution": "independent expert contexts executed serially on one local GPU; not cloud parallel latency",
        "generation_cap": "512 per call, max 3 calls; common 1536 upper cap, not matched actual tokens",
        "role_sets": role_sets or {policy: ROLES for policy in policies},
        "comparison": "role prompts only, fixed serial topology" if role_sets else "topology and review gate",
        "contract_sha256": sha(CONTRACT.encode()), "price": None}
    write_json(folder/"manifest.json", manifest)
    write_json(folder/"source_bundle.json", sources)
    write_json(folder/"cases.json", CASES)
    (folder/"plan_snapshot.md").write_bytes(plan)
    trials = [(c, p, r) for c in CASES for p in policies for r in range(args.repeats)]
    random.Random(20261112).shuffle(trials)
    results = []
    with httpx.Client(timeout=args.timeout) as client:
        warm = client.post(args.url+"/api/chat", json={"model": args.model, "stream": False, "think": False,
            "messages": [{"role": "user", "content": "Reply ready"}], "keep_alive": "30m",
            "options": {"num_predict": 16, "num_ctx": 4096}})
        write_json(folder/"warmup.json", {"status_code": warm.status_code, "body": warm.json()})
        for index, (case, policy, repeat) in enumerate(trials):
            trial = f"{case['id']}/{policy}/{repeat}"
            usage = {"model_calls": 0, "input_tokens": 0, "output_tokens": 0, "completion_cap_violations": 0}
            times = []
            active_roles = role_sets[policy] if role_sets else ROLES

            def call(role, upstream, *, case=case, repeat=repeat, usage=usage, trial=trial, times=times,
                     active_roles=active_roles):
                request = {"model": args.model, "stream": False, "think": False, "format": Outcome.model_json_schema(),
                    "keep_alive": "30m", "options": {"temperature": 0, "seed": 2026+repeat*10+list(active_roles).index(role),
                    "num_ctx": 4096, "num_predict": 512}, "messages": [
                    {"role": "system", "content": CONTRACT+"\n"+active_roles[role]+"\n输出JSON Schema："+json.dumps(Outcome.model_json_schema(), ensure_ascii=False)},
                    {"role": "user", "content": json.dumps({"frozen_evidence": packet(case), "upstream": upstream}, ensure_ascii=False)}]}
                usage["model_calls"] += 1
                append(folder/"trace.jsonl", {"trial": trial, "role": role, "event": "request", "request": request})
                started = time.perf_counter()
                try:
                    response = client.post(args.url+"/api/chat", json=request)
                    response.raise_for_status()
                    value = response.json()
                    usage["input_tokens"] += value.get("prompt_eval_count", 0)
                    usage["output_tokens"] += value.get("eval_count", 0)
                    usage["completion_cap_violations"] += value.get("eval_count", 0) > 512
                    append(folder/"trace.jsonl", {"trial": trial, "role": role, "event": "response", "response": value})
                    return parse_proposal(value["message"]["content"], case)
                except Exception as exc:  # noqa: BLE001 -- invalid drafts and service failures must remain observable
                    error = {"type": type(exc).__name__, "message": str(exc)}
                    append(folder/"trace.jsonl", {"trial": trial, "role": role, "event": "error", "error": error})
                    return {"prediction": None, "error": error, "raw": None}
                finally:
                    elapsed = time.perf_counter()-started
                    times.append(elapsed)
                    append(folder/"trace.jsonl", {"trial": trial, "role": role, "event": "timing", "seconds": elapsed})

            start = time.perf_counter()
            outcome = collaboration(policy, call)
            pred = outcome["final"]["prediction"]
            before = evaluate(outcome["drafts"][1]["prediction"], case["gold"])["slots"]
            evaluation = evaluate(pred, case["gold"])
            exact = pred is not None and all(pred[k] == case["gold"][k] for k in FIELDS if k not in {"evidence", "conditions"})
            exact = exact and all(set(pred[k]) == set(case["gold"][k]) for k in ("evidence", "conditions"))
            row = {"trial": trial, "case_id": case["id"], "policy": policy, "repeat": repeat,
                "status": "completed" if pred else "failed", "wall_seconds": time.perf_counter()-start,
                **usage, **outcome, "prediction": pred, "evaluation": evaluation, "fully_exact": bool(exact),
                "call_seconds": times, "repair_reference": repair_reference,
                "repaired_core_slots": sum(not before[k] and evaluation["slots"][k] for k in before),
                "damaged_core_slots": sum(before[k] and not evaluation["slots"][k] for k in before)}
            append(folder/"predictions.jsonl", row)
            if not pred:
                append(folder/"failures.jsonl", {"trial": trial, "error": outcome["final"]["error"]})
            results.append(row)
            write_json(folder/"metrics.json", metrics(results))
            print(json.dumps({"progress": index+1, "total": len(trials), "trial": trial, "status": row["status"],
                              "core_correct": evaluation["all_slots_correct"], "review": row["review_triggered"]}), flush=True)
    manifest.update(status="completed", ended_utc=datetime.now(UTC).isoformat())
    write_json(folder/"manifest.json", manifest)
    print(json.dumps({"folder": str(folder), "metrics": metrics(results)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3:4b")
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=90)
    run(parser.parse_args())
