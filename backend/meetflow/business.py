"""Evidence-bound customer opportunities, follow-up feedback and role digests."""
from datetime import datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import select

from .database import Account, Customer, Followup, Opportunity, PersonalRecording, Submission, row_dict
from .domain import Contract, DomainError, now, uid
from .workspace import valid_date


class OpportunityInput(Contract):
    customer_id: str | None = None
    customer_name: str = Field(default="", max_length=150)
    title: str = Field(min_length=1, max_length=150)


class StageInput(Contract):
    version: int = Field(ge=1)
    stage: Literal["需求沟通", "产品演示", "方案报价", "决策签约", "已签约", "已终止"]
    source_id: str
    reason: str = Field(min_length=2, max_length=1500)


class FollowupInput(Contract):
    version: int = Field(ge=1)
    status: Literal["todo", "in_progress", "waiting", "paused", "done", "cancelled"]
    due: str | None = None
    note: str = Field(min_length=2, max_length=1500)
    supersedes_id: str | None = None
    responsible: str | None = Field(default=None, max_length=100)


class BusinessService:
    def __init__(self, workspace):
        self.workspace, self.store, self.sessions = workspace, workspace.store, workspace.sessions

    def sources(self, session, user, scope="center"):
        if scope == "personal":
            if user["role"] != "sales":
                raise DomainError("个人资料仅本人可见", 403)
            rows = session.scalars(select(PersonalRecording).where(PersonalRecording.owner_id == user["id"],
                PersonalRecording.company_id == user["company_id"], PersonalRecording.deleted.is_(False))).all()
            results = []
            for row in rows:
                report = self.store.latest_report(row.run_id)
                if report and self.store.get_run(row.run_id)["status"] in {"waiting_review", "completed"}:
                    results.append({"id": row.id, "revision": row.version, "metadata": row.body,
                        "report": report["body"], "evidence": self.store.source_map(row.run_id),
                        "at": row.created_at, "owner": user["name"], "kind": scope})
            return results
        rows = session.scalars(select(Submission).where(Submission.company_id == user["company_id"],
                                Submission.deleted.is_(False))).all()
        return [{"id": row.id, "revision": row.revision, "metadata": row.snapshot["metadata"],
                 "report": row.snapshot["report"], "evidence": row.snapshot["evidence"],
                 "at": row.created_at, "owner": row.snapshot["owner_name"], "kind": "center"}
                for row in rows if self.workspace.visible(row, user)]

    def opportunity(self, session, key, user, private=False):
        row = session.get(Opportunity, key)
        if not row or row.company_id != user["company_id"]:
            raise DomainError("商机不存在或无权访问", 404)
        if user["role"] == "sales" and row.owner_id == user["id"]:
            return row
        if not private and any(s["metadata"].get("opportunity_id") == key for s in self.sources(session, user)):
            return row
        raise DomainError("商机不存在或无权访问", 404)

    def label(self, session, key, user):
        row = self.opportunity(session, key, user, private=True)
        customer = session.get(Customer, row.customer_id)
        return f"{customer.name} / {row.title}"

    def opportunity_dict(self, session, row, user):
        visible = {s["id"]: s["revision"] for s in self.sources(session, user)
                   if s["metadata"].get("opportunity_id") == row.id}
        history = [{**h, "source_available": h["source_id"] in visible,
                    "reason": h["reason"] if h["source_id"] in visible else "依据来源已撤下或不再属于当前商机，内容暂不展示。"}
                   for h in row.history]
        latest = row.history[-1] if row.history else None
        basis = "依据来源待核对" if latest and visible.get(latest["source_id"]) != latest["source_revision"] else "人工确认" if latest else "待确认"
        return {**row_dict(row), "history": history, "stage_basis": basis,
                "customer": session.get(Customer, row.customer_id).name,
                "owner": session.get(Account, row.owner_id).name}

    def ensure(self, kind, key):
        with self.sessions.begin() as session:
            if self.workspace.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = session.get(PersonalRecording if kind == "personal" else Submission, key)
            if not row or row.deleted:
                return
            if kind == "personal":
                report = self.store.latest_report(row.run_id)
                if not report or self.store.get_run(row.run_id)["status"] not in {"waiting_review", "completed"}:
                    return
                revision, body = row.version, report["body"]
            else:
                revision, body = row.revision, row.snapshot["report"]
            for action in body.get("actions", []):
                scope_key = f"{kind}:{key}:v{revision}:{action['id']}"
                if session.scalar(select(Followup.id).where(Followup.scope_key == scope_key)):
                    continue
                previous = next((item for item in session.scalars(select(Followup).where(
                    Followup.source_kind == kind, Followup.source_id == key, Followup.source_revision < revision)
                    .order_by(Followup.source_revision.desc())) if item.body["title"] == action["title"]), None)
                carry = {k: previous.body[k] for k in ("status", "feedback", "manual_due", "manual_responsible") if k in previous.body} if previous and previous.body.get("feedback") else {}
                if previous and previous.body.get("manual_due"):
                    carry["due"] = previous.body.get("due")
                if previous and previous.body.get("manual_responsible"):
                    carry["responsible"] = previous.body.get("responsible")
                session.add(Followup(id=uid("fu_"), scope_key=scope_key, company_id=row.company_id,
                    owner_id=row.owner_id, team_id=row.team_id, source_kind=kind, source_id=key,
                    source_revision=revision, body={"title": action["title"], "responsible": action.get("owner"),
                        "due": action.get("due"), "status": "waiting" if action.get("status") == "blocked" else action.get("status", "todo"),
                        "conditions": action.get("conditions", []),
                        "evidence": action.get("evidence", []), "origin": "自动提取，未确认履约状态", **carry},
                    history=[{"previous_id": previous.id, "source_revision": previous.source_revision,
                              "note": "来源修订后保留同名事项的历史，请核对", "history": previous.history}] if previous else []))

    def on_run_ready(self, run_id):
        with self.sessions() as session:
            key = session.scalar(select(PersonalRecording.id).where(PersonalRecording.run_id == run_id))
        if key:
            self.ensure("personal", key)

    def recover(self):
        with self.sessions() as session:
            private = session.scalars(select(PersonalRecording.id).where(PersonalRecording.deleted.is_(False))).all()
            center = session.scalars(select(Submission.id).where(Submission.deleted.is_(False))).all()
        for key in private:
            self.ensure("personal", key)
        for key in center:
            self.ensure("center", key)

    def followups(self, session, user, scope):
        if scope == "mine":
            if user["role"] != "sales":
                raise DomainError("本人跟进仅销售本人可见", 403)
            personal = self.followups(session, user, "personal")
            center = self.followups(session, user, "center")
            published = {session.get(Submission, item["source_id"]).recording_id:
                         session.get(Submission, item["source_id"]).snapshot["recording_version"] for item in center}
            personal = [item for item in personal if published.get(item["source_id"]) != item["source_revision"]
                        or item["body"].get("feedback")]
            return sorted([*personal, *center], key=lambda f: f["updated_at"], reverse=True)
        sources = {s["id"]: s for s in self.sources(session, user, scope)}
        rows = session.scalars(select(Followup).where(Followup.company_id == user["company_id"],
                                Followup.source_kind == scope).order_by(Followup.updated_at.desc())).all()
        return [{**row_dict(row), "source": {**{k: source[k] for k in ("id", "kind", "revision", "metadata", "owner")},
                                             "analysis_mode": source["report"]["mode"]}}
                for row in rows if (source := sources.get(row.source_id)) and source["revision"] == row.source_revision]


def business_router(service):
    router = APIRouter(prefix="/api/workspace/business", tags=["sales-business"])
    current_user = Depends(service.workspace.auth.identity)

    @router.post("/opportunities", status_code=201)
    def create(value: OpportunityInput, user=current_user):
        if user["role"] != "sales":
            raise DomainError("请由负责销售建立客户商机", 403)
        if not value.title.strip() or not value.customer_id and not value.customer_name.strip():
            raise DomainError("请填写客户和商机名称")
        with service.sessions.begin() as session:
            if value.customer_id:
                customer = session.get(Customer, value.customer_id)
                if not customer or customer.owner_id != user["id"] or customer.company_id != user["company_id"]:
                    raise DomainError("客户不存在或无权访问", 404)
            else:
                customer = Customer(id=uid("cust_"), company_id=user["company_id"], owner_id=user["id"],
                                    team_id=user["team_id"], name=value.customer_name.strip())
                session.add(customer)
                session.flush()
            row = Opportunity(id=uid("opp_"), customer_id=customer.id, company_id=user["company_id"],
                owner_id=user["id"], team_id=user["team_id"], title=value.title.strip())
            session.add(row)
            session.flush()
            service.store.audit(session, "business.opportunity_created", row.id, {"actor": user["id"]})
            return {"id": row.id, "customer_id": customer.id}

    @router.get("/opportunities")
    def opportunities(user=current_user):
        with service.sessions() as session:
            rows = session.scalars(select(Opportunity).where(Opportunity.company_id == user["company_id"])).all()
            visible = {s["metadata"].get("opportunity_id") for s in service.sources(session, user)}
            return [service.opportunity_dict(session, row, user)
                    for row in rows if user["role"] == "sales" and row.owner_id == user["id"] or row.id in visible]

    @router.get("/opportunities/{key}")
    def opportunity(key: str, user=current_user):
        with service.sessions() as session:
            row = service.opportunity(session, key, user)
            scope = "personal" if user["role"] == "sales" else "center"
            sources = [s for s in service.sources(session, user, scope) if s["metadata"].get("opportunity_id") == key]
            # Include own submitted records when the private copy was removed, once per recording.
            if user["role"] == "sales":
                private_ids = {s["id"] for s in sources}
                submissions = service.sources(session, user)
                for s in submissions:
                    original = session.get(Submission, s["id"]).recording_id
                    if s["metadata"].get("opportunity_id") == key and original not in private_ids:
                        sources.append(s)
            sources.sort(key=lambda s: (s["metadata"].get("meeting_date") or "", s["at"]), reverse=True)
            timeline = []
            for index, source in enumerate(sources):
                previous = sources[index + 1] if index + 1 < len(sources) else None
                earlier = {c["text"] for c in previous["report"].get("claims", [])} if previous else set()
                timeline.append({**source, "new_records": [c for c in source["report"].get("claims", []) if c["text"] not in earlier],
                                 "comparison": "与上一场文字条目对比，非成交或阶段确认" if previous else "首场可见记录"})
            stage_sources = [{"id": s["id"], "metadata": s["metadata"]} for s in service.sources(session, user)
                             if s["metadata"].get("opportunity_id") == key]
            return {**service.opportunity_dict(session, row, user),
                    "timeline": timeline, "stage_sources": stage_sources}

    @router.post("/opportunities/{key}/stage")
    def stage(key: str, value: StageInput, user=current_user):
        with service.sessions.begin() as session:
            if service.workspace.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = service.opportunity(session, key, user)
            if user["role"] not in {"sales", "manager"}:
                raise DomainError("阶段由负责销售或团队主管确认", 403)
            source = service.workspace.central(session, value.source_id, user)
            if source.snapshot["metadata"].get("opportunity_id") != key or row.version != value.version:
                raise DomainError("商机版本或会谈归属已变化", 409)
            if not value.reason.strip():
                raise DomainError("请填写阶段确认依据")
            row.history = [*row.history, {"before": row.stage, "after": value.stage, "source_id": source.id,
                "source_revision": source.revision, "actor": user["name"], "reason": value.reason, "at": now()}]
            row.stage, row.version = value.stage, row.version + 1
            service.store.audit(session, "business.stage_confirmed", key, {"actor": user["id"]})
            return {"version": row.version}

    @router.get("/followups")
    def followups(scope: Literal["personal", "center", "mine"] = "center", user=current_user):
        with service.sessions() as session:
            return service.followups(session, user, scope)

    @router.patch("/followups/{key}")
    def update_followup(key: str, value: FollowupInput, user=current_user):
        if value.due:
            valid_date(value.due)
        if not value.note.strip():
            raise DomainError("请说明完成、改期或状态变更的依据")
        with service.sessions.begin() as session:
            if service.workspace.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = session.get(Followup, key)
            if not row or row.company_id != user["company_id"]:
                raise DomainError("后续事项不存在或无权访问", 404)
            sources = {s["id"]: s for s in service.sources(session, user, row.source_kind)}
            source = sources.get(row.source_id)
            if not source:
                raise DomainError("后续事项不存在或无权访问", 404)
            if source["revision"] != row.source_revision or row.version != value.version:
                raise DomainError("来源或事项已更新，请刷新", 409)
            if user["role"] not in {"sales", "manager"}:
                raise DomainError("完成反馈由负责销售或主管记录", 403)
            entry = {"before": row.body, "after": value.model_dump(exclude={"version"}), "actor": user["name"], "at": now()}
            if value.supersedes_id:
                previous = session.get(Followup, value.supersedes_id)
                if not previous or previous.id == row.id or previous.source_id not in sources or previous.source_kind != row.source_kind:
                    raise DomainError("只能关联当前可见范围的其他事项")
                old_source = sources[previous.source_id]
                if old_source["revision"] != previous.source_revision or previous.body["status"] in {"done", "cancelled"}:
                    raise DomainError("旧事项已修订或已结束，请刷新核对", 409)
                opportunity_id = source["metadata"].get("opportunity_id")
                if not opportunity_id or old_source["metadata"].get("opportunity_id") != opportunity_id:
                    raise DomainError("修订关联须属于同一商机")
                previous.history = [*previous.history, {"before": previous.body, "actor": user["name"],
                    "at": now(), "superseded_by": row.id, "note": value.note}]
                previous.body = {**previous.body, "status": "paused", "superseded_by": row.id,
                                 "feedback": value.note, "origin": "已有人工跟进反馈"}
                previous.version += 1
                previous.updated_at = now()
            due = value.due if "due" in value.model_fields_set else row.body.get("due")
            row.body = {**row.body, "status": value.status, "due": due,
                        "responsible": value.responsible if "responsible" in value.model_fields_set else row.body.get("responsible"),
                        "manual_responsible": row.body.get("manual_responsible", False) or ("responsible" in value.model_fields_set and value.responsible != row.body.get("responsible")),
                        "manual_due": row.body.get("manual_due", False) or ("due" in value.model_fields_set and due != row.body.get("due")),
                        "feedback": value.note, "origin": "已有人工跟进反馈"}
            row.history, row.version, row.updated_at = [*row.history, entry], row.version + 1, now()
            service.store.audit(session, "business.followup_updated", key, {"actor": user["id"]})
            return {"version": row.version}

    @router.get("/dashboard")
    def dashboard(days: int = 3, user=current_user):
        if days not in {0, 3, 7}:
            raise DomainError("请选择今天、3 天或 7 天的站内提醒范围")
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        with service.sessions() as session:
            scope = "personal" if user["role"] == "sales" else "center"
            sources = service.sources(session, user, scope)
            items = service.followups(session, user, "mine" if user["role"] == "sales" else scope)
        due = [f for f in items if f["body"]["status"] not in {"done", "cancelled", "paused"}
               and f["body"].get("due") and f["body"]["due"] <= (today + timedelta(days=days)).isoformat()]
        suggestions = []
        for source in sources:
            refs = {ref for item in [*source["report"].get("risks", []), *source["report"].get("claims", [])] for ref in item["evidence"]}
            for ref in refs:
                span = source["evidence"].get(ref, {})
                words = ("跨团队", "跨部门", "公司资源", "老板", "法务") if user["role"] == "boss" else ("主管", "协助", "协调", "未完成", "没定", "待确认")
                if any(w in span.get("text", "") for w in words):
                    suggestions.append({"source_id": source["id"], "kind": scope, "customer": source["metadata"].get("customer"),
                        "text": span["text"], "evidence": ref, "label": "规则关注建议，需人工核对"})
        return {"scope": scope, "today": today.isoformat(), "received": sum(datetime.fromisoformat(s["at"]).astimezone(ZoneInfo("Asia/Shanghai")).date() == today for s in sources),
                "occurred_today": sum(s["metadata"].get("meeting_date") == today.isoformat() for s in sources),
                "unknown_dates": sum(not s["metadata"].get("meeting_date") for s in sources),
                "sources": [{k: s[k] for k in ("id", "kind", "metadata", "owner", "at", "report")} for s in sorted(sources, key=lambda s:s["at"], reverse=True)[:20]],
                "due": due, "suggestions": suggestions[:20],
                "notice": ("个人资料仅本人可见；已上报会谈的中心跟进反馈可被主管／老板查看。" if user["role"] == "sales" else "管理范围仅含已上报资料；") +
                          "未上传不代表未推进，未反馈不代表失约。关注建议是规则线索，不是业务结论。"}

    return router
