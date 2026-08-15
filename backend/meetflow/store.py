import re
import threading
import time

from sqlalchemy import select, update

from .database import Artifact, Audit, Meeting, Project, Report, Run, RunEvent, Source, Task, row_dict
from .domain import DomainError, MeetingInput, SourceInput, digest, now, uid


class Store:
    def __init__(self, sessions, settings):
        self.sessions, self.settings = sessions, settings
        self._deadlines = {}
        self._deadline_lock = threading.Lock()

    def begin_processing(self, run_id):
        with self._deadline_lock:
            self._deadlines[run_id] = time.monotonic() + self.settings.run_timeout

    def end_processing(self, run_id):
        with self._deadline_lock:
            self._deadlines.pop(run_id, None)

    def audit(self, session, action, target_id, body=None):
        session.add(Audit(action=action, target_id=target_id, body=body or {}))

    def create_project(self, name):
        with self.sessions.begin() as session:
            project = Project(id=uid("p_"), name=name)
            session.add(project)
            session.flush()
            self.audit(session, "project.created", project.id, {"name": name})
            return row_dict(project)

    def add_source(self, session, project_id, value: SourceInput, meeting_id=None):
        if not session.get(Project, project_id):
            raise DomainError("项目不存在", 404)
        source_id = uid("s_")
        spans = [span.model_dump() for span in value.spans]
        if not spans:
            spans = [{"id": str(i), "text": line.strip(), "speaker": None, "start": None, "end": None}
                     for i, line in enumerate(re.split(r"\n+", value.text)) if line.strip()]
        if not spans:
            raise DomainError("资料不能为空")
        if len({span["id"] for span in spans}) != len(spans):
            raise DomainError("资料片段编号重复")
        for span in spans:
            span["id"] = f"{source_id}:{span['id']}"
        body = {"spans": spans}
        source = Source(id=source_id, project_id=project_id, meeting_id=meeting_id,
                        title=value.title, kind=value.kind, content_hash=digest(body), body=body)
        session.add(source)
        session.flush()
        self.audit(session, "source.created", source.id, {"kind": value.kind, "hash": source.content_hash})
        return source

    def create_meeting(self, value: MeetingInput):
        if value.mode == "live" and not self.settings.live_available:
            raise DomainError("真实模型尚未配置，请设置服务端模型地址、名称与密钥；不会自动转为演示模式", 422)
        with self.sessions.begin() as session:
            if not session.get(Project, value.project_id):
                raise DomainError("项目不存在", 404)
            meeting = Meeting(id=uid("m_"), project_id=value.project_id, title=value.title,
                              meeting_time=value.meeting_time.isoformat() if value.meeting_time else None,
                              timezone=value.timezone)
            session.add(meeting)
            session.flush()
            self.add_source(session, value.project_id,
                            SourceInput(title=value.title, kind="transcript", text=value.transcript), meeting.id)
            run = self.create_run(session, meeting, value.mode)
            self.audit(session, "meeting.created", meeting.id, {"run_id": run.id, "mode": value.mode})
            return {"meeting": row_dict(meeting), "run": row_dict(run)}

    def create_run(self, session, meeting, mode):
        sources = session.scalars(select(Source).where(Source.project_id == meeting.project_id)).all()
        sources = [source for source in sources if source.meeting_id is None
                   or source.meeting_id == meeting.id or source.kind == "history"]
        tasks = session.scalars(select(Task).where(Task.project_id == meeting.project_id)).all()
        manifest = {"sources": [row_dict(source) for source in sources],
                    "tasks": [row_dict(task) for task in tasks], "title": meeting.title,
                    "meeting_time": meeting.meeting_time, "timezone": meeting.timezone}
        run = Run(id=uid("r_"), meeting_id=meeting.id, project_id=meeting.project_id, mode=mode,
                  manifest=manifest)
        session.add(run)
        session.flush()
        session.add(RunEvent(run_id=run.id, kind="queued", message="会议已保存，等待处理", payload={}))
        return run

    def get_run(self, run_id):
        with self.sessions() as session:
            run = session.get(Run, run_id)
            if not run:
                raise DomainError("运行不存在", 404)
            return row_dict(run)

    def event(self, run_id, kind, message, payload=None):
        with self.sessions.begin() as session:
            session.add(RunEvent(run_id=run_id, kind=kind, message=message, payload=payload or {}))

    def update_run(self, run_id, **values):
        with self.sessions.begin() as session:
            session.execute(update(Run).where(Run.id == run_id).values(**values, updated_at=now()))

    def check_active(self, run_id):
        run = self.get_run(run_id)
        if run["cancel_requested"]:
            raise DomainError("处理已停止", 409)
        with self._deadline_lock:
            deadline = self._deadlines.get(run_id)
        if deadline is not None and time.monotonic() > deadline:
            raise DomainError("本次执行达到时间预算，可恢复处理；累计调用预算仍保留", 409)
        return run

    def reserve(self, run_id, kind):
        self.check_active(run_id)
        field = Run.model_calls if kind == "model" else Run.tool_calls
        limit = self.settings.max_model_calls if kind == "model" else self.settings.max_tool_calls
        with self.sessions.begin() as session:
            result = session.execute(update(Run).where(Run.id == run_id, field < limit,
                                                       Run.cancel_requested.is_(False))
                                     .values({field: field + 1, Run.updated_at: now()}))
            if result.rowcount != 1:
                raise DomainError("本次处理已达到调用预算或已停止", 409)

    def add_usage(self, run_id, usage):
        with self.sessions.begin() as session:
            session.execute(update(Run).where(Run.id == run_id).values(
                input_tokens=Run.input_tokens + usage.get("prompt_tokens", 0),
                output_tokens=Run.output_tokens + usage.get("completion_tokens", 0)))

    def artifacts(self, run_id):
        with self.sessions() as session:
            return [row_dict(row) for row in session.scalars(
                select(Artifact).where(Artifact.run_id == run_id).order_by(Artifact.created_at))]

    def commit_artifact(self, run_id, task_key, role, body):
        with self.sessions.begin() as session:
            existing = session.scalar(select(Artifact).where(Artifact.run_id == run_id,
                                                             Artifact.task_key == task_key))
            if existing:
                if existing.body != body:
                    raise DomainError("同一任务的不可变产物发生冲突", 409)
                return existing.id
            artifact = Artifact(id=uid("a_"), run_id=run_id, task_key=task_key, role=role, body=body)
            session.add(artifact)
            session.flush()
            return artifact.id

    def latest_report(self, run_id):
        with self.sessions() as session:
            report = session.scalar(select(Report).where(Report.run_id == run_id)
                                    .order_by(Report.revision.desc()))
            return row_dict(report) if report else None

    def save_report(self, run_id, body):
        current = self.latest_report(run_id)
        if current and current["content_hash"] == digest(body):
            return current
        with self.sessions.begin() as session:
            report = Report(id=uid("rep_"), run_id=run_id,
                            revision=current["revision"] + 1 if current else 1,
                            body=body, content_hash=digest(body))
            session.add(report)
            session.flush()
            self.audit(session, "report.created", report.id, {"hash": report.content_hash})
            return row_dict(report)

    def source_map(self, run_id):
        run = self.get_run(run_id)
        return {span["id"]: {**span, "source_id": source["id"], "source_title": source["title"],
                            "source_kind": source["kind"], "source_version": source["version"]}
                for source in run["manifest"]["sources"] for span in source["body"]["spans"]}
