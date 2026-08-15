"""Candidate role tool schemas and bounded real AgentRunner diagnostic; product is unchanged."""
import copy
import json
import sys
import tempfile
import time
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import httpx
from agent_experiment import sha, write_json
from pydantic import create_model

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
from current_graph_probe import TracedProvider
from meetflow.agents import AgentRunner
from meetflow.domain import ROLE_NAMES, AgentResult, Contract
from meetflow.providers import LiveProvider
from test_access import access

EXCLUSIVE = {"actions": "planner", "challenges": "auditor", "delegations": "coordinator"}


@lru_cache
def role_schema(role):
    if role not in ROLE_NAMES:
        raise ValueError("unknown role")
    fields = {name: (field.annotation, copy.deepcopy(field)) for name, field in AgentResult.model_fields.items()
              if name not in EXCLUSIVE or EXCLUSIVE[name] == role}
    return create_model(f"{role.title()}Submission", __base__=Contract, **fields).model_json_schema()


def constrain_evidence(schema, ids):
    result = copy.deepcopy(schema)
    def visit(node):
        if isinstance(node, list):
            for child in node:
                visit(child)
        elif isinstance(node, dict):
            field = node.get("properties", {}).get("evidence")
            if field:
                field["items"]["enum"] = ids
            for value in node.values():
                visit(value)
    visit(result)
    return result


class DeterministicTransport(httpx.BaseTransport):
    def __init__(self, folder):
        self.folder = folder

    def handle_request(self, request):
        body = json.loads(request.content)
        body["options"].update(temperature=0, seed=2026)
        with (self.folder / "wire.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(body, ensure_ascii=False) + "\n")
        modified = httpx.Request(request.method, request.url, json=body,
                                 headers={k:v for k,v in request.headers.items() if k != "content-length"},
                                 extensions=request.extensions)
        with httpx.HTTPTransport() as transport:
            response = transport.handle_request(modified)
            response.read()
            return response


class ProfileProvider(TracedProvider):
    def __init__(self, settings, folder, profile):
        super().__init__(settings, folder)
        self.inner = LiveProvider(settings, transport=DeterministicTransport(folder))
        self.profile = profile

    def complete(self, role, messages, tools, info):
        tools = copy.deepcopy(tools)
        for tool in tools:
            if tool["function"]["name"] == "submit_result":
                tool["function"]["parameters"] = (role_schema(role) if self.profile == "role_schema"
                                                    else AgentResult.model_json_schema())
        return super().complete(role, messages, tools, info)


def main():
    folder = ROOT / "test-results/backend-agent-audit/role-schema" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    folder.mkdir(parents=True, exist_ok=False)
    base = AgentResult.model_json_schema()
    size = lambda value: len(json.dumps(value, ensure_ascii=False))
    footprint = [{"role": role, "shared_schema_characters": size(base), "role_schema_characters": size(role_schema(role)),
        "role_schema_fields": list(role_schema(role)["properties"]), "enum_characters_by_reference_count": {
            str(n): size(constrain_evidence(role_schema(role), [f"span-{i:04d}" for i in range(n)]))
            for n in (10, 100, 1000)}} for role in ROLE_NAMES]
    write_json(folder / "footprint.json", footprint)
    files = [Path(__file__), Path(__file__).with_name("current_graph_probe.py"),
             *[ROOT / "backend/meetflow" / name for name in ("agents.py", "providers.py", "domain.py", "tools.py")]]
    manifest = {"status": "running", "scope": "one meeting specialist, one synthetic case, original AgentRunner",
        "model": "qwen3:4b", "models": httpx.get("http://127.0.0.1:11434/api/tags").json(),
        "profiles": ["shared_schema", "role_schema"], "generation_per_call": 512, "max_steps": 6,
        "thinking": False, "temperature": 0, "seed": 2026, "context": 8192,
        "plan_sha256": sha(Path(__file__).with_name("PLAN.md").read_bytes()),
        "source_sha256": {str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in files},
        "limitations": "ordered single-case integration diagnostic, no quality or speed ranking; no product change"}
    write_json(folder / "manifest.json", manifest)
    write_json(folder / "source_bundle.json", {str(p.relative_to(ROOT)):p.read_text(encoding="utf-8") for p in files})
    (folder / "plan_snapshot.md").write_bytes(Path(__file__).with_name("PLAN.md").read_bytes())
    results = []
    with tempfile.TemporaryDirectory(prefix="meetflow-role-schema-") as directory:
        fixture = access.__wrapped__(Path(directory)); a = next(fixture)
        try:
            store = a.app.state.store
            settings = store.settings
            settings.model_name = "qwen3:4b"; settings.model_api_profile = "ollama"
            settings.model_base_url = "http://127.0.0.1:11434"; settings.model_context_window = 8192
            settings.max_output_tokens = 512; settings.max_agent_steps = 6; settings.max_model_calls = 6
            settings.run_timeout = 180
            row = a.client.post("/api/workspace/recordings", headers=a.actors["sales.a"], json={
                "title": "synthetic role schema diagnostic", "meeting_date": "2026-11-12",
                "transcript": "销售王宁：我明天发送报价。"}).json()
            run_id = row["run_id"]
            write_json(folder / "frozen_input.json", store.get_run(run_id)["manifest"])
            for profile in manifest["profiles"]:
                output = folder / profile; output.mkdir()
                store.update_run(run_id, status="running", mode="live", model_calls=0, tool_calls=0,
                                 input_tokens=0, output_tokens=0, error=None)
                store.begin_processing(run_id)
                provider = ProfileProvider(settings, output, profile)
                start = time.perf_counter()
                try:
                    result = AgentRunner(store, provider).run(run_id, "meeting", "提取本次明确承诺及其原文依据", {})
                    item = {"profile": profile, "status": "completed", "result": result}
                except Exception as exc:  # noqa: BLE001 -- retain integration failures, never substitute demo output
                    item = {"profile": profile, "status": "failed", "error": {"type":type(exc).__name__, "message":str(exc)}}
                finally:
                    store.end_processing(run_id)
                run = store.get_run(run_id)
                item.update(wall_seconds=time.perf_counter()-start, model_calls=run["model_calls"], tool_calls=run["tool_calls"],
                            input_tokens=run["input_tokens"], output_tokens=run["output_tokens"])
                results.append(item)
                write_json(folder / "results.json", results)
                print(json.dumps({"directory": str(folder), **item}, ensure_ascii=True), flush=True)
        finally:
            fixture.close()
    manifest["status"] = "completed"
    write_json(folder / "manifest.json", manifest)


if __name__ == "__main__":
    main()
