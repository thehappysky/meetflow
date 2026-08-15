import json
import re
from pathlib import Path

from .database import Task
from .domain import MeetingInput, SourceInput, Span, uid


def import_demo(store):
    fixture = json.loads((Path(__file__).resolve().parents[2] / "examples/project-review-scenario.json").read_text(encoding="utf-8"))
    project = store.create_project("示例 · 支付项目发布评审")
    members = {item["member_id"]: item["name"] for item in fixture["participants"]}
    transcript = []
    with store.sessions.begin() as session:
        for source in fixture["sources"]:
            if source["kind"] == "transcript":
                transcript = [f"{members.get(span.get('confirmed_member_id'), '发言人')}：{span['text']}"
                              for span in source["spans"]]
                continue
            kind = "history" if source["kind"] == "approved_minutes" else source["kind"]
            store.add_source(session, project["id"], SourceInput(
                title=source["source_id"], kind=kind,
                spans=[Span(id=span["span_id"], text=span["text"]) for span in source["spans"]]))
            if source["kind"] == "task_snapshot":
                for span in source["spans"]:
                    text = span["text"]
                    owner = re.search("负责人([^；。]+)", text)
                    due = re.search(r"截止日期(\d{4}-\d{2}-\d{2})", text)
                    session.add(Task(id=uid("t_"), project_id=project["id"], title=text.split("；")[0],
                                     owner=owner[1] if owner else None, due=due[1] if due else None,
                                     status="done" if "状态完成" in text else "todo", conditions=[], evidence=[],
                                     version=span.get("task_version", 1)))
        store.add_source(session, project["id"], SourceInput(title="项目成员", kind="members",
                         text="\n".join(f"{item['name']}：{item['role']}" for item in fixture["participants"])))
    # Expected outputs in the fixture are deliberately never supplied to agents.
    return store.create_meeting(MeetingInput(project_id=project["id"], title="发布评审 · 演示案例",
        meeting_time=fixture["meeting_start"], timezone=fixture["timezone"], transcript="\n".join(transcript), mode="demo"))
