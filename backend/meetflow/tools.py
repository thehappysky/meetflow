import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .domain import DomainError


def normalize_date(expression, meeting_time, timezone):
    if not meeting_time:
        return {"raw": expression, "date": None, "reason": "实际会议时间待确认"}
    base = datetime.fromisoformat(meeting_time)
    if base.tzinfo is not None:
        base = base.astimezone(ZoneInfo(timezone))
    day = base.date()
    explicit = re.search(r"(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})", expression)
    if explicit:
        from datetime import date
        return {"raw": expression, "date": date(*map(int, explicit.groups())).isoformat()}
    weekdays = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    match = re.search(r"(下下|下|本|这)?(?:周|星期)([一二三四五六日天])", expression)
    if match:
        weeks = {"下下": 2, "下": 1, "本": 0, "这": 0, None: 0}[match[1]]
        target = day - timedelta(days=day.weekday()) + timedelta(weeks=weeks, days=weekdays[match[2]])
        return {"raw": expression, "date": target.isoformat()}
    for word, delta in (("后天", 2), ("明天", 1), ("今天", 0)):
        if word in expression:
            return {"raw": expression, "date": (day + timedelta(days=delta)).isoformat()}
    return {"raw": expression, "date": None, "reason": "未识别，保留待确认"}


TOOLS = {
    "list_sources": ("列出本次运行可访问的资料标题及片段索引，不含全文。", {}),
    "read_source": ("读取清单中某份资料的原文片段。", {"source_id": {"type": "string"}}),
    "search_evidence": ("在已授权快照中按词语查找原文；空关键词列出片段。", {"query": {"type": "string"}}),
    "read_tasks": ("读取本次运行冻结的项目任务状态和版本。", {}),
    "read_upstream": ("读取已经完成的专家结果和审查反馈。", {}),
    "normalize_date": ("以实际会议时间解析中文相对日期；不能推断承诺。", {"expression": {"type": "string"}}),
}


class ToolGateway:
    def __init__(self, store, run_id, role, upstream):
        self.store, self.run_id, self.role, self.upstream = store, run_id, role, upstream

    def definitions(self):
        result = []
        for name, (description, props) in TOOLS.items():
            if self.role == "meeting" and name == "read_tasks":
                continue
            result.append({"type": "function", "function": {"name": name, "description": description,
                           "parameters": {"type": "object", "properties": props,
                                          "required": list(props), "additionalProperties": False}}})
        return result

    def call(self, name, args):
        allowed = {tool["function"]["name"] for tool in self.definitions()}
        if name not in allowed:
            raise DomainError("该角色不允许调用此工具", 403)
        props = TOOLS[name][1]
        if set(args) != set(props) or any(not isinstance(value, str) for value in args.values()):
            raise DomainError("工具参数不符合契约")
        self.store.reserve(self.run_id, "tool")
        run = self.store.get_run(self.run_id)
        sources = run["manifest"]["sources"]
        if name == "list_sources":
            value = [{"id": source["id"], "title": source["title"], "kind": source["kind"],
                      "spans": len(source["body"]["spans"])} for source in sources]
        elif name == "read_source":
            source = next((item for item in sources if item["id"] == args["source_id"]), None)
            if not source or (self.role == "meeting" and source["kind"] != "transcript"):
                raise DomainError("资料不在本角色可读范围", 403)
            value = {"title": source["title"], "kind": source["kind"], "spans": source["body"]["spans"]}
        elif name == "search_evidence":
            terms = re.findall(r"[\w]+", args["query"].lower())
            spans = self.store.source_map(self.run_id).values()
            if self.role == "meeting":
                spans = [span for span in spans if span["source_kind"] == "transcript"]
            scored = [(sum(term in span["text"].lower() for term in terms), span) for span in spans]
            value = [span for score, span in sorted(scored, key=lambda pair: -pair[0])
                     if score or not terms][:40]
        elif name == "read_tasks":
            value = run["manifest"]["tasks"]
        elif name == "read_upstream":
            value = self.upstream
        else:
            value = normalize_date(args["expression"], run["manifest"]["meeting_time"],
                                   run["manifest"]["timezone"])
        encoded = json.dumps(value, ensure_ascii=False)
        if len(encoded) > 24000:
            value = {"truncated": True, "message": "结果较长，请缩小查询或读取指定资料", "preview": encoded[:20000]}
        self.store.event(self.run_id, "tool", f"{self.role} · {name}",
                         {"role": self.role, "tool": name, "args": args, "result": value})
        return value
