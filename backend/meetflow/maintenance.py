"""Scoped recovery, withdrawal impact and owner-only processing controls."""
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select

from .database import Account, Audit, Correction, Followup, PersonalRecording, QualityCase, Run, Submission
from .domain import Contract, DomainError


class RecoveryInput(Contract):
    version: int = Field(ge=1)
    reason: str = Field(min_length=2, max_length=1000)


class ProcessingInput(Contract):
    version: int = Field(ge=1)


def maintenance_router(workspace):
    router = APIRouter(prefix="/api/workspace/maintenance", tags=["workspace-maintenance"])
    current_user = Depends(workspace.auth.identity)

    def scoped(session, kind, key, user):
        row = session.get(PersonalRecording if kind == "personal" else Submission, key)
        visible = row and row.company_id == user["company_id"] and (
            row.owner_id == user["id"] and user["role"] == "sales" if kind == "personal" else workspace.visible(row, user))
        if not visible:
            raise DomainError("资料不存在或无权访问", 404)
        return row

    @router.get("/trash")
    def trash(kind: Literal["personal", "center"] = "center", user=current_user):
        if kind == "personal" and user["role"] != "sales":
            raise DomainError("个人回收站仅本人可见", 403)
        with workspace.sessions() as session:
            model = PersonalRecording if kind == "personal" else Submission
            rows = session.scalars(select(model).where(model.company_id == user["company_id"], model.deleted.is_(True))).all()
            result = []
            for row in rows:
                if kind == "personal" and row.owner_id != user["id"] or kind == "center" and not workspace.visible(row, user):
                    continue
                last = session.scalar(select(Audit).where(Audit.target_id == row.id,
                    Audit.action == f"workspace.{'personal' if kind == 'personal' else 'center'}_deleted").order_by(Audit.id.desc()))
                result.append({"id": row.id, "kind": kind, "version": row.version if kind == "personal" else row.revision,
                    "metadata": row.body if kind == "personal" else row.snapshot["metadata"], "created_at": row.created_at,
                    "removed_at": last.created_at if last else None, "reason": last.body.get("reason", "本人移除") if last else "历史记录未存说明",
                    "can_restore": kind == "personal" or user["role"] == "manager" and row.team_id == user["team_id"]})
            return sorted(result, key=lambda r:r["removed_at"] or r["created_at"], reverse=True)

    @router.post("/{kind}/{key}/restore")
    def restore(kind: Literal["personal", "center"], key: str, value: RecoveryInput, user=current_user):
        if not value.reason.strip():
            raise DomainError("请填写恢复原因")
        with workspace.sessions.begin() as session:
            if workspace.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = scoped(session, kind, key, user)
            if kind == "center" and (user["role"] != "manager" or row.team_id != user["team_id"]):
                raise DomainError("中心恢复由负责团队主管处理", 403)
            version = row.version if kind == "personal" else row.revision
            if not row.deleted or version != value.version:
                raise DomainError("资料已恢复或版本已变化，请刷新", 409)
            row.deleted = False
            workspace.store.audit(session, "workspace." + kind + "_restored", key,
                {"actor": user["id"], "reason": value.reason})
        workspace.prepare_quality(kind, key)
        return {"restored": True}

    @router.get("/{kind}/{key}/impact")
    def impact(kind: Literal["personal", "center"], key: str, user=current_user):
        with workspace.sessions() as session:
            row = scoped(session, kind, key, user)
            quality = session.scalars(select(QualityCase.id).where(QualityCase.source_kind == kind,
                QualityCase.source_id == key)).all()
            followups = session.scalars(select(Followup.id).where(Followup.source_kind == kind,
                Followup.source_id == key)).all()
            return {"version": row.version if kind == "personal" else row.revision,
                **({"quality_versions": len(quality)} if user["role"] in {"manager","boss"} and kind == "center" else {}),
                "followup_versions": len(followups),
                "notice": "移除后不再参与当前工作台和客户跟进；历史保留，可在回收站恢复。个人副本和已确认报告不被级联删除。"}

    @router.get("/{kind}/{key}/activity")
    def activity(kind: Literal["personal", "center"], key: str, user=current_user):
        with workspace.sessions() as session:
            row = scoped(session, kind, key, user)
            events = [{"action": "上传" if kind == "personal" else "确认上报", "at": row.created_at,
                       "actor": session.get(Account, row.owner_id).name, "reason": ""}]
            actions = {"workspace.personal_edited":"修改个人信息", "workspace.personal_deleted":"移除个人记录",
                "workspace.center_deleted":"撤下中心资料", "workspace.personal_restored":"恢复个人记录",
                "workspace.center_restored":"恢复中心资料", "workspace.clarified":"补充疑点并重算",
                "workspace.transcript_corrected":"修订转写并重算",
                "workspace.correction_requested":"提出中心更正", "workspace.correction_reviewed":"审核中心更正",
                "workspace.retry_requested":"恢复失败处理", "workspace.cancel_requested":"请求停止处理"}
            targets = [key]
            if kind == "center":
                targets.extend(session.scalars(select(Correction.id).where(Correction.submission_id == key)))
            for audit in session.scalars(select(Audit).where(Audit.target_id.in_(targets)).order_by(Audit.id)):
                if audit.action in actions:
                    actor = session.get(Account, audit.body.get("actor", ""))
                    events.append({"action": actions[audit.action], "at": audit.created_at,
                        "actor": actor.name if actor else "历史操作", "reason": audit.body.get("reason", "")})
            if kind == "center":
                for h in row.history:
                    events.append({"action": f"中心 v{h['revision']} 被新版替代", "at": h["at"], "actor": "主管审核", "reason": "旧快照仍保留"})
            return sorted(events, key=lambda e:e["at"])

    @router.post("/personal/{key}/retry")
    def retry(key: str, value: ProcessingInput, user=current_user):
        with workspace.sessions.begin() as session:
            if workspace.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = workspace.private(session, key, user)
            run = session.get(Run, row.run_id)
            if row.version != value.version or run.status != "failed" or run.cancel_requested:
                raise DomainError("只有当前失败且未停止的会谈可以恢复，请刷新", 409)
            if run.model_calls >= workspace.settings.max_model_calls or run.tool_calls >= workspace.settings.max_tool_calls:
                raise DomainError("本次累计调用预算已用尽，不能通过重试清零", 409)
            run.status, run.error, run.phase = "queued", None, "等待恢复处理"
            workspace.store.audit(session, "workspace.retry_requested", key, {"actor": user["id"]})
            run_id = run.id
        if workspace.settings.start_worker:
            workspace.driver.submit(run_id)
        return {"status":"queued", "run_id":run_id}

    @router.post("/personal/{key}/cancel")
    def cancel(key: str, value: ProcessingInput, user=current_user):
        with workspace.sessions.begin() as session:
            row = workspace.private(session, key, user)
            run = session.get(Run, row.run_id)
            if row.version != value.version or run.status not in {"queued", "running"}:
                raise DomainError("当前会谈无需停止，请刷新", 409)
            run.cancel_requested = True
            if run.status == "queued":
                run.status, run.phase = "cancelled", "已停止"
            workspace.store.audit(session, "workspace.cancel_requested", key, {"actor": user["id"]})
        return {"notice": "已请求停止，在途语音识别或模型请求结束后生效，不会自动退回演示模式"}

    return router
