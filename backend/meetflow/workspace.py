"""Owner-private recordings and fixed team/company evidence submissions."""
import copy
import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .audio import audio_available
from .database import (
    Account,
    Audit,
    Correction,
    Meeting,
    MeetingClarification,
    MeetingContact,
    PersonalRecording,
    Run,
    Submission,
    Team,
    UploadReceipt,
    row_dict,
)
from .domain import Contract, DomainError, SourceInput, digest, now, uid
from .tools import normalize_date


class RecordingInput(Contract):
    title: str = Field(default="", max_length=200)
    customer: str = Field(default="", max_length=400)
    meeting_date: str = ""
    opportunity_id: str | None = None
    request_key: str | None = Field(default=None, min_length=8, max_length=150)
    transcript: str = Field(min_length=2, max_length=200000)


class RecordingEdit(Contract):
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    customer: str = Field(default="", max_length=400)
    meeting_date: str = ""
    opportunity_id: str | None = None


class SubmitInput(Contract):
    version: int = Field(ge=1)
    customer: str = Field(default="", max_length=400)
    opportunity_id: str | None = None
    request_key: str = Field(min_length=8, max_length=150)


class BulkSubmission(SubmitInput):
    recording_id: str


class BulkSubmitInput(Contract):
    items: list[BulkSubmission] = Field(min_length=1, max_length=30)


class CorrectionInput(Contract):
    base_revision: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    customer: str = Field(min_length=1, max_length=400)
    meeting_date: str = ""
    reason: str = Field(min_length=1, max_length=1000)
    opportunity_id: str | None = None
    use_personal_revision: bool = False


class DecisionInput(Contract):
    approve: bool


class ClarificationInput(Contract):
    version: int = Field(ge=1)
    answers: dict[str, str]


class DeleteInput(Contract):
    reason: str = Field(min_length=1, max_length=1000)
    revision: int | None = Field(default=None, ge=1)


class TranscriptEdit(Contract):
    version: int = Field(ge=1)
    text: str = Field(min_length=2, max_length=200000)
    reason: str = Field(min_length=2, max_length=1000)


def transcript_text(run):
    return "\n".join(span["text"] for source in run["manifest"]["sources"] if source["kind"] == "transcript"
                     for span in source["body"]["spans"])


def valid_date(value):
    if value:
        try:
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError
        except ValueError:
            raise DomainError("会议日期格式应为 YYYY-MM-DD") from None
    return value


def rebase_report(report, evidence, meeting_date):
    result = copy.deepcopy(report)
    for action in result.get("actions", []):
        text = " ".join(evidence.get(ref, {}).get("text", "") for ref in action.get("evidence", []))
        match = re.search(r"(?:下下|下|本|这)?(?:周|星期)[一二三四五六日天]|后天|明天|今天", text)
        if match:
            action["due"] = None if re.search("候选|不定|再定|待确认", text) else normalize_date(match.group(), meeting_date, "Asia/Shanghai")["date"]
    return result


class WorkspaceService:
    def __init__(self, store, driver, auth):
        self.store, self.driver, self.auth = store, driver, auth
        self.sessions, self.settings = store.sessions, store.settings
        self.quality = None
        self.business = None

    def prepare_quality(self, kind, key):
        for service in (self.quality, self.business):
            if service:
                try:
                    service.ensure(kind, key)
                except DomainError as exc:
                    with self.sessions.begin() as session:
                        self.store.audit(session, "derived.prepare_pending", key, {"message": exc.message})

    def index_existing_uploads(self):
        """Add owner-only fingerprints for old audio without changing any source or run."""
        with self.sessions.begin() as session:
            indexed = set(session.scalars(select(UploadReceipt.recording_id)))
            hashes = {(r.owner_id,r.content_hash) for r in session.scalars(select(UploadReceipt)) if r.content_hash}
            for row in session.scalars(select(PersonalRecording).order_by(PersonalRecording.deleted, PersonalRecording.created_at)):
                if row.id in indexed:
                    continue
                run = session.get(Run,row.run_id)
                meeting = session.get(Meeting,run.meeting_id)
                path = Path(meeting.audio_path or "").resolve()
                if not meeting.audio_path or not path.is_relative_to(self.settings.data_dir.resolve()/"audio") or not path.is_file():
                    continue
                try:
                    with path.open('rb') as stream:
                        checksum = "audio:" + hashlib.file_digest(stream,"sha256").hexdigest()
                except OSError:
                    self.store.audit(session,"workspace.upload_index_unavailable",row.id)
                    continue
                if (row.owner_id,checksum) not in hashes:
                    session.add(UploadReceipt(id=uid('upload_'),owner_id=row.owner_id, request_key='existing-'+row.id,
                        request_hash=digest({'existing':row.id}),content_hash=checksum,recording_id=row.id))
                    hashes.add((row.owner_id,checksum))

    def private(self, session, key, user):
        row = session.get(PersonalRecording, key)
        if not row or row.deleted or row.owner_id != user["id"] or row.company_id != user["company_id"]:
            raise DomainError("个人录音不存在或无权访问", 404)
        return row

    @staticmethod
    def visible(row, user):
        if row.company_id != user["company_id"]:
            return False
        return (user["role"] in {"admin", "boss"} or
                user["role"] == "manager" and row.team_id == user["team_id"] or
                user["role"] == "sales" and row.owner_id == user["id"])

    def central(self, session, key, user):
        row = session.get(Submission, key)
        if not row or row.deleted or not self.visible(row, user):
            raise DomainError("中心记录不存在或无权访问", 404)
        return row

    def recording_dict(self, row, run=None):
        run = run or self.store.get_run(row.run_id)
        return {"id": row.id, "body": row.body, "version": row.version, "uploaded_at": row.created_at,
                "run_id": row.run_id, "status": run["status"], "phase": run["phase"], "error": run["error"],
                "cancel_requested": run["cancel_requested"]}

    @staticmethod
    def clarifications(session, key):
        return [row_dict(row) for row in session.scalars(select(MeetingClarification).where(
            MeetingClarification.recording_id == key).order_by(MeetingClarification.created_at))]

    def create(self, user, value, audio_path=None, content_hash=None):
        if user["role"] != "sales":
            raise DomainError("请使用销售账号上传到个人区", 403)
        valid_date(value.meeting_date)
        title = value.title.strip() or datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%m月%d日 %H:%M 上传的会议录音")
        request_hash = digest({**value.model_dump(exclude={"request_key"}), "content_hash": content_hash})
        with self.sessions.begin() as session:
            if self.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            prior = session.scalar(select(UploadReceipt).where(UploadReceipt.owner_id == user["id"],
                UploadReceipt.request_key == value.request_key)) if value.request_key else None
            if prior and prior.request_hash != request_hash:
                raise DomainError("同一上传请求对应其他内容，请重新选择后提交", 409)
            duplicate = prior or (session.scalar(select(UploadReceipt).where(UploadReceipt.owner_id == user["id"],
                UploadReceipt.content_hash == content_hash)) if content_hash else None)
            if duplicate:
                existing = session.get(PersonalRecording, duplicate.recording_id)
                if existing and existing.deleted and existing.owner_id == user["id"]:
                    raise DomainError("该资料已在个人回收站，请先恢复原会谈，不重复创建", 409)
                row = self.private(session, duplicate.recording_id, user)
                return {**self.recording_dict(row), "duplicate": True,
                    "notice": "该资料已保存，已打开原会谈，不重复转写或创建。新填信息未覆盖原记录，需要时请修改个人信息。"}
            account = session.get(Account, user["id"])
            if value.opportunity_id:
                value.customer = self.business.label(session, value.opportunity_id, user)
            meeting = Meeting(id=uid("m_"), project_id=account.project_id, title=title,
                              timezone="Asia/Shanghai", audio_path=audio_path)
            session.add(meeting)
            session.flush()
            session.add(MeetingContact(meeting_id=meeting.id, salesperson=user["name"], customer=value.customer))
            if not audio_path:
                self.store.add_source(session, account.project_id, SourceInput(title=title, kind="transcript",
                    text=value.transcript), meeting.id)
            session.flush()
            run = self.store.create_run(session, meeting, "live" if self.settings.live_available else "demo")
            run.manifest = {**run.manifest, "meeting_date": value.meeting_date or None}
            row = PersonalRecording(id=uid("rec_"), owner_id=user["id"], company_id=user["company_id"],
                team_id=user["team_id"], run_id=run.id,
                body={"title": title, "customer": value.customer, "meeting_date": value.meeting_date, "opportunity_id": value.opportunity_id})
            session.add(row)
            session.flush()
            if value.request_key or content_hash:
                session.add(UploadReceipt(id=uid("upload_"), owner_id=user["id"],
                    request_key=value.request_key or uid(), request_hash=request_hash,
                    content_hash=content_hash, recording_id=row.id))
            self.store.audit(session, "workspace.recording_created", row.id, {"owner": user["id"]})
            self.store.audit(session, "meeting.created", meeting.id, {"run_id": run.id, "mode": run.mode})
            output = self.recording_dict(row, row_dict(run))
        if self.settings.start_worker:
            self.driver.submit(row.run_id)
        return output

    def submit(self, user, key, value):
        if not value.customer.strip() and not value.opportunity_id:
            raise DomainError("上报前请选择客户／商机")
        request_hash = digest({"recording": key, **value.model_dump()})
        try:
            with self.sessions.begin() as session:
                if self.settings.db_url.startswith("sqlite"):
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                prior = session.scalar(select(Submission).where(Submission.request_key == value.request_key))
                if prior:
                    if prior.owner_id != user["id"] or prior.request_hash != request_hash:
                        raise DomainError("重复请求标识对应其他内容", 409)
                    return {"id": prior.id, "revision": prior.revision}
                row = self.private(session, key, user)
                if row.version != value.version:
                    raise DomainError("个人记录已变化，请刷新后上报", 409)
                opportunity_id = value.opportunity_id or row.body.get("opportunity_id")
                if not opportunity_id:
                    raise DomainError("上报前请选择或新建客户／商机")
                label = self.business.label(session, opportunity_id, user)
                if session.scalar(select(Submission.id).where(Submission.recording_id == key)):
                    raise DomainError("已上报；如需修改中心记录请申请更正", 409)
                report = self.store.latest_report(row.run_id)
                run = self.store.get_run(row.run_id)
                if not report or run["status"] not in {"waiting_review", "completed"}:
                    raise DomainError("请等待整理完成后上报", 409)
                meeting = session.get(Meeting, run["meeting_id"])
                snapshot = {"metadata": {**row.body, "customer": label, "opportunity_id": opportunity_id},
                    "owner_name": user["name"], "report": report["body"],
                    "evidence": self.store.source_map(row.run_id), "run_id": row.run_id,
                    "audio_path": meeting.audio_path, "recording_version": row.version}
                if self.quality:
                    snapshot["quality_context"] = self.quality.freeze(session, "personal", key, row.version)
                snapshot["clarifications"] = self.clarifications(session, key)
                result = Submission(id=uid("sub_"), recording_id=key, owner_id=user["id"],
                    company_id=user["company_id"], team_id=row.team_id, snapshot=snapshot,
                    request_key=value.request_key, request_hash=request_hash)
                session.add(result)
                session.flush()
                self.store.audit(session, "workspace.submitted", result.id,
                                 {"actor": user["id"], "recording_id": key})
                output = {"id": result.id, "revision": result.revision}
            self.prepare_quality("center", output["id"])
            return output
        except IntegrityError:
            raise DomainError("已上报或请求冲突，请刷新查看", 409) from None

    @staticmethod
    def central_dict(row, detail=False):
        result = {"id": row.id, "recording_id": row.recording_id, "revision": row.revision,
                  "team_id": row.team_id, "owner_id": row.owner_id, "owner": row.snapshot["owner_name"],
                  "metadata": row.snapshot["metadata"], "submitted_at": row.created_at}
        if detail:
            result.update(report=row.snapshot["report"], evidence=row.snapshot["evidence"],
                          clarifications=row.snapshot.get("clarifications", []),
                          has_audio=bool(row.snapshot.get("audio_path")),
                          history=[{"revision": h["revision"], "metadata": h["snapshot"]["metadata"],
                                    "at": h["at"]} for h in row.history])
        return result

    def audio(self, path):
        path = Path(path or "").resolve()
        if not path.is_relative_to(self.settings.data_dir.resolve() / "audio") or not path.is_file():
            raise DomainError("录音不可用", 404)
        return FileResponse(path)


def workspace_router(service):
    router = APIRouter(prefix="/api/workspace", tags=["workspace"])
    current_user = Depends(service.auth.identity)

    @router.get("/recordings")
    def recordings(user=current_user):
        with service.sessions() as session:
            rows = session.scalars(select(PersonalRecording).where(PersonalRecording.owner_id == user["id"],
                PersonalRecording.company_id == user["company_id"], PersonalRecording.deleted.is_(False))
                .order_by(PersonalRecording.created_at.desc())).all()
            return [service.recording_dict(row) for row in rows]

    @router.post("/recordings", status_code=201)
    def create(value: RecordingInput, user=current_user):
        return service.create(user, value)

    @router.post("/submissions/batch")
    def bulk_submit(value: BulkSubmitInput, user=current_user):
        if user["role"] != "sales":
            raise DomainError("仅销售本人可上报个人会谈",403)
        if len({i.recording_id for i in value.items}) != len(value.items):
            raise DomainError("同一批次不能重复选择会谈")
        succeeded,failed=[],[]
        for item in value.items:
            try:
                result=service.submit(user,item.recording_id,SubmitInput.model_validate(item.model_dump(exclude={'recording_id'})))
                succeeded.append({'recording_id':item.recording_id,**result})
            except DomainError as exc:
                failed.append({'recording_id':item.recording_id,'message':exc.message,'status':exc.status})
        return {'succeeded':succeeded,'failed':failed,'notice':'逐场保存，上报成功的资料已进入中心，失败的可补充后单独重试。'}

    @router.post("/audio", status_code=201)
    async def audio_upload(file: Annotated[UploadFile, File()], metadata: Annotated[str, Form()] = "{}",
                           user=current_user):
        if user["role"] != "sales":
            raise DomainError("请使用销售账号上传", 403)
        if not audio_available(service.settings):
            raise DomainError("ASR 未配置，可先导入已有转写", 422)
        try:
            value = RecordingInput.model_validate({**json.loads(metadata), "transcript": "等待真实转写"})
        except (ValueError, TypeError):
            raise DomainError("会谈信息格式错误") from None
        valid_date(value.meeting_date)
        extension = Path(file.filename or "").suffix.lower()
        if extension not in {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm"}:
            raise DomainError("录音格式不支持", 422)
        directory = service.settings.data_dir.resolve() / "audio"
        directory.mkdir(exist_ok=True)
        path, size, checksum = directory / (uid("audio_") + extension), 0, hashlib.sha256()
        try:
            with path.open("wb") as destination:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > service.settings.max_upload_mb * 1024 * 1024:
                        raise DomainError("录音超过上传上限", 413)
                    destination.write(chunk)
                    checksum.update(chunk)
            if not size:
                raise DomainError("录音为空", 422)
            result = service.create(user, value, audio_path=str(path), content_hash="audio:" + checksum.hexdigest())
            if result.get("duplicate"):
                path.unlink(missing_ok=True)
            return result
        except Exception:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()

    @router.get("/recordings/{key}")
    def detail(key: str, user=current_user):
        with service.sessions() as session:
            row = service.private(session, key, user)
            run = service.store.get_run(row.run_id)
            meeting = session.get(Meeting, run["meeting_id"])
            submitted = session.scalar(select(Submission).where(Submission.recording_id == key))
            return {**service.recording_dict(row), "report": service.store.latest_report(row.run_id),
                    "evidence": service.store.source_map(row.run_id), "has_audio": bool(meeting.audio_path),
                    "clarifications": service.clarifications(session, key),
                    "transcript_text": transcript_text(run),
                    "transcript_history": [{"at": a.created_at, "reason": a.body["reason"],
                        "text": transcript_text(service.store.get_run(a.body["before_run_id"]))}
                        for a in session.scalars(select(Audit).where(Audit.target_id == key,
                            Audit.action == "workspace.transcript_corrected").order_by(Audit.id.desc()))],
                    "submission": {"id": submitted.id, "deleted": submitted.deleted} if submitted else None}

    @router.post("/recordings/{key}/transcript", status_code=201)
    def correct_transcript(key: str, value: TranscriptEdit, user=current_user):
        if not value.text.strip() or not value.reason.strip():
            raise DomainError("请填写修订文字和原因")
        with service.sessions.begin() as session:
            if service.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = service.private(session, key, user)
            run = service.store.get_run(row.run_id)
            if row.version != value.version or run["status"] not in {"waiting_review", "completed", "failed", "cancelled"}:
                raise DomainError("会谈已变化或还在处理，请刷新", 409)
            if transcript_text(run).strip() == value.text.strip():
                raise DomainError("转写文字没有变化，无需重复分析", 409)
            previous = max((s["version"] for s in run["manifest"]["sources"] if s["kind"] == "transcript"), default=0)
            source = service.store.add_source(session, run["project_id"], SourceInput(title="销售修订转写（未重新对齐音频）",
                kind="transcript", text=value.text), run["meeting_id"])
            source.version = previous + 1
            session.flush()
            next_run = service.store.create_run(session, session.get(Meeting, run["meeting_id"]), run["mode"])
            next_run.manifest = {**next_run.manifest, "meeting_date": row.body.get("meeting_date") or None}
            service.store.audit(session, "workspace.transcript_corrected", key, {"actor": user["id"],
                "before_run_id": row.run_id, "after_run_id": next_run.id, "reason": value.reason})
            row.run_id, row.version = next_run.id, row.version + 1
            run_id = next_run.id
        if service.settings.start_worker:
            service.driver.submit(run_id)
        return {"version": row.version, "run_id": run_id}

    @router.post("/recordings/{key}/clarifications", status_code=201)
    def clarify(key: str, value: ClarificationInput, user=current_user):
        if not value.answers or len(value.answers) > 30 or any(not answer.strip() or len(answer) > 2000 for answer in value.answers.values()):
            raise DomainError("请填写 1—30 项补充，每项不超过 2000 字")
        with service.sessions.begin() as session:
            if service.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = service.private(session, key, user)
            run = service.store.get_run(row.run_id)
            if row.version != value.version or run["status"] not in {"waiting_review", "completed"}:
                raise DomainError("会谈已变化或仍在处理，请刷新", 409)
            report = service.store.latest_report(row.run_id)
            questions = {q["key"]: q["text"] for q in report["body"]["questions"]} if report else {}
            if not set(value.answers) <= set(questions):
                raise DomainError("补充问题不属于当前会谈")
            latest = {}
            for note in service.clarifications(session, key):
                latest.update(note["body"]["answers"])
            answers = {k: v.strip() for k, v in value.answers.items() if v.strip() != latest.get(k)}
            if not answers:
                raise DomainError("这些补充已保存，无需重复提交", 409)
            text = "销售本人补充，未经客户确认；不得将单方补充写成双方已确认事实。\n" + "\n".join(
                f"问题：{questions[k]}\n销售补充：{v}" for k, v in answers.items())
            service.store.add_source(session, run["project_id"], SourceInput(
                title="销售补充（未经客户确认）", kind="requirement", text=text), run["meeting_id"])
            next_run = service.store.create_run(session, session.get(Meeting, run["meeting_id"]), run["mode"])
            next_run.manifest = {**next_run.manifest, "meeting_date": row.body.get("meeting_date") or None}
            row.run_id, row.version = next_run.id, row.version + 1
            session.add(MeetingClarification(id=uid("mc_"), recording_id=key, actor_id=user["id"],
                source_version=row.version, body={"answers": answers, "questions": {k: questions[k] for k in answers}}))
            service.store.audit(session, "workspace.clarified", key, {"actor": user["id"], "version": row.version})
            run_id = next_run.id
        if service.settings.start_worker:
            service.driver.submit(run_id)
        return {"version": row.version, "run_id": run_id}

    @router.patch("/recordings/{key}")
    def edit(key: str, value: RecordingEdit, user=current_user):
        valid_date(value.meeting_date)
        if not value.title.strip():
            raise DomainError("录音名称不能为空")
        with service.sessions.begin() as session:
            if service.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = service.private(session, key, user)
            if row.version != value.version:
                raise DomainError("记录已变化，请刷新", 409)
            body = {**row.body, **value.model_dump(exclude={"version", "opportunity_id"})}
            opportunity_id = value.opportunity_id if "opportunity_id" in value.model_fields_set else row.body.get("opportunity_id")
            body["opportunity_id"] = opportunity_id
            if opportunity_id:
                body["customer"] = service.business.label(session, opportunity_id, user)
            rerun = None
            if value.meeting_date != row.body.get("meeting_date", ""):
                run = service.store.get_run(row.run_id)
                if run["status"] not in {"waiting_review", "completed", "failed"}:
                    raise DomainError("请等待整理结束后更正会谈日期", 409)
                next_run = service.store.create_run(session, session.get(Meeting, run["meeting_id"]), run["mode"])
                next_run.manifest = {**next_run.manifest, "meeting_date": value.meeting_date or None}
                row.run_id, rerun = next_run.id, next_run.id
            changed = session.execute(update(PersonalRecording).where(PersonalRecording.id == row.id,
                PersonalRecording.version == value.version).values(
                    body=body, version=value.version + 1))
            if changed.rowcount != 1:
                raise DomainError("记录已变化，请刷新", 409)
            service.store.audit(session, "workspace.personal_edited", key, {"actor": user["id"]})
        if rerun and service.settings.start_worker:
            service.driver.submit(rerun)
        else:
            service.prepare_quality("personal", key)
        return {"version": value.version + 1}

    @router.delete("/recordings/{key}")
    def delete(key: str, user=current_user):
        with service.sessions.begin() as session:
            row = service.private(session, key, user)
            row.deleted = True
            service.store.audit(session, "workspace.personal_deleted", key, {"actor": user["id"]})
        return {"deleted": True, "notice": "个人记录已移除，中心上报证据不受影响"}

    @router.get("/recordings/{key}/audio")
    def private_audio(key: str, user=current_user):
        with service.sessions() as session:
            row = service.private(session, key, user)
            run = service.store.get_run(row.run_id)
            path = session.get(Meeting, run["meeting_id"]).audio_path
        return service.audio(path)

    @router.post("/recordings/{key}/submit", status_code=201)
    def submit(key: str, value: SubmitInput, user=current_user):
        return service.submit(user, key, value)

    @router.get("/center")
    def center(user=current_user, q: str = ""):
        with service.sessions() as session:
            rows = session.scalars(select(Submission).where(Submission.company_id == user["company_id"],
                Submission.deleted.is_(False)).order_by(Submission.created_at.desc())).all()
            return [{**service.central_dict(row), "team": session.get(Team, row.team_id).name} for row in rows if service.visible(row, user)
                    and q.lower() in (row.snapshot["metadata"]["title"] + row.snapshot["metadata"]["customer"]).lower()]

    @router.get("/center/{key}")
    def center_detail(key: str, user=current_user):
        with service.sessions() as session:
            return service.central_dict(service.central(session, key, user), detail=True)

    @router.get("/center/{key}/audio")
    def center_audio(key: str, user=current_user):
        with service.sessions() as session:
            path = service.central(session, key, user).snapshot.get("audio_path")
        return service.audio(path)

    @router.get("/center/{key}/versions/{revision}")
    def old_version(key: str, revision: int, user=current_user):
        with service.sessions() as session:
            row = service.central(session, key, user)
            snapshot = row.snapshot if revision == row.revision else next((h["snapshot"] for h in row.history if h["revision"] == revision), None)
            if not snapshot:
                raise DomainError("中心版本不存在", 404)
            return {"revision": revision, "metadata": snapshot["metadata"], "report": snapshot["report"], "evidence": snapshot["evidence"]}

    @router.get("/center/{key}/export")
    def export(key: str, user=current_user):
        with service.sessions() as session:
            row = service.central(session, key, user)
            lines = ["# " + row.snapshot["metadata"]["title"], f"中心版本：{row.revision}",
                     "分析模式：" + row.snapshot["report"]["mode"], row.snapshot["report"]["summary"], "## 原文依据"]
            lines.extend("- " + s["text"] for s in row.snapshot["evidence"].values())
        return Response("\n\n".join(lines), media_type="text/markdown; charset=utf-8")

    @router.post("/center/{key}/delete")
    def remove_center(key: str, value: DeleteInput, user=current_user):
        with service.sessions.begin() as session:
            row = service.central(session, key, user)
            if user["role"] != "manager" or row.team_id != user["team_id"]:
                raise DomainError("中心删除需由负责团队的主管处理", 403)
            if not value.reason.strip():
                raise DomainError("请填写撤下原因")
            if value.revision is not None and value.revision != row.revision:
                raise DomainError("中心版本已变化，请刷新后确认影响", 409)
            row.deleted = True
            service.store.audit(session, "workspace.center_deleted", key,
                                {"actor": user["id"], "reason": value.reason})
        return {"deleted": True, "notice": "中心记录已撤下，历史与个人副本保留"}

    @router.post("/center/{key}/corrections", status_code=201)
    def correction(key: str, value: CorrectionInput, user=current_user):
        valid_date(value.meeting_date)
        if not all(item.strip() for item in (value.title, value.customer, value.reason)):
            raise DomainError("更正名称、客户及原因不能为空")
        with service.sessions.begin() as session:
            row = service.central(session, key, user)
            if row.owner_id != user["id"] or user["role"] != "sales":
                raise DomainError("仅来源销售可以提出更正", 403)
            if row.revision != value.base_revision:
                raise DomainError("中心已更新，请刷新后提出更正", 409)
            proposed = value.model_dump(exclude={"base_revision", "reason", "use_personal_revision"})
            opportunity_id = value.opportunity_id or row.snapshot["metadata"].get("opportunity_id")
            proposed["opportunity_id"] = opportunity_id
            if opportunity_id:
                proposed["customer"] = service.business.label(session, opportunity_id, user)
            if value.use_personal_revision:
                personal = service.private(session, row.recording_id, user)
                run = service.store.get_run(personal.run_id)
                report = service.store.latest_report(personal.run_id)
                if not report or run["status"] not in {"waiting_review", "completed"}:
                    raise DomainError("请先完成个人新版整理再申请中心更正", 409)
                proposed["replacement"] = {"report": report["body"], "evidence": service.store.source_map(personal.run_id),
                    "run_id": personal.run_id, "recording_version": personal.version,
                    "quality_context": service.quality.freeze(session,"personal", personal.id,personal.version) if service.quality else {},
                    "clarifications": service.clarifications(session, personal.id)}
            request = Correction(id=uid("cor_"), submission_id=key, requester_id=user["id"],
                base_revision=value.base_revision, proposed=proposed,
                reason=value.reason)
            session.add(request)
            session.flush()
            service.store.audit(session, "workspace.correction_requested", request.id,
                {"actor": user["id"], "reason":value.reason})
            return {"id": request.id}

    @router.get("/corrections")
    def corrections(user=current_user):
        with service.sessions() as session:
            rows = session.execute(select(Correction, Submission).join(
                Submission, Correction.submission_id == Submission.id).where(
                    Submission.company_id == user["company_id"], Submission.deleted.is_(False))).all()
            result = []
            for c, s in rows:
                if not service.visible(s, user):
                    continue
                before = next((h["snapshot"] for h in s.history if h["revision"] == c.base_revision), s.snapshot)
                result.append({**row_dict(c), "before": before["metadata"], "before_summary": before["report"]["summary"],
                    "before_evidence": before["evidence"]})
            return result

    @router.post("/corrections/{key}/review")
    def review(key: str, value: DecisionInput, user=current_user):
        with service.sessions.begin() as session:
            if service.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            request = session.get(Correction, key)
            if not request:
                raise DomainError("更正申请不存在", 404)
            row = service.central(session, request.submission_id, user)
            if user["role"] != "manager" or row.team_id != user["team_id"]:
                raise DomainError("更正需由负责团队的主管确认", 403)
            if request.status != "pending":
                raise DomainError("申请已处理", 409)
            if request.base_revision != row.revision:
                raise DomainError("中心版本已变化，请重新申请", 409)
            if value.approve:
                row.history = [*row.history, {"revision": row.revision, "snapshot": row.snapshot, "at": now()}]
                context = service.quality.freeze(session, "center", row.id, row.revision) if service.quality else {}
                metadata = {k:v for k,v in request.proposed.items() if k != "replacement"}
                base = {**row.snapshot, **request.proposed.get("replacement", {})}
                row.snapshot = {**base, "metadata": metadata,
                                "quality_context": base.get("quality_context", {}) if "replacement" in request.proposed else context or base.get("quality_context", {}),
                                "report": rebase_report(base["report"], base["evidence"], metadata["meeting_date"])}
                row.revision += 1
            request.status = "approved" if value.approve else "rejected"
            request.reviewed_by = user["id"]
            service.store.audit(session, "workspace.correction_reviewed", key,
                                {"actor": user["id"], "approve": value.approve})
        if value.approve:
            service.prepare_quality("center", row.id)
        return {"status": request.status, "revision": row.revision}

    return router
