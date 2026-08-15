import asyncio
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, Header, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select, update

from .audio import audio_available
from .config import Settings
from .database import (
    Approval,
    Audit,
    Meeting,
    Project,
    ProjectEvent,
    Run,
    RunEvent,
    Source,
    Task,
    make_database,
    row_dict,
)
from .demo import import_demo
from .domain import DomainError, MeetingInput, ReviewInput, SourceInput, TaskUpdate, now, uid
from .orchestration import RunDriver
from .reviews import approve, render_markdown
from .store import Store


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=200)


def create_app(settings=None, provider=None):
    settings = settings or Settings()
    engine, sessions = make_database(settings)
    store = Store(sessions, settings)
    driver = RunDriver(store, provider)

    @asynccontextmanager
    async def lifespan(app):
        if settings.start_worker:
            driver.recover()
        yield
        driver.close()
        engine.dispose()

    app = FastAPI(title="MeetFlow", version="0.1.0", lifespan=lifespan)
    app.state.store, app.state.driver = store, driver
    app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
                       allow_methods=["GET", "POST", "PATCH"], allow_headers=["Content-Type", "Last-Event-ID"])

    @app.exception_handler(DomainError)
    async def domain_error(_, exc):
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

    @app.exception_handler(ValidationError)
    async def contract_error(_, exc):
        return JSONResponse({"detail": "数据不符合契约", "errors": json.loads(exc.json(include_url=False))}, status_code=422)

    def schedule(run_id, resume=None):
        if settings.start_worker:
            driver.submit(run_id, resume)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.1.0", "live_available": settings.live_available,
                "audio_available": audio_available(settings), "deployment": "local-single-user",
                "demo_notice": "离线演示使用规则模拟，不调用大模型；真实模式需服务端配置。"}

    @app.get("/api/projects")
    def projects():
        with sessions() as session:
            rows = session.scalars(select(Project).order_by(Project.created_at)).all()
        if not rows:
            return [store.create_project("个人项目")]
        return [row_dict(row) for row in rows]

    @app.post("/api/projects", status_code=201)
    def create_project(value: ProjectInput):
        return store.create_project(value.name)

    @app.get("/api/projects/{project_id}/sources")
    def sources(project_id: str):
        with sessions() as session:
            if not session.get(Project, project_id):
                raise DomainError("项目不存在", 404)
            return [row_dict(row) for row in session.scalars(select(Source).where(Source.project_id == project_id))]

    @app.post("/api/projects/{project_id}/sources", status_code=201)
    def add_source(project_id: str, value: SourceInput):
        if value.kind == "transcript":
            raise DomainError("本次转写请通过创建会议提交；项目资料请选择历史、需求或成员等类型")
        with sessions.begin() as session:
            return row_dict(store.add_source(session, project_id, value))

    @app.post("/api/meetings", status_code=201)
    def create_meeting(value: MeetingInput):
        result = store.create_meeting(value)
        schedule(result["run"]["id"])
        return result

    @app.post("/api/demo", status_code=201)
    def demo():
        result = import_demo(store)
        schedule(result["run"]["id"])
        return result

    @app.post("/api/meetings/audio", status_code=201)
    async def create_audio_meeting(file: Annotated[UploadFile, File()], project_id: Annotated[str, Form()],
                                   title: Annotated[str, Form()] = "录音会议",
                                   meeting_time: Annotated[str | None, Form()] = None,
                                   timezone: Annotated[str, Form()] = "Asia/Shanghai",
                                   mode: Annotated[str, Form()] = "demo"):
        if not audio_available(settings):
            raise DomainError("音频转写未启用：安装 audio 依赖并配置 ASR 模型后再上传；当前可粘贴会议文字", 422)
        metadata = MeetingInput(project_id=project_id, title=title, meeting_time=meeting_time,
                                timezone=timezone, mode=mode, transcript="仅校验元数据，不保存为转写")
        if metadata.mode == "live" and not settings.live_available:
            raise DomainError("真实模型未配置", 422)
        extension = Path(file.filename or "").suffix.lower()
        if extension not in {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm"}:
            raise DomainError("录音格式不支持，请使用 WAV、MP3、M4A、FLAC、OGG 或 WEBM", 422)
        with sessions() as session:
            if not session.get(Project, project_id):
                raise DomainError("项目不存在", 404)
        directory = settings.data_dir.resolve() / "audio"
        directory.mkdir(exist_ok=True)
        path = directory / (uid("audio_") + extension)
        size = 0
        try:
            with path.open("wb") as stream:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload_mb * 1024 * 1024:
                        raise DomainError("录音超过当前大小限制", 413)
                    stream.write(chunk)
            if not size:
                raise DomainError("录音文件为空", 422)
            with sessions.begin() as session:
                meeting = Meeting(id=uid("m_"), project_id=project_id, title=title,
                    meeting_time=metadata.meeting_time.isoformat() if metadata.meeting_time else None,
                    timezone=metadata.timezone, audio_path=str(path))
                session.add(meeting)
                session.flush()
                run = store.create_run(session, meeting, metadata.mode)
                store.audit(session, "audio.uploaded", meeting.id, {"bytes": size, "extension": extension})
                result = {"meeting": row_dict(meeting), "run": row_dict(run)}
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
        schedule(result["run"]["id"])
        return result

    @app.get("/api/meetings/{meeting_id}/audio")
    def get_audio(meeting_id: str):
        with sessions() as session:
            meeting = session.get(Meeting, meeting_id)
            if not meeting or not meeting.audio_path:
                raise DomainError("该会议没有录音", 404)
            path = Path(meeting.audio_path).resolve()
        if not path.is_relative_to(settings.data_dir.resolve() / "audio") or not path.is_file():
            raise DomainError("录音文件不可用", 404)
        return FileResponse(path)

    @app.get("/api/meetings")
    def meetings(project_id: str | None = None):
        with sessions() as session:
            query = select(Meeting).order_by(Meeting.created_at.desc())
            if project_id:
                query = query.where(Meeting.project_id == project_id)
            result = []
            for meeting in session.scalars(query.limit(100)):
                run = session.scalar(select(Run).where(Run.meeting_id == meeting.id).order_by(Run.created_at.desc()))
                result.append({**row_dict(meeting), "run": row_dict(run) if run else None})
            return result

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str):
        run = store.get_run(run_id)
        with sessions() as session:
            meeting = session.get(Meeting, run["meeting_id"])
            approval = session.scalar(select(Approval).where(Approval.run_id == run_id))
        return {"run": run, "meeting": row_dict(meeting), "report": store.latest_report(run_id),
                "evidence": store.source_map(run_id), "artifacts": store.artifacts(run_id),
                "approval": row_dict(approval) if approval else None}

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request, after: int = Query(0, ge=0),
                     last_event_id: str | None = Header(None)):
        store.get_run(run_id)
        try:
            cursor = max(after, int(last_event_id or 0))
        except ValueError:
            raise DomainError("无效事件游标") from None

        async def stream():
            nonlocal cursor
            while not await request.is_disconnected():
                with sessions() as session:
                    rows = session.scalars(select(RunEvent).where(RunEvent.run_id == run_id,
                        RunEvent.id > cursor).order_by(RunEvent.id).limit(200)).all()
                for row in rows:
                    cursor = row.id
                    yield f"id: {row.id}\ndata: {json.dumps(row_dict(row), ensure_ascii=False)}\n\n"
                run = store.get_run(run_id)
                if run["status"] in {"waiting_review", "completed", "failed", "cancelled"} and not rows:
                    yield f"data: {json.dumps({'kind': 'snapshot', 'status': run['status']})}\n\n"
                    return
                if not rows:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)
        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/runs/{run_id}/event-log")
    def event_log(run_id: str, after: int = Query(0, ge=0)):
        store.get_run(run_id)
        with sessions() as session:
            return [row_dict(row) for row in session.scalars(select(RunEvent).where(
                RunEvent.run_id == run_id, RunEvent.id > after).order_by(RunEvent.id).limit(500))]

    @app.post("/api/runs/{run_id}/review")
    def review(run_id: str, value: ReviewInput):
        result = approve(store, run_id, value)
        schedule(run_id, {"approval_id": result["id"]})
        return result

    @app.post("/api/runs/{run_id}/cancel")
    def cancel(run_id: str):
        run = store.get_run(run_id)
        if run["status"] not in {"queued", "running"}:
            raise DomainError("当前阶段无须停止", 409)
        store.update_run(run_id, cancel_requested=True)
        store.event(run_id, "cancel_requested", "已请求停止，等待在途处理结束", {})
        return {"status": "stopping"}

    @app.post("/api/runs/{run_id}/retry")
    def retry(run_id: str):
        run = store.get_run(run_id)
        if run["status"] != "failed":
            raise DomainError("只有失败运行可以恢复", 409)
        store.check_active(run_id)
        if run["model_calls"] >= settings.max_model_calls or run["tool_calls"] >= settings.max_tool_calls:
            raise DomainError("本次预算已用尽，请新建分析运行", 409)
        store.update_run(run_id, status="queued", error=None)
        with sessions.begin() as session:
            store.audit(session, "run.retry_requested", run_id)
        schedule(run_id)
        return {"run_id": run_id, "status": "queued"}

    @app.post("/api/meetings/{meeting_id}/reanalyze", status_code=201)
    def reanalyze(meeting_id: str):
        with sessions.begin() as session:
            meeting = session.get(Meeting, meeting_id)
            if not meeting:
                raise DomainError("会议不存在", 404)
            previous = session.scalar(select(Run).where(Run.meeting_id == meeting_id).order_by(Run.created_at.desc()))
            if previous and previous.status in {"queued", "running", "approved"}:
                raise DomainError("该会议已有运行正在处理", 409)
            run = store.create_run(session, meeting, previous.mode)
            store.audit(session, "run.reanalysis_created", run.id, {"meeting_id": meeting_id})
            result = row_dict(run)
        schedule(result["id"])
        return result

    @app.get("/api/runs/{run_id}/export")
    def export(run_id: str):
        run = store.get_run(run_id)
        report = store.latest_report(run_id)
        if not report:
            raise DomainError("纪要尚未生成", 409)
        with sessions() as session:
            meeting = row_dict(session.get(Meeting, run["meeting_id"]))
            approval = session.scalar(select(Approval).where(Approval.run_id == run_id))
            approval_value = row_dict(approval) if approval else None
        content = render_markdown(meeting, run, report, approval_value, store.source_map(run_id))
        return Response(content, media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="meetflow-{run_id}.md"'})

    @app.get("/api/tasks")
    def tasks(project_id: str | None = None):
        with sessions() as session:
            query = select(Task).order_by(Task.updated_at.desc())
            if project_id:
                query = query.where(Task.project_id == project_id)
            return [row_dict(row) for row in session.scalars(query.limit(300))]

    @app.patch("/api/tasks/{task_id}")
    def update_task(task_id: str, value: TaskUpdate):
        with sessions.begin() as session:
            task = session.get(Task, task_id)
            if not task:
                raise DomainError("任务不存在", 404)
            before = row_dict(task)
            result = session.execute(update(Task).where(Task.id == task_id, Task.version == value.version)
                .values(status=value.status, version=Task.version + 1, updated_at=now()))
            if result.rowcount != 1:
                raise DomainError("任务已变化，请刷新后再修改", 409)
            session.add(ProjectEvent(id=uid("e_"), project_id=task.project_id, kind="task.status_changed",
                                    body={"task_id": task_id, "before": before, "status": value.status}))
            store.audit(session, "task.status_changed", task_id, {"before": before, "status": value.status})
            session.flush()
            session.refresh(task)
            return row_dict(task)

    @app.get("/api/projects/{project_id}/agenda")
    def agenda(project_id: str):
        with sessions() as session:
            if not session.get(Project, project_id):
                raise DomainError("项目不存在", 404)
            open_tasks = session.scalars(select(Task).where(Task.project_id == project_id,
                                                           Task.status.not_in(["done", "cancelled"]))).all()
            return {"title": "下次会议准备", "items": [
                {"task_id": task.id, "text": task.title, "owner": task.owner, "due": task.due,
                 "status": task.status, "conditions": task.conditions} for task in open_tasks]}

    @app.get("/api/audit")
    def audit_log():
        with sessions() as session:
            return [row_dict(row) for row in session.scalars(select(Audit).order_by(Audit.id.desc()).limit(100))]

    dist = Path(__file__).resolve().parents[2] / "frontend/dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="web")
    return app


app = create_app()
