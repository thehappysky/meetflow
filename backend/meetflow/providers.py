"""Live tool-calling transport and explicitly labelled offline rule simulation."""
import json
import re
from difflib import SequenceMatcher

import httpx

from .domain import AgentResult, DomainError, uid


def tool_call(name, args):
    return {"id": uid("call_"), "type": "function", "function": {
        "name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


class LiveProvider:
    def __init__(self, settings):
        self.settings = settings

    def complete(self, role, messages, tools, info):
        if not self.settings.live_available:
            raise DomainError("真实模型未配置", 422)
        try:
            with httpx.Client(timeout=self.settings.model_timeout) as client:
                response = client.post(self.settings.model_base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": f"Bearer {self.settings.model_api_key}"},
                    json={"model": self.settings.model_name, "messages": messages, "tools": tools,
                          "max_completion_tokens": self.settings.max_output_tokens})
            if response.is_error:
                raise DomainError(f"模型服务返回 HTTP {response.status_code}；请检查模型、工具调用支持及额度", 502)
            payload = response.json()
            message = payload["choices"][0]["message"]
            result = {"role": "assistant", "content": message.get("content")}
            if message.get("tool_calls"):
                result["tool_calls"] = message["tool_calls"]
            return result, payload.get("usage", {})
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise DomainError(f"模型调用失败：{type(exc).__name__}；不会回退为演示结果", 502) from exc


class DemoProvider:
    """Rule-based protocol simulation. Never represented as model inference."""
    def complete(self, role, messages, tools, info):
        observations = []
        for message in messages:
            if message["role"] == "tool":
                try:
                    observations.append((message.get("name"), json.loads(message["content"])))
                except ValueError:
                    pass
        names = {name for name, _ in observations}
        if "list_sources" not in names:
            calls = [tool_call("list_sources", {}), tool_call("read_upstream", {})]
            if role != "meeting":
                calls.append(tool_call("read_tasks", {}))
            return {"role": "assistant", "content": None, "tool_calls": calls}, {}
        sources = next(value for name, value in observations if name == "list_sources")
        if role == "coordinator":
            return self.submit(self.plan(sources)), {}
        if "read_source" not in names:
            chosen = [source for source in sources if role != "meeting" or source["kind"] == "transcript"]
            return {"role": "assistant", "content": None,
                    "tool_calls": [tool_call("read_source", {"source_id": source["id"]}) for source in chosen[:8]]}, {}
        read_sources = [value for name, value in observations if name == "read_source" and "spans" in value]
        spans = [{**span, "kind": source["kind"]} for source in read_sources for span in source["spans"]]
        dates = [value for name, value in observations if name == "normalize_date"]
        expression = next((match.group() for span in spans if span["kind"] == "transcript"
                           for match in [re.search(r"下(?:下)?周[一二三四五六日天]", span["text"])] if match), None)
        if expression and not dates and role in {"meeting", "planner"}:
            return {"role": "assistant", "content": None,
                    "tool_calls": [tool_call("normalize_date", {"expression": expression})]}, {}
        tasks = next((value for name, value in observations if name == "read_tasks"), [])
        upstream = next((value for name, value in observations if name == "read_upstream"), {})
        return self.submit(self.analyze(role, spans, tasks, dates, upstream)), {}

    @staticmethod
    def submit(result):
        return {"role": "assistant", "content": None,
                "tool_calls": [tool_call("submit_result", AgentResult.model_validate(result).model_dump(mode="json"))]}

    @staticmethod
    def plan(sources):
        jobs = [{"id": "meeting", "role": "meeting", "goal": "理解本次会议的事实、决定、承诺和条件", "depends_on": []}]
        dependencies = ["meeting"]
        if any(source["kind"] != "transcript" for source in sources):
            jobs.append({"id": "context", "role": "context", "goal": "核对项目历史、任务、成员与需求", "depends_on": []})
            dependencies.append("context")
        jobs.extend([
            {"id": "planner", "role": "planner", "goal": "根据证据提出可审核的任务变化", "depends_on": dependencies},
            {"id": "risk", "role": "risk", "goal": "核查执行条件与状态口径冲突", "depends_on": dependencies},
            {"id": "auditor", "role": "auditor", "goal": "寻找结论和行动方案中的反证", "depends_on": ["planner", "risk"]},
        ])
        return {"summary": "离线演示：按可用资料安排调查、规划和审查。", "delegations": jobs}

    def analyze(self, role, spans, tasks, dates, upstream):
        current = [span for span in spans if span["kind"] == "transcript"]
        historical = [span for span in spans if span["kind"] != "transcript"]
        claims = [{"text": span["text"], "kind": "condition" if re.search("如果|必须|前提|等.*通过", span["text"])
                   else "unresolved" if re.search("候选|不定|待确认|没定", span["text"]) else "fact",
                   "evidence": [span["id"]]} for span in current[:20]]
        if role == "meeting":
            return {"summary": "\n".join(span["text"] for span in current[:5]), "claims": claims}
        if role == "context":
            return {"summary": f"离线规则读取了 {len(historical)} 条项目资料和 {len(tasks)} 个任务快照。",
                    "claims": [{"text": span["text"], "kind": "fact", "evidence": [span["id"]]}
                               for span in historical[:20]]}
        if role == "planner":
            actions, questions = [], []
            for span in current:
                text = span["text"]
                if not re.search("负责|我来|交付|改造|准备|跟进|完成|发布", text):
                    continue
                if re.search("只是候选|今天不定", text):
                    continue
                task = next((task for task in tasks if SequenceMatcher(None, task["title"], text)
                             .find_longest_match().size >= 4), None)
                owner = task["owner"] if task else None
                speaker = re.match(r"^([^：:]{1,12})[：:]", text)
                if speaker and re.search("我|负责", text):
                    owner = speaker[1]
                due = next((item["date"] for item in dates if item["raw"] in text), None)
                if re.search("候选|不定|再定", text):
                    due = None
                conditions = [sentence.strip() for sentence in re.split("[。；]", text)
                              if re.search("必须|如果|通过|确定前|先不要", sentence)]
                action_id = f"action-{len(actions) + 1}"
                action = {"id": action_id, "title": task["title"] if task else re.sub(r"^[^：:]{1,12}[：:]", "", text)[:100],
                          "owner": owner, "due": due, "status": "blocked" if "必须" in text else "todo",
                          "conditions": conditions, "evidence": [span["id"]],
                          "target_task_id": task["id"] if task else None,
                          "expected_version": task["version"] if task else None,
                          "rationale": "离线规则生成的候选，请人工核对；不代表真实模型理解。"}
                actions.append(action)
                if "老王" in text:
                    action["owner"] = None
                    questions.append({"key": "owner:老王", "text": "本次会议中的“老王”指谁？", "options": [],
                                      "affected_actions": [action_id], "evidence": [span["id"]]})
            return {"summary": "以下为离线规则生成的任务候选，确认后才写入看板。",
                    "actions": actions, "questions": questions}
        if role == "risk":
            risks = [{"text": span["text"], "kind": "condition", "evidence": [span["id"]]}
                     for span in current if re.search("必须|未完成|没定|不要|候选|不代表|待", span["text"])]
            return {"summary": "离线规则标出条件与不确定表达；未完成完整语义核查。", "risks": risks}
        challenges = []
        span_map = {span["id"]: span for span in spans}
        for value in upstream.values():
            result = value.get("result", {})
            for action in result.get("actions", []):
                evidence_text = " ".join(span_map.get(ref, {}).get("text", "") for ref in action["evidence"])
                if action.get("due") and re.search("只是候选|今天不定|日期再定", evidence_text):
                    challenges.append({"text": "候选窗口不能直接作为已确认截止日期", "action_id": action["id"],
                                       "severity": "blocking", "evidence": action["evidence"]})
        return {"summary": "离线规则完成引用和候选日期检查；不等于真实模型反证审查。", "challenges": challenges}
