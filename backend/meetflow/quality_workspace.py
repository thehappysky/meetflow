"""Account-scoped sales quality using the same immutable meeting evidence.

Legacy sales APIs stay isolated. Only this adapter can create or expose quality
analyses in account mode; specialist tools still read only the frozen snapshot.
"""
import re
import threading
from collections import defaultdict
from statistics import mean
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy import and_, func, select

from .database import (
    Account,
    Project,
    QualityCase,
    QualityFeedback,
    QualityPolicy,
    QualityReview,
    SalesAnalysis,
    SalesConversation,
    SalesPlaybook,
    Submission,
    Team,
    row_dict,
)
from .domain import Contract, DomainError, digest, now, uid
from .sales_domain import QualityRule, ReviewDecision, score, scored_decisions

STAGES = ("通用复盘", "需求沟通", "产品演示", "方案报价", "决策签约")


def example_card(stage):
    focus = {"通用复盘": "业务阶段未确认，不推测销售进度。", "需求沟通": "重点核对现状、困难与影响和后续验证意向。",
             "产品演示": "重点核对演示与需求对应、客户反馈和未解决技术问题。",
             "方案报价": "重点核对范围、报价条件、异议及决策安排；报价不等于客户接受。",
             "决策签约": "重点核对采购、法务、交付等未决条件；意向不等于已经签署。"}[stage]
    applicability = {
        "needs": "本次涉及需求了解、核对或变更时适用。",
        "decision": "本次适合了解决策参与者、安排或约束时适用；客户不愿透露本身不是销售失误。",
        "promise": "本次存在具体产品、交付或服务承诺时适用；没有承诺则不适用，产品依据缺失则无法判断。",
        "objection": "仅本次出现客户明确异议时适用；没有异议则不适用。",
        "next": "本次适合讨论后续安排时适用；客户不愿约时间不直接判为销售失误。",
    }
    definitions = [
        ("needs", "需求澄清", "discovery", 25, "核对现状、困难与影响，确认理解。", ["目前", "影响", "理解对吗"]),
        ("decision", "决策条件了解", "discovery", 20, "适时了解参与者、决策安排与约束；未知如实记录。", ["决策", "预算", "参与"]),
        ("promise", "方案与承诺边界", "product", 20, "方案回应需求，具体承诺有产品依据且说明条件。", []),
        ("objection", "异议处理", "process", 15, "出现异议时澄清原因，给有依据的回应或约定核实。", ["顾虑", "异议", "核实"]),
        ("next", "下一步清晰度", "process", 20, "区分单方建议、双方约定与等待条件，责任和日期如实记录。", ["下一步", "安排", "周五"]),
    ]
    return {"name": f"企业服务销售 · {stage}（示例）", "stage": stage, "product_notes": "",
            "rules": [{"id": key, "title": title, "dimension": dimension, "weight": weight,
                       "criterion": criterion + focus,
                       "applicability": applicability[key] + " 缺失信息不等于失误。",
                       "pass_anchor": criterion + " 有相应销售行为和客户回应支持。",
                       "partial_anchor": "已开展相关动作，但核对、依据或双方确认不充分。",
                       "demo_terms": terms} for key, title, dimension, weight, criterion, terms in definitions]}


class ContextInput(Contract):
    version: int = Field(ge=1)
    stage: Literal["通用复盘", "需求沟通", "产品演示", "方案报价", "决策签约"] = "通用复盘"
    complete: bool = False
    speakers: dict[str, Literal["sales", "customer", "unknown"]] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=1000)


class PolicyInput(Contract):
    base_id: str
    name: str = Field(min_length=1, max_length=150)
    product_notes: str = Field(default="", max_length=15000)
    rules: list[QualityRule] = Field(min_length=1, max_length=15)


class QualityDecision(ReviewDecision):
    evidence: list[str] | None = Field(default=None, max_length=20)


class ReviewQualityInput(Contract):
    version: int = Field(ge=1)
    report_hash: str
    decisions: list[QualityDecision] = Field(default_factory=list, max_length=15)
    note: str = Field(default="", max_length=2000)


class FeedbackInput(Contract):
    version: int = Field(ge=1)
    report_hash: str
    rule_id: str
    reason: str = Field(min_length=2, max_length=2000)
    evidence: list[str] = Field(default_factory=list, max_length=20)


class QualityWorkspace:
    def __init__(self, workspace, sales, driver):
        self.workspace, self.sales, self.driver = workspace, sales, driver
        self.store, self.sessions, self.settings = sales.store, sales.sessions, sales.settings
        self.lock = threading.RLock()

    def policy(self, company_id):
        with self.lock, self.sessions.begin() as session:
            if self.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = session.get(QualityPolicy, company_id)
            if not row:
                project = Project(id=uid("p_"), name="组织销售培训规则（内部）")
                session.add(project)
                session.flush()
                cards = {}
                for stage in STAGES:
                    card = SalesPlaybook(id=uid("pb_"), project_id=project.id, family_id=uid("pf_"),
                                         version=1, body=example_card(stage))
                    session.add(card)
                    cards[stage] = card.id
                session.flush()
                row = QualityPolicy(company_id=company_id, project_id=project.id, cards=cards)
                session.add(row)
                session.flush()
            return {"project_id": row.project_id,
                    "cards": [row_dict(session.get(SalesPlaybook, key)) for key in row.cards.values()]}

    def publish(self, user, value):
        if user["role"] != "admin":
            raise DomainError("培训规则由公司管理员发布", 403)
        policy = self.policy(user["company_id"])
        base = next((card for card in policy["cards"] if card["id"] == value.base_id), None)
        if not base:
            raise DomainError("规则版本不存在或已有新版", 409)
        if not value.name.strip() or len({r.id for r in value.rules}) != len(value.rules):
            raise DomainError("规则名称不能为空，规则编号不能重复")
        if sum(rule.weight for rule in value.rules) != 100:
            raise DomainError("本版规则权重之和须为 100")
        with self.lock, self.sessions.begin() as session:
            if self.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = session.get(QualityPolicy, user["company_id"])
            stage = base["body"]["stage"]
            if row.cards[stage] != value.base_id:
                raise DomainError("已有新版本，请刷新规则", 409)
            card = SalesPlaybook(id=uid("pb_"), project_id=row.project_id, family_id=base["family_id"],
                version=base["version"] + 1, body={**value.model_dump(exclude={"base_id"}), "stage": stage})
            session.add(card)
            session.flush()
            row.cards = {**row.cards, stage: card.id}
            self.store.audit(session, "quality.policy_published", card.id, {"actor": user["id"], "stage": stage})
            return {"id": card.id, "version": card.version}

    def source(self, session, kind, key, user=None):
        if kind != "center":
            raise DomainError("管理质检只读取已上报资料", 403)
        row = session.get(Submission, key)
        if not row or row.deleted or user and not self.workspace.visible(row, user):
            raise DomainError("中心会谈不存在或无权访问", 404)
        evidence = {key: span for key, span in row.snapshot["evidence"].items() if span["source_kind"] == "transcript"}
        return (row, row.revision, row.snapshot["metadata"], evidence,
                bool(row.snapshot.get("audio_path")), row.snapshot.get("quality_context", {}))

    @staticmethod
    def scope_key(kind, key, revision):
        return f"{kind}:{key}:v{revision}"

    def freeze(self, session, kind, key, revision):
        case = session.scalar(select(QualityCase).where(
            QualityCase.scope_key == self.scope_key(kind, key, revision)))
        if not case:
            return {}
        return {"stage": case.body["stage"], "complete": case.body["complete"],
                "speakers": {s["source_ref"]: s["speaker"] for s in case.body["spans"]}}

    @staticmethod
    def spans(evidence, inherited):
        if (len(evidence) > 400 or sum(len(s["text"]) for s in evidence.values()) > 50000
                or any(len(s["text"]) > 4000 for s in evidence.values())):
            raise DomainError("质检上限为 400 段／50000 字、单段 4000 字；纪要保留，请按实际发言或业务场景拆分后复盘", 422)
        spans = []
        for index, (ref, span) in enumerate(evidence.items()):
            text = span["text"]
            explicit = re.match(r"^(销售[^：:\s]{0,8}|客户[^：:\s]{0,8})[：:]", text)
            speaker = "sales" if explicit and explicit[1].startswith("销售") else "customer" if explicit else "unknown"
            speaker = inherited.get("speakers", {}).get(ref, speaker)
            spans.append({"id": f"s{index + 1}", "source_ref": ref, "text": text,
                          "speaker": speaker, "start": span.get("start"), "end": span.get("end")})
        return spans

    def ensure(self, kind, key):
        if kind != "center":
            return None  # Management grading starts only after explicit submission.
        with self.sessions() as session:
            source, revision, meta, evidence, audio, inherited = self.source(session, kind, key)
            company_id, owner_id, team_id = source.company_id, source.owner_id, source.team_id
            owner_name = session.get(Account, owner_id).name
        policy = self.policy(company_id)
        with self.lock, self.sessions.begin() as session:
            if self.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            # Recheck after acquiring the write transaction, avoiding stale publication races.
            _, current, _, _, _, _ = self.source(session, kind, key)
            if current != revision:
                raise DomainError("来源已更新，请重新整理", 409)
            scope_key = self.scope_key(kind, key, revision)
            case = session.scalar(select(QualityCase).where(QualityCase.scope_key == scope_key))
            if case:
                return case.id
            error = None
            try:
                spans = self.spans(evidence, inherited)
            except DomainError as exc:
                spans, error = [], exc.message
            body = {"metadata": meta, "owner_name": owner_name, "has_audio": audio, "spans": spans,
                    "stage": inherited.get("stage", "通用复盘"), "complete": inherited.get("complete", False)}
            case = QualityCase(id=uid("qc_"), scope_key=scope_key, company_id=company_id,
                owner_id=owner_id, team_id=team_id, source_kind=kind, source_id=key,
                source_revision=revision, body=body, error=error)
            session.add(case)
            session.flush()
            previous_cases = session.scalars(select(QualityCase.id).where(
                QualityCase.source_kind == kind, QualityCase.source_id == key,
                QualityCase.source_revision < revision)).all()
            if previous_cases:
                for feedback in session.scalars(select(QualityFeedback).where(
                        QualityFeedback.case_id.in_(previous_cases), QualityFeedback.status == "pending")):
                    feedback.status = "superseded"
                    feedback.resolution = f"来源已修订为 v{revision}，原异议保留，请在新版继续核对。"
            analysis_id = self.start(session, case, policy) if not error else None
            result = case.id
        self.schedule(analysis_id)
        return result

    def start(self, session, case, policy):
        if not any(s["speaker"] == "sales" for s in case.body["spans"]):
            case.active_analysis_id = None
            return None  # Do not pay for a grading call until a salesperson can be identified.
        card = next(card for card in policy["cards"] if card["body"]["stage"] == case.body["stage"])
        body = {"title": case.body["metadata"]["title"], "salesperson": case.body["owner_name"],
                "customer": case.body["metadata"].get("customer") or "待归属",
                "occurred_on": case.body["metadata"].get("meeting_date") or None,
                "stage": case.body["stage"], "complete": case.body["complete"],
                "spans": [{k: v for k, v in s.items() if k != "source_ref"} for s in case.body["spans"]]}
        conversation = SalesConversation(id=uid("sc_"), project_id=policy["project_id"], body=body,
                                         status="ready", audio_path=None)
        session.add(conversation)
        session.flush()
        snapshot = {"conversation": self.sales.conversation_dict(conversation), "playbook": card}
        analysis = SalesAnalysis(id=uid("sa_"), conversation_id=conversation.id, project_id=policy["project_id"],
            playbook_id=card["id"], snapshot=snapshot, mode="live" if self.settings.live_available else "demo")
        session.add(analysis)
        session.flush()
        case.analysis_ids = [*case.analysis_ids, analysis.id]
        case.active_analysis_id = analysis.id
        self.store.audit(session, "quality.analysis_started", case.id,
                         {"source_revision": case.source_revision, "analysis": analysis.id})
        return analysis.id

    def schedule(self, analysis_id):
        if analysis_id and self.settings.start_worker:
            self.driver.submit(analysis_id)

    def suspend_private(self):
        with self.sessions.begin() as session:
            ids = [key for case in session.scalars(select(QualityCase).where(
                QualityCase.source_kind == "personal")) for key in case.analysis_ids]
            for analysis in session.scalars(select(SalesAnalysis).where(
                    SalesAnalysis.id.in_(ids), SalesAnalysis.status.in_(["queued", "running"]))):
                analysis.cancel_requested = True
                analysis.status, analysis.phase = "cancelled", "个人资料不执行管理质检"
                self.store.audit(session, "quality.private_suspended", analysis.id)

    def recover(self):
        with self.sessions() as session:
            central = session.scalars(select(Submission.id).where(Submission.deleted.is_(False))).all()
        for key in central:
            self.ensure("center", key)

    def case(self, session, key, user, write=False):
        self.manager_reader(user)
        case = session.get(QualityCase, key)
        if not case or case.source_kind != "center" or case.company_id != user["company_id"]:
            raise DomainError("质检不存在或无权访问", 404)
        _, revision, _, _, _, _ = self.source(session, case.source_kind, case.source_id, user)
        if write and revision != case.source_revision:
            raise DomainError("来源已有新版本，请查看当前质检", 409)
        return case, revision

    @staticmethod
    def manager_reader(user):
        if user["role"] not in {"manager", "boss"}:
            raise DomainError("销售质检仅供主管和老板查看", 403)

    def serialize(self, session, case, current_revision, full=False, analysis_id=None, view="full"):
        selected = analysis_id or case.active_analysis_id
        if selected and selected not in case.analysis_ids:
            raise DomainError("报告不属于本场质检", 404)
        analysis = session.get(SalesAnalysis, selected) if selected else None
        review = session.scalar(select(QualityReview).where(QualityReview.analysis_id == selected)
                                .order_by(QualityReview.created_at.desc())) if selected else None
        feedback = session.scalars(select(QualityFeedback).where(QualityFeedback.case_id == case.id)
                                   .order_by(QualityFeedback.created_at)).all()
        team = session.get(Team, case.team_id) if case.team_id else None
        result = {"id": case.id, "source_kind": case.source_kind, "source_id": case.source_id,
                  "source_revision": case.source_revision, "version": case.version,
                  "stale": current_revision != case.source_revision,
                  "owner": case.body["owner_name"], "metadata": case.body["metadata"],
                  "subject": {"account_id": case.owner_id, "name": case.body["owner_name"],
                              "team_id": case.team_id,
                              "team_name": team.name if team and team.company_id == case.company_id else "团队待确认"},
                  "stage": analysis.snapshot["conversation"]["body"]["stage"] if analysis else case.body["stage"],
                  "status": analysis.status if analysis else "failed" if case.error else "needs_speakers",
                  "pending_feedback": sum(f.status == "pending" for f in feedback),
                  "mode": analysis.mode if analysis else None, "error": analysis.error if analysis else case.error,
                  "metrics": (review.body if review else analysis.report).get("metrics") if analysis and analysis.report else None,
                  "policy": {"id": analysis.playbook_id, "name": analysis.snapshot["playbook"]["body"]["name"],
                             "version": analysis.snapshot["playbook"]["version"]} if analysis else None}
        if full:
            refs = {s["id"]: s["source_ref"] for s in case.body["spans"]}
            spans = [{**s, "source_ref": refs[s["id"]]} for s in analysis.snapshot["conversation"]["body"]["spans"]] if analysis else case.body["spans"]
            result.update(spans=spans, complete=analysis.snapshot["conversation"]["body"]["complete"] if analysis else case.body["complete"],
                          has_audio=case.body["has_audio"], report=analysis.report if analysis else None,
                          report_hash=analysis.report_hash if analysis else None,
                          rules=analysis.snapshot["playbook"]["body"]["rules"] if analysis else [],
                          review=review.body if review else None,
                          historical=bool(selected and selected != case.active_analysis_id),
                          history=[{"id": key, "at": session.get(SalesAnalysis, key).created_at} for key in case.analysis_ids],
                          source_history=[{"id": c.id, "source_revision": c.source_revision,
                                           "title": c.body["metadata"]["title"],
                                           "feedback_count": len(session.scalars(select(QualityFeedback.id).where(
                                               QualityFeedback.case_id == c.id)).all())} for c in session.scalars(
                              select(QualityCase).where(QualityCase.company_id == case.company_id,
                                  QualityCase.source_kind == case.source_kind,
                                  QualityCase.source_id == case.source_id).order_by(QualityCase.source_revision.desc()))],
                          feedback=[row_dict(f) for f in feedback],
                          review_history=[{"id": r.id, "actor": session.get(Account, r.actor_id).name,
                                           "at": r.created_at, "body": r.body} for r in session.scalars(
                              select(QualityReview).where(QualityReview.case_id == case.id)
                              .order_by(QualityReview.created_at.desc()))])
            effective = (review.body if review else analysis.report).get("decisions", []) if analysis and analysis.report else []
            rules = {r["id"]:r for r in result["rules"]}
            labels = {"met":"满足要求", "partial":"部分满足要求", "unmet":"未满足要求",
                      "unknown":"依据不足，待确认", "not_applicable":"本场不适用"}
            result["has_report"] = bool(analysis and analysis.report)
            result["view"] = view
            result["decisions"] = effective
            if view == "summary":
                result["decisions"] = [{"rule_id":d["rule_id"], "title":rules[d["rule_id"]]["title"],
                    "status":d["status"], "weight":d["weight"], "points":d.get("points"), "deduction":d.get("deduction"),
                    "reason_title": d.get("reason_title") or f"{rules[d['rule_id']]['title']}：{labels[d['status']]}"}
                    for d in effective]
                for key in ("report", "review", "spans", "rules", "feedback", "review_history", "report_hash"):
                    result.pop(key, None)
            elif view == "details":
                result.pop("spans", None)
        return result

    def listing(self, user, scope):
        self.manager_reader(user)
        if scope != "center":
            raise DomainError("质检仅分析已上报的中心资料", 403)
        visible = select(QualityCase.id, QualityCase.source_kind, QualityCase.source_id,
            QualityCase.source_revision, QualityCase.version, QualityCase.team_id,
            QualityCase.owner_id, QualityCase.active_analysis_id, QualityCase.error,
            QualityCase.created_at, QualityCase.body["metadata"].label("metadata"),
            QualityCase.body["owner_name"].as_string().label("owner"),
            QualityCase.body["stage"].as_string().label("stage")).join(Submission,
                and_(Submission.id == QualityCase.source_id,
                     Submission.revision == QualityCase.source_revision)).where(
                QualityCase.company_id == user["company_id"], QualityCase.source_kind == scope,
                Submission.company_id == user["company_id"], Submission.deleted.is_(False))
        if user["role"] == "manager":
            visible = visible.where(Submission.team_id == user["team_id"])
        visible = visible.cte("visible_quality")
        reviews = select(QualityReview.analysis_id, QualityReview.id,
            QualityReview.body["metrics"].label("metrics"),
            func.row_number().over(partition_by=QualityReview.analysis_id,
                order_by=QualityReview.created_at.desc()).label("position")).join(
                    visible, QualityReview.case_id == visible.c.id).subquery()
        feedback = select(QualityFeedback.case_id, func.count().label("pending")).join(
            visible, QualityFeedback.case_id == visible.c.id).where(
                QualityFeedback.status == "pending").group_by(QualityFeedback.case_id).subquery()
        statement = select(visible, Team.name.label("team_name"), SalesAnalysis.id.label("analysis_id"),
            SalesAnalysis.status.label("analysis_status"), SalesAnalysis.error.label("analysis_error"),
            SalesAnalysis.mode, SalesAnalysis.playbook_id,
            SalesAnalysis.snapshot["conversation"]["body"]["stage"].as_string().label("analysis_stage"),
            SalesAnalysis.snapshot["playbook"]["body"]["name"].as_string().label("policy_name"),
            SalesAnalysis.snapshot["playbook"]["version"].as_integer().label("policy_version"),
            SalesAnalysis.report["metrics"].label("machine_metrics"), reviews.c.id.label("review_id"),
            reviews.c.metrics.label("review_metrics"), feedback.c.pending).outerjoin(
                Team, and_(Team.id == visible.c.team_id, Team.company_id == user["company_id"])).outerjoin(
                SalesAnalysis, SalesAnalysis.id == visible.c.active_analysis_id).outerjoin(
                reviews, and_(reviews.c.analysis_id == SalesAnalysis.id, reviews.c.position == 1)).outerjoin(
                feedback, feedback.c.case_id == visible.c.id).order_by(visible.c.created_at.desc())
        with self.sessions() as session:
            output = []
            for row in session.execute(statement).mappings():
                has_analysis = row["analysis_id"] is not None
                output.append({"id": row["id"], "source_kind": row["source_kind"],
                    "source_id": row["source_id"], "source_revision": row["source_revision"],
                    "version": row["version"], "stale": False, "owner": row["owner"],
                    "metadata": row["metadata"],
                    "subject": {"account_id": row["owner_id"], "name": row["owner"],
                        "team_id": row["team_id"], "team_name": row["team_name"] or "团队待确认"},
                    "stage": row["analysis_stage"] if has_analysis else row["stage"],
                    "status": row["analysis_status"] if has_analysis else "failed" if row["error"] else "needs_speakers",
                    "pending_feedback": row["pending"] or 0, "mode": row["mode"],
                    "error": row["analysis_error"] if has_analysis else row["error"],
                    "metrics": (row["review_metrics"] if row["review_id"] else row["machine_metrics"])
                        if row["machine_metrics"] is not None else None,
                    "policy": {"id": row["playbook_id"], "name": row["policy_name"],
                        "version": row["policy_version"]} if has_analysis else None})
        groups = defaultdict(list)
        for row in output:
            if row["metrics"]:
                groups[(row["policy"]["id"], row["mode"], row["status"] == "reviewed")].append(row)
        summary = [{"policy": rows[0]["policy"], "mode": mode, "reviewed": reviewed, "samples": len(rows),
                    "average_score": round(mean(values), 1) if (values := [r["metrics"]["score"] for r in rows if r["metrics"]["score"] is not None]) else None}
                   for (_, mode, reviewed), rows in groups.items()]
        return {"cases": output, "summary": summary, "scope": scope,
                "notice": "仅基于当前可见来源；规则版本、分析模式及自动／已复核结果分开统计，不代表全部销售活动。"}

    def context(self, user, key, value):
        with self.sessions() as session:
            case, _ = self.case(session, key, user, write=True)
            company_id = case.company_id
        policy = self.policy(company_id)
        with self.lock, self.sessions.begin() as session:
            if self.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            case, _ = self.case(session, key, user, write=True)
            if user["role"] != "manager" or user["team_id"] != case.team_id:
                raise DomainError("中心质检背景由负责主管核对", 403)
            if case.version != value.version:
                raise DomainError("质检已更新，请刷新", 409)
            if not value.reason.strip():
                raise DomainError("请说明中心质检背景的更正原因")
            if case.error:
                raise DomainError(case.error, 422)
            refs = {s["id"] for s in case.body["spans"]}
            if not set(value.speakers) <= refs:
                raise DomainError("身份确认只能引用本场发言")
            if case.active_analysis_id and session.get(SalesAnalysis, case.active_analysis_id).status in {"queued", "running"}:
                raise DomainError("本次质检正在处理，请完成后再调整背景", 409)
            spans = [{**s, "speaker": value.speakers.get(s["id"], s["speaker"])} for s in case.body["spans"]]
            case.body = {**case.body, "stage": value.stage, "complete": value.complete, "spans": spans}
            case.version += 1
            case.updated_at = now()
            case.error = None
            analysis_id = self.start(session, case, policy)
            self.store.audit(session, "quality.context_updated", case.id,
                             {"actor": user["id"], "reason": value.reason, "version": case.version})
            for feedback in session.scalars(select(QualityFeedback).where(
                    QualityFeedback.case_id == key, QualityFeedback.status == "pending")):
                feedback.status, feedback.reviewed_by = "superseded", user["id"]
                feedback.resolution = "已更新质检背景并重新整理，原反馈保留。" + value.reason
        self.schedule(analysis_id)
        return {"id": key}

    def review(self, user, key, value):
        request_hash = digest({"case": key, "actor": user["id"], **value.model_dump()})
        with self.lock, self.sessions.begin() as session:
            if self.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            case, _ = self.case(session, key, user, write=True)
            if user["role"] != "manager" or case.team_id != user["team_id"]:
                raise DomainError("仅负责团队主管可复核已上报质检", 403)
            previous = session.scalar(select(QualityReview).where(QualityReview.request_hash == request_hash))
            if previous:
                return {"status": "reviewed"}
            if case.version != value.version or not case.active_analysis_id:
                raise DomainError("报告已更新，请刷新", 409)
            analysis = session.get(SalesAnalysis, case.active_analysis_id)
            if analysis.status not in {"waiting_review", "reviewed"} or not analysis.report or analysis.report_hash != value.report_hash:
                raise DomainError("报告未完成或已变化", 409)
            if any(f.status == "pending" for f in session.scalars(select(QualityFeedback).where(QualityFeedback.case_id == key))) and not value.note.strip():
                raise DomainError("存在销售异议，请填写处理说明")
            latest = session.scalar(select(QualityReview).where(QualityReview.analysis_id == analysis.id)
                                    .order_by(QualityReview.created_at.desc()))
            decisions = {item["rule_id"]: dict(item) for item in (latest.body if latest else analysis.report)["decisions"]}
            if len({d.rule_id for d in value.decisions}) != len(value.decisions):
                raise DomainError("复核规则不能重复")
            spans = {s["id"]: s for s in analysis.snapshot["conversation"]["body"]["spans"]}
            for decision in value.decisions:
                if decision.rule_id not in decisions:
                    raise DomainError("规则不属于本场质检")
                item = decisions[decision.rule_id]
                refs = decision.evidence if decision.evidence is not None else item["evidence"]
                if not set(refs) <= set(spans):
                    raise DomainError("复核依据不属于本场发言")
                if (decision.status != item["status"] or refs != item["evidence"]) and not decision.reason.strip():
                    raise DomainError("改判必须填写原因")
                if decision.status in {"met", "partial"} and not any(spans[r]["speaker"] == "sales" for r in refs):
                    raise DomainError("达标或部分达标必须有已确认销售身份的依据")
                if decision.status == "unmet" and (not case.body["complete"] or any(s["speaker"] == "unknown" for s in spans.values())):
                    raise DomainError("录音不完整或有未知身份，不能据此认定销售未执行")
                if decision.status != item["status"] or refs != item["evidence"] or decision.reason:
                    item["reason_title"] = ""  # A superseded machine headline must not describe a new judgment.
                item.update(status=decision.status, evidence=refs, review_reason=decision.reason or item.get("review_reason", "保留机器判断"))
            rules = analysis.snapshot["playbook"]["body"]["rules"]
            body = {"decisions": scored_decisions(rules, list(decisions.values())),
                    "metrics": score(rules, list(decisions.values())), "note": value.note}
            review = QualityReview(id=uid("qr_"), case_id=key, analysis_id=analysis.id,
                                   actor_id=user["id"], request_hash=request_hash, body=body)
            session.add(review)
            analysis.status = "reviewed"
            case.version += 1
            case.updated_at = now()
            for feedback in session.scalars(select(QualityFeedback).where(
                    QualityFeedback.case_id == key, QualityFeedback.analysis_id == analysis.id,
                    QualityFeedback.status == "pending")):
                feedback.status, feedback.resolution, feedback.reviewed_by = "resolved", value.note, user["id"]
            self.store.audit(session, "quality.manager_reviewed", key, {"actor": user["id"], "review": review.id})
            return {"status": "reviewed"}


def quality_router(service):
    authentication = Depends(service.workspace.auth.identity)
    def quality_identity(user=authentication):
        if user["role"] not in {"manager", "boss", "admin"}:
            raise DomainError("销售账号不能访问管理质检", 403)
        return user
    router = APIRouter(prefix="/api/workspace/quality", tags=["account-quality"], dependencies=[Depends(quality_identity)])
    current_user = Depends(quality_identity)

    @router.get("/policy")
    def policy(user=current_user):
        value = service.policy(user["company_id"])
        return {"cards": [{"id": c["id"], "version": c["version"], **c["body"]} for c in value["cards"]]}

    @router.post("/policy", status_code=201)
    def publish(value: PolicyInput, user=current_user):
        return service.publish(user, value)

    @router.get("/cases")
    def listing(scope: Literal["personal", "center"] = "center", user=current_user):
        return service.listing(user, scope)

    @router.get("/cases/{key}")
    def detail(key: str, analysis_id: str | None = None,
               view: Literal["summary", "details", "full"] = "full", user=current_user):
        with service.sessions() as session:
            case, revision = service.case(session, key, user)
            return service.serialize(session, case, revision, full=True, analysis_id=analysis_id, view=view)

    @router.post("/cases/{key}/context")
    def context(key: str, value: ContextInput, user=current_user):
        return service.context(user, key, value)

    @router.post("/cases/{key}/review")
    def review(key: str, value: ReviewQualityInput, user=current_user):
        return service.review(user, key, value)

    @router.post("/cases/{key}/retry")
    def retry(key: str, user=current_user):
        with service.lock, service.sessions.begin() as session:
            if service.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            case, _ = service.case(session, key, user, write=True)
            if user["role"] != "manager" or user["team_id"] != case.team_id:
                raise DomainError("仅负责主管可恢复中心质检", 403)
            analysis = session.get(SalesAnalysis, case.active_analysis_id) if case.active_analysis_id else None
            if not analysis or analysis.status != "failed" or analysis.cancel_requested:
                raise DomainError("仅处理失败的质检可重试", 409)
            if analysis.model_calls >= service.settings.max_model_calls or analysis.tool_calls >= service.settings.max_tool_calls:
                raise DomainError("累计调用预算已用尽，需检查配置后重新整理", 409)
            analysis.status, analysis.error = "queued", None
            analysis_id = analysis.id
            service.store.audit(session, "quality.retry", key, {"actor": user["id"]})
        service.schedule(analysis_id)
        return {"status": "queued"}

    @router.post("/cases/{key}/feedback", status_code=201)
    def feedback(key: str, value: FeedbackInput, user=current_user):
        raise DomainError("此入口已停用；管理质检由负责主管版本化复核", 403)
    return router
