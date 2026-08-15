"""Private sales reports: map existing evidence, confirm once, export fixed content."""
from copy import deepcopy
from datetime import datetime
from typing import Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from pydantic import Field
from sqlalchemy import select

from .database import SalesReport, Submission, row_dict
from .domain import Contract, DomainError, digest, now, uid
from .report_export import pdf_bytes, word_bytes
from .workspace import valid_date

TEMPLATES = [{"id": "weekly", "name": "销售周报", "version": 1},
             {"id": "brief", "name": "会谈简报", "version": 1}]
STATUS = {"todo": "待跟进", "in_progress": "推进中", "waiting": "等待条件", "paused": "已暂停",
          "done": "已完成反馈", "cancelled": "已取消"}


class SourceChoice(Contract):
    kind: Literal["personal", "center"]
    id: str
    revision: int = Field(ge=1)


class ReportInput(Contract):
    template: Literal["weekly", "brief"] = "weekly"
    start: str
    end: str
    sources: list[SourceChoice] = Field(min_length=1, max_length=50)
    parent_id: str | None = None
    request_key: str = Field(min_length=8, max_length=150)


class EntryConfirmation(Contract):
    key: str
    meeting_date: str = ""
    summary: str = Field(max_length=12000)
    next_steps: str = Field(max_length=12000)
    risks: str = Field(max_length=12000)
    note: str = Field(default="", max_length=3000)


class ReportConfirmation(Contract):
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=200)
    entries: list[EntryConfirmation] = Field(min_length=1, max_length=50)
    plan: str = Field(default="", max_length=6000)
    accept_unknown_dates: bool = False


def period(start, end):
    if not start or not end:
        raise DomainError("请选择报告起止日期")
    valid_date(start)
    valid_date(end)
    if start > end:
        raise DomainError("报告结束日期不能早于开始日期")


def apply_edits(body, value, final=False):
    if not value.title.strip():
        raise DomainError("请填写报告名称")
    entries = {e.key: e for e in value.entries}
    if len(entries) != len(value.entries) or set(entries) != {e["key"] for e in body["entries"]}:
        raise DomainError("请完整核对当前报告中的会谈")
    result = deepcopy(body)
    for entry in result["entries"]:
        edit = entries[entry["key"]]
        valid_date(edit.meeting_date)
        original = entry.get("mapped", entry)
        if original["meeting_date"] and edit.meeting_date != original["meeting_date"]:
            raise DomainError("已有实际日期请先更正来源会谈；这里只补未知日期")
        if edit.meeting_date and not result["start"] <= edit.meeting_date <= result["end"]:
            raise DomainError("补充日期不在期间内，请调整报告期间并重新生成")
        if final and not edit.meeting_date and not value.accept_unknown_dates:
            raise DomainError("存在未知日期，请补充或确认将其单列为时间待确认")
        entry["date_supplemented"] = bool(not original["meeting_date"] and edit.meeting_date)
        entry["edits"] = []
        for field in ("summary", "next_steps", "risks", "meeting_date", "note"):
            text = getattr(edit, field).strip()
            if text != original[field]:
                entry["edits"].append({"summary":"会谈摘要", "next_steps":"后续安排", "risks":"风险疑点", "meeting_date":"实际日期", "note":"本人补充"}[field])
            entry[field] = text
    result.update(title=value.title.strip(), plan=value.plan.strip())
    return result


class ReportService:
    def __init__(self, business):
        self.business, self.workspace = business, business.workspace
        self.sessions, self.store = business.sessions, business.store

    @staticmethod
    def sales(user):
        if user["role"] != "sales":
            raise DomainError("个人简报／周报仅销售本人可用，导出不自动上报", 403)

    def owned(self, session, key, user):
        self.sales(user)
        row = session.get(SalesReport, key)
        if not row or row.company_id != user["company_id"] or row.owner_id != user["id"]:
            raise DomainError("个人报告不存在或无权访问", 404)
        return row

    def sources(self, session, user):
        private = self.business.sources(session, user, "personal")
        ids = {s["id"] for s in private}
        center = []
        for source in self.business.sources(session, user):
            original = session.get(Submission, source["id"]).recording_id
            if original not in ids:
                center.append(source)
        return {f"{s['kind']}:{s['id']}": s for s in [*private, *center]}

    def followups(self, session, user, source):
        items = self.business.followups(session, user, "mine")
        aliases = {(source["kind"], source["id"], source["revision"])}
        if source["kind"] == "personal":
            for row in session.scalars(select(Submission).where(Submission.recording_id == source["id"], Submission.deleted.is_(False))):
                if row.snapshot["recording_version"] == source["revision"]:
                    aliases.add(("center", row.id, row.revision))
        return [f for f in items if (f["source_kind"], f["source_id"], f["source_revision"]) in aliases]

    def stamp(self, session, user, source):
        # Keep the original source fingerprint shape across additive API metadata.
        # A saved note still invalidates drafts through the source revision.
        frozen = {k: source[k] for k in ('id', 'revision', 'metadata', 'report', 'evidence', 'at', 'owner', 'kind')}
        return digest({"source": frozen, "followups": self.followups(session, user, source)})

    def entry(self, session, user, key, source):
        report = source["report"]
        followups = self.followups(session, user, source)
        steps = [f"{'个人跟进' if f['source_kind'] == 'personal' else '已上报跟进'}：{f['body']['title']}｜{STATUS.get(f['body']['status'], '待核对')}｜"
                 f"责任人 {f['body'].get('responsible') or '待确认'}｜约定日期 {f['body'].get('due') or '待确认'}"
                 + ("｜条件 " + "；".join(f["body"].get("conditions", [])) if f["body"].get("conditions") else "")
                 + ("｜反馈 " + f["body"]["feedback"] if f["body"].get("feedback") else "") for f in followups]
        risks = [r["text"] for r in report.get("risks", [])]
        risks.extend("待确认：" + q["text"] for q in report.get("questions", []))
        supplements = {}
        notes = (self.workspace.clarifications(session, source["id"]) if source["kind"] == "personal"
                 else source.get("clarifications", []))
        for note in notes:
            for question, answer in note["body"]["answers"].items():
                text = note["body"]["questions"][question]
                identity = (note["body"].get("run_id"), question, text)
                supplements[identity] = {"question": text, "answer": answer, "context":
                    "当前分析的本人补充" if note["body"].get("run_id") and note["body"]["run_id"] == source["run_id"]
                    else "历史补充，适用性待核对"}
        result = {"key": key, "kind": source["kind"], "id": source["id"], "revision": source["revision"],
            "title": source["metadata"]["title"], "customer": source["metadata"].get("customer", ""),
            "meeting_date": source["metadata"].get("meeting_date") or "", "at": source["at"],
            "summary": report["summary"], "next_steps": "\n".join(steps), "risks": "\n".join(risks),
            "note": "", "supplements": list(supplements.values()),
            "mode": report["mode"], "evidence": deepcopy(source["evidence"]),
            "stamp": self.stamp(session, user, source), "edits": []}
        result["mapped"] = {k: result[k] for k in ("summary", "next_steps", "risks", "meeting_date", "note")}
        return result

    def stale(self, session, row, user):
        sources = self.sources(session, user)
        return [e["key"] for e in row.body["entries"] if e["key"] not in sources or
                self.stamp(session, user, sources[e["key"]]) != e["stamp"]]

    def create(self, value, user):
        self.sales(user)
        period(value.start, value.end)
        request_hash = digest(value.model_dump())
        with self.sessions.begin() as session:
            if self.workspace.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            existing = session.scalar(select(SalesReport).where(SalesReport.owner_id == user["id"],
                SalesReport.request_key == value.request_key))
            if existing:
                if existing.request_hash != request_hash:
                    raise DomainError("同一生成请求的内容已变化，请重新生成", 409)
                return row_dict(existing)
            if value.parent_id:
                parent = self.owned(session, value.parent_id, user)
                if not parent.confirmed_at:
                    raise DomainError("草稿可重新生成，只有已确认报告才需要创建后续版本")
                if any(parent.body[k] != getattr(value, k) for k in ("template", "start", "end")):
                    raise DomainError("不同期间或模板请新建报告，不作为旧报告的后续版本")
            template = next(t for t in TEMPLATES if t["id"] == value.template)
            sources = self.sources(session, user)
            entries, selected = [], set()
            for choice in value.sources:
                key = f"{choice.kind}:{choice.id}"
                source = sources.get(key)
                if key in selected:
                    raise DomainError("同一会谈请只选择一次")
                selected.add(key)
                if not source:
                    raise DomainError("会谈已移除、未完成或无权访问，请刷新", 404)
                if source["revision"] != choice.revision:
                    raise DomainError("所选会谈已更新，请刷新", 409)
                day = source["metadata"].get("meeting_date")
                if day and not value.start <= day <= value.end:
                    raise DomainError("所选会谈不在报告期间内")
                entries.append(self.entry(session, user, key, source))
            if value.template == "brief" and len(entries) != 1:
                raise DomainError("会谈简报请选择一场；多场汇总请使用销售周报")
            body = {"title": f"{user['name']} {template['name']}", "author": user["name"],
                "template": template["id"], "template_name": template["name"], "template_version": template["version"],
                "start": value.start, "end": value.end, "entries": entries, "plan": ""}
            row = SalesReport(id=uid("rep_"), owner_id=user["id"], company_id=user["company_id"],
                body=body, parent_id=value.parent_id, request_key=value.request_key, request_hash=request_hash,
                confirmed_at=None)
            session.add(row)
            session.flush()
            self.store.audit(session, "report.draft_created", row.id, {"actor": user["id"], "count": len(entries)})
            return row_dict(row)


def reports_router(service):
    router = APIRouter(prefix="/api/workspace/reports", tags=["private-reports"])
    current_user = Depends(service.workspace.auth.identity)

    @router.get("/templates")
    def templates(user=current_user):
        service.sales(user)
        return TEMPLATES

    @router.get("/sources")
    def sources(start: str, end: str, user=current_user):
        service.sales(user)
        period(start, end)
        with service.sessions() as session:
            rows = [{**{k: s[k] for k in ("id", "kind", "revision", "metadata", "at")}, "key": key}
                    for key, s in service.sources(session, user).items()
                    if not s["metadata"].get("meeting_date") or start <= s["metadata"]["meeting_date"] <= end]
            return sorted(rows, key=lambda r:r["at"], reverse=True)

    @router.get("")
    def reports(user=current_user):
        service.sales(user)
        with service.sessions() as session:
            rows = session.scalars(select(SalesReport).where(SalesReport.owner_id == user["id"],
                SalesReport.company_id == user["company_id"]).order_by(SalesReport.created_at.desc())).all()
            return [{**{k: row_dict(row)[k] for k in ("id", "version", "parent_id", "confirmed_at", "created_at")},
                     "body": {k: row.body[k] for k in ("title", "start", "end", "template")}} for row in rows]

    @router.post("", status_code=201)
    def create(value: ReportInput, user=current_user):
        return service.create(value, user)

    @router.get("/{key}")
    def detail(key: str, user=current_user):
        with service.sessions() as session:
            row = service.owned(session, key, user)
            return {**row_dict(row), "stale_sources": service.stale(session, row, user)}

    @router.post("/{key}/confirm")
    def confirm(key: str, value: ReportConfirmation, user=current_user):
        if not value.title.strip():
            raise DomainError("请填写报告名称")
        with service.sessions.begin() as session:
            if service.workspace.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = service.owned(session, key, user)
            if row.confirmed_at:
                # Identical retry is safe; different edits must create another document.
                if row.body.get("confirmation_hash") == digest(value.model_dump()):
                    return {"id": row.id, "confirmed_at": row.confirmed_at, "content_id": row.body["content_id"]}
                raise DomainError("报告已确认，修改请生成后续版本", 409)
            if row.version != value.version or service.stale(session, row, user):
                raise DomainError("来源或跟进资料已变化，请重新生成后再确认", 409)
            result = apply_edits(row.body, value, final=True)
            row.confirmed_at = now()
            result.update(title=value.title.strip(), plan=value.plan.strip(),
                confirmed_at=datetime.fromisoformat(row.confirmed_at).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S +08:00"))
            result["content_id"] = digest(result)
            result["confirmation_hash"] = digest(value.model_dump())
            row.body, row.version = result, row.version + 1
            service.store.audit(session, "report.confirmed", key, {"actor": user["id"], "content_id": result["content_id"]})
            return {"id": row.id, "confirmed_at": row.confirmed_at, "content_id": result["content_id"]}

    @router.patch("/{key}/draft")
    def save_draft(key: str, value: ReportConfirmation, user=current_user):
        with service.sessions.begin() as session:
            if service.workspace.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = service.owned(session, key, user)
            if row.confirmed_at or row.version != value.version:
                raise DomainError("报告版本已变化或已确认，请刷新", 409)
            if service.stale(session, row, user):
                raise DomainError("来源资料已变化，请重新生成草稿", 409)
            row.body = apply_edits(row.body, value)
            row.version += 1
            service.store.audit(session, "report.draft_saved", key, {"actor": user["id"], "version": row.version})
            return {"version": row.version}

    @router.get("/{key}/export/{format}")
    def export(key: str, format: Literal["docx", "pdf"], user=current_user):
        with service.sessions() as session:
            row = service.owned(session, key, user)
            if not row.confirmed_at:
                raise DomainError("请先核对并确认报告，再导出文件", 409)
            body = deepcopy(row.body)
        try:
            data = word_bytes(body) if format == "docx" else pdf_bytes(body, service.workspace.settings.report_font)
        except (ValueError, OSError) as exc:
            raise DomainError("文件生成失败，请检查服务端字体与导出配置后重试", 503) from exc
        media = "application/pdf" if format == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        filename = f"{body['template']}-{key}.{format}"
        return Response(data, media_type=media, headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "X-Report-Content-ID": body["content_id"]})

    return router
