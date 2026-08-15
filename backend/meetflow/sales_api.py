"""Sales routes registered before the frontend static mount."""
import json
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import FileResponse, Response

from .audio import audio_available
from .audio_storage import audio_file
from .database import SalesConversation
from .domain import DomainError, uid
from .sales_domain import (
    AnalyzeInput,
    ConversationInput,
    PlaybookInput,
    SalesReviewInput,
    SpanEdit,
    template,
)


def sales_router(service, driver):
    router = APIRouter(prefix="/api/sales", tags=["sales-quality"])

    def schedule(key, audio=False):
        if service.settings.start_worker:
            driver.submit(key, audio)

    @router.get("/template")
    def get_template():
        return template()

    @router.get("/playbooks")
    def playbooks(project_id: str):
        return service.playbooks(project_id)

    @router.post("/playbooks", status_code=201)
    def publish(value: PlaybookInput):
        return service.publish(value)

    @router.get("/conversations")
    def conversations(project_id: str):
        return service.conversations(project_id)

    @router.post("/conversations", status_code=201)
    def create_conversation(value: ConversationInput):
        return service.create_conversation(value)

    @router.post("/audio", status_code=201)
    async def import_audio(file: Annotated[UploadFile, File()], metadata: Annotated[str, Form()]):
        if not audio_available(service.settings):
            raise DomainError("录音转写未配置；请安装 audio 依赖并设置 ASR 模型，或先导入已有转写", 422)
        try:
            data = json.loads(metadata)
        except ValueError:
            raise DomainError("会话信息不是有效 JSON") from None
        if not isinstance(data, dict):
            raise DomainError("会话信息必须为对象")
        data.update(transcript="未知：等待录音转写", spans=[], complete=False)
        value = ConversationInput.model_validate(data)
        with service.sessions() as session:
            service.require_project(session, value.project_id)
        extension = Path(file.filename or "").suffix.lower()
        if extension not in {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm"}:
            raise DomainError("请使用 WAV、MP3、M4A、FLAC、OGG 或 WEBM 音频")
        directory = service.settings.data_dir.resolve() / "sales-audio"
        directory.mkdir(exist_ok=True)
        path, size = directory / (uid() + extension), 0
        try:
            with path.open("wb") as stream:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > service.settings.max_upload_mb * 1024 * 1024:
                        raise DomainError("录音超过当前上传上限", 413)
                    stream.write(chunk)
            if not size:
                raise DomainError("录音文件为空")
            result = service.create_conversation(value, audio_path=str(path), status="transcribing")
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
        schedule(result["id"], audio=True)
        return result

    @router.get("/conversations/{key}")
    def conversation(key: str):
        return service.conversation(key)

    @router.patch("/conversations/{key}/spans")
    def edit_spans(key: str, value: SpanEdit):
        return service.edit_spans(key, value)

    @router.get("/conversations/{key}/audio")
    def get_audio(key: str):
        with service.sessions() as session:
            row = session.get(SalesConversation, key)
            if not row or not row.audio_path:
                raise DomainError("该会话没有录音", 404)
            path = audio_file(service.settings.data_dir, row.audio_path, 'sales-audio')
        return FileResponse(path)

    @router.post("/conversations/{key}/analyze", status_code=201)
    def analyze(key: str, value: AnalyzeInput):
        result = service.analyze(key, value)
        schedule(result["id"])
        return result

    @router.get("/analyses/{key}")
    def detail(key: str):
        return service.detail(key)

    @router.post("/analyses/{key}/review")
    def review(key: str, value: SalesReviewInput):
        return service.review(key, value)

    @router.post("/analyses/{key}/retry")
    def retry(key: str):
        row = service.get(key)
        if row["status"] != "failed" or row["cancel_requested"]:
            raise DomainError("仅失败且未取消的分析可恢复", 409)
        if row["model_calls"] >= service.settings.max_model_calls or row["tool_calls"] >= service.settings.max_tool_calls:
            raise DomainError("累计预算已用尽，请重新创建分析", 409)
        service.change(key, status="queued", error=None)
        schedule(key)
        return {"status": "queued"}

    @router.post("/analyses/{key}/cancel")
    def cancel(key: str):
        if service.get(key)["status"] not in {"queued", "running"}:
            raise DomainError("当前阶段无需停止", 409)
        service.change(key, cancel_requested=True)
        return {"status": "stopping"}

    @router.get("/analyses/{key}/export")
    def export(key: str):
        return Response(service.export(key), media_type="text/markdown; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="sales-{key}.md"'})

    @router.get("/dashboard")
    def dashboard(project_id: str, mode: Literal["demo", "live"] = "live", playbook_id: str | None = None,
                  salesperson: str | None = None, stage: str | None = None,
                  start: date | None = None, end: date | None = None):
        if start and end and start > end:
            raise DomainError("开始日期不能晚于结束日期")
        return service.dashboard(project_id, mode, playbook_id, salesperson, stage,
                                 start.isoformat() if start else None, end.isoformat() if end else None)

    @router.post("/demo", status_code=201)
    def demo(project_id: str):
        result = service.demo(project_id)
        schedule(result["analysis"]["id"])
        return result

    return router
