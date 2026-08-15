"""Persistent sales quality application service, review and descriptive analytics."""
from collections import Counter, defaultdict
from threading import RLock

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .database import (
    Project,
    SalesAnalysis,
    SalesConversation,
    SalesPlaybook,
    SalesReview,
    Task,
    row_dict,
)
from .domain import DomainError, digest, now, uid
from .leases import current_lease
from .sales_domain import LABELS, ConversationInput, parse_transcript, score, scored_decisions


class SalesService:
    def __init__(self, store):
        self.store, self.sessions, self.settings = store, store.sessions, store.settings
        self.lock = RLock()

    @staticmethod
    def require_project(session, project_id):
        if not session.get(Project, project_id):
            raise DomainError("项目不存在", 404)

    def playbooks(self, project_id):
        with self.sessions() as session:
            self.require_project(session, project_id)
            return [row_dict(row) for row in session.scalars(select(SalesPlaybook).where(
                SalesPlaybook.project_id == project_id).order_by(SalesPlaybook.created_at.desc()))]

    def publish(self, value):
        try:
            with self.sessions.begin() as session:
                self.require_project(session, value.project_id)
                family, version = uid("pf_"), 1
                if value.base_id:
                    base = session.get(SalesPlaybook, value.base_id)
                    if not base or base.project_id != value.project_id:
                        raise DomainError("基础评分卡不属于当前项目", 404)
                    latest = session.scalar(select(SalesPlaybook).where(
                        SalesPlaybook.family_id == base.family_id).order_by(SalesPlaybook.version.desc()))
                    if latest.id != base.id:
                        raise DomainError("已有更新的评分卡版本，请从最新版本修改", 409)
                    family, version = base.family_id, base.version + 1
                row = SalesPlaybook(id=uid("pb_"), project_id=value.project_id, family_id=family,
                    version=version, body=value.model_dump(exclude={"project_id", "base_id"}))
                session.add(row)
                session.flush()
                self.store.audit(session, "sales.playbook_published", row.id, {"version": version})
                return row_dict(row)
        except IntegrityError:
            raise DomainError("评分卡发布冲突，请刷新最新版本", 409) from None

    def create_conversation(self, value, audio_path=None, status="ready"):
        body = value.model_dump(exclude={"project_id", "transcript"})
        body["spans"] = body["spans"] or parse_transcript(value.transcript)
        with self.sessions.begin() as session:
            self.require_project(session, value.project_id)
            row = SalesConversation(id=uid("sc_"), project_id=value.project_id, body=body,
                                    audio_path=audio_path, status=status)
            session.add(row)
            session.flush()
            self.store.audit(session, "sales.conversation_imported", row.id, {"audio": bool(audio_path)})
            return self.conversation_dict(row)

    @staticmethod
    def conversation_dict(row):
        return {key: value for key, value in row_dict(row).items() if key != "audio_path"} | {
            "has_audio": bool(row.audio_path)}

    def conversation(self, conversation_id):
        with self.sessions() as session:
            row = session.get(SalesConversation, conversation_id)
            if not row:
                raise DomainError("销售会话不存在", 404)
            return self.conversation_dict(row)

    def conversations(self, project_id):
        with self.sessions() as session:
            self.require_project(session, project_id)
            rows = session.scalars(select(SalesConversation).where(
                SalesConversation.project_id == project_id).order_by(SalesConversation.created_at.desc()))
            result = []
            for row in rows:
                latest = session.scalar(select(SalesAnalysis).where(SalesAnalysis.conversation_id == row.id)
                                        .order_by(SalesAnalysis.created_at.desc()))
                result.append({**self.conversation_dict(row), "analysis": {
                    "id": latest.id, "status": latest.status, "phase": latest.phase} if latest else None})
            return result

    def edit_spans(self, conversation_id, value):
        with self.sessions.begin() as session:
            row = session.get(SalesConversation, conversation_id)
            if not row:
                raise DomainError("销售会话不存在", 404)
            if row.status == "transcribing" or session.scalar(select(SalesAnalysis.id).where(
                    SalesAnalysis.conversation_id == row.id, SalesAnalysis.status.in_(["queued", "running"]))):
                raise DomainError("正在处理，请先等待或停止分析", 409)
            spans = [span.model_dump() for span in value.spans]
            # Audio edits change identity only, preserving immutable timed words and seek evidence.
            if row.audio_path and [(s["id"], s["text"], s.get("start"), s.get("end")) for s in spans] != [
                    (s["id"], s["text"], s.get("start"), s.get("end")) for s in row.body["spans"]]:
                raise DomainError("录音片段仅可确认身份，不可改写原文或时间戳")
            body = {**row.body, "spans": spans, "complete": value.complete}
            result = session.execute(update(SalesConversation).where(
                SalesConversation.id == row.id, SalesConversation.version == value.version).values(
                    body=body, version=SalesConversation.version + 1, status="ready", error=None))
            if result.rowcount != 1:
                raise DomainError("会话已变化，请刷新", 409)
            self.store.audit(session, "sales.speakers_confirmed", row.id, {"version": value.version + 1})
            session.refresh(row)
            return self.conversation_dict(row)

    def analyze(self, conversation_id, value):
        if value.mode == "live" and not self.settings.live_available:
            raise DomainError("真实模型未配置；不会使用规则演示代替", 422)
        with self.sessions.begin() as session:
            conversation = session.get(SalesConversation, conversation_id)
            if not conversation:
                raise DomainError("销售会话不存在", 404)
            card = session.get(SalesPlaybook, value.playbook_id)
            if not card or card.project_id != conversation.project_id:
                raise DomainError("评分卡不属于当前项目", 404)
            if conversation.body["stage"] != card.body["stage"]:
                raise DomainError("会话阶段与评分卡不匹配，请选用相同阶段的评分卡")
            if conversation.status != "ready":
                raise DomainError("请完成转写和说话人确认后再分析", 409)
            if session.scalar(select(SalesAnalysis.id).where(SalesAnalysis.conversation_id == conversation.id,
                    SalesAnalysis.status.in_(["queued", "running"]))):
                raise DomainError("已有分析正在执行", 409)
            snapshot = {"conversation": self.conversation_dict(conversation), "playbook": row_dict(card)}
            row = SalesAnalysis(id=uid("sa_"), conversation_id=conversation.id, project_id=conversation.project_id,
                                playbook_id=card.id, snapshot=snapshot, mode=value.mode)
            session.add(row)
            session.flush()
            self.store.audit(session, "sales.analysis_created", row.id,
                             {"mode": value.mode, "playbook_id": card.id, "conversation_version": conversation.version})
            return row_dict(row)

    def get(self, analysis_id):
        with self.sessions() as session:
            row = session.get(SalesAnalysis, analysis_id)
            if not row:
                raise DomainError("质检分析不存在", 404)
            return row_dict(row)

    def detail(self, analysis_id):
        result = self.get(analysis_id)
        with self.sessions() as session:
            review = session.scalar(select(SalesReview).where(SalesReview.analysis_id == analysis_id))
            conversation = session.get(SalesConversation, result["conversation_id"])
            return {"analysis": result, "review": row_dict(review) if review else None,
                    "stale": conversation.version != result["snapshot"]["conversation"]["version"]}

    def change(self, analysis_id, **values):
        with self.sessions.begin() as session:
            self.fence(session, analysis_id)
            session.execute(update(SalesAnalysis).where(SalesAnalysis.id == analysis_id).values(**values))

    def event(self, analysis_id, message):
        with self.lock, self.sessions.begin() as session:
            self.fence(session, analysis_id)
            row = session.get(SalesAnalysis, analysis_id)
            row.events = [*row.events, {"message": message, "at": now()}][-150:]

    def reserve(self, analysis_id, kind):
        field = SalesAnalysis.model_calls if kind == "model" else SalesAnalysis.tool_calls
        limit = self.settings.max_model_calls if kind == "model" else self.settings.max_tool_calls
        with self.sessions.begin() as session:
            self.fence(session, analysis_id)
            result = session.execute(update(SalesAnalysis).where(SalesAnalysis.id == analysis_id,
                SalesAnalysis.cancel_requested.is_(False), field < limit).values({field.key: field + 1}))
            if result.rowcount != 1:
                raise DomainError("质检已停止或累计调用预算已用尽", 409)

    def artifact(self, analysis_id, key, result):
        with self.lock, self.sessions.begin() as session:
            self.fence(session, analysis_id)
            row = session.get(SalesAnalysis, analysis_id)
            row.artifacts = {**row.artifacts, key: result}

    def usage(self, analysis_id, usage):
        with self.sessions.begin() as session:
            self.fence(session, analysis_id)
            session.execute(update(SalesAnalysis).where(SalesAnalysis.id == analysis_id).values(
                input_tokens=SalesAnalysis.input_tokens + max(0, int(usage.get("prompt_tokens", 0))),
                output_tokens=SalesAnalysis.output_tokens + max(0, int(usage.get("completion_tokens", 0)))))

    @staticmethod
    def fence(session, analysis_id):
        lease = current_lease()
        if lease and lease.key == f'quality:{analysis_id}':
            lease.fence(session)

    def review(self, analysis_id, value):
        request_hash = digest(value.model_dump())
        try:
            with self.sessions.begin() as session:
                previous = session.scalar(select(SalesReview).where(
                    SalesReview.idempotency_key == value.idempotency_key))
                if previous:
                    if previous.analysis_id != analysis_id or previous.request_hash != request_hash:
                        raise DomainError("幂等键已用于其他复核内容", 409)
                    return row_dict(previous)
                row = session.get(SalesAnalysis, analysis_id)
                if not row:
                    raise DomainError("质检分析不存在", 404)
                if row.status != "waiting_review" or not row.report or row.report_hash != value.report_hash:
                    raise DomainError("报告已变化或已复核，请刷新", 409)
                conversation = session.get(SalesConversation, row.conversation_id)
                frozen_version = row.snapshot["conversation"]["version"]
                # Conditional UPDATE serializes a concurrent transcript edit with this review.
                guard = session.execute(update(SalesConversation).where(SalesConversation.id == conversation.id,
                    SalesConversation.version == value.conversation_version,
                    SalesConversation.version == frozen_version).values(version=SalesConversation.version))
                if guard.rowcount != 1:
                    raise DomainError("会话已修订，请基于新版本重新分析", 409)
                decisions = {item["rule_id"]: dict(item) for item in row.report["decisions"]}
                ids = [item.rule_id for item in value.decisions]
                if len(ids) != len(set(ids)) or not set(ids) <= set(decisions):
                    raise DomainError("复核包含重复或不存在的规则")
                for item in value.decisions:
                    if decisions[item.rule_id]["status"] != item.status:
                        if not item.reason.strip():
                            raise DomainError("修改机器判断必须填写复核原因")
                        decisions[item.rule_id].update(status=item.status, review_reason=item.reason)
                chosen = value.followup_rule_ids
                if len(chosen) != len(set(chosen)) or not set(chosen) <= set(decisions):
                    raise DomainError("辅导任务包含重复或不存在的规则")
                rules = row.snapshot["playbook"]["body"]["rules"]
                task_ids = []
                for rule in rules:
                    if rule["id"] not in chosen:
                        continue
                    decision = decisions[rule["id"]]
                    task = Task(id=uid("t_"), project_id=row.project_id, meeting_id=None,
                        title=f"销售辅导：{rule['title']}", owner=conversation.body["salesperson"], due=None,
                        status="todo", conditions=[decision.get("guidance", ""), f"质检来源 {row.id}"],
                        evidence=[{"sales_analysis_id": row.id, "span_id": ref} for ref in decision["evidence"]])
                    session.add(task)
                    task_ids.append(task.id)
                body = {"decisions": scored_decisions(rules, list(decisions.values())),
                        "metrics": score(rules, list(decisions.values())),
                        "note": value.note, "task_ids": task_ids, "report_hash": row.report_hash}
                review = SalesReview(id=uid("sr_"), analysis_id=row.id, idempotency_key=value.idempotency_key,
                                     request_hash=request_hash, body=body)
                session.add(review)
                row.status, row.phase = "reviewed", "已复核"
                self.store.audit(session, "sales.review_saved", row.id, body)
                session.flush()
                return row_dict(review)
        except IntegrityError:
            raise DomainError("复核发生并发冲突，请刷新；未重复创建辅导任务", 409) from None

    def dashboard(self, project_id, mode="live", playbook_id=None, salesperson=None, stage=None,
                  start=None, end=None):
        with self.sessions() as session:
            self.require_project(session, project_id)
            rows = session.execute(select(SalesAnalysis, SalesReview).join(
                SalesReview, SalesReview.analysis_id == SalesAnalysis.id).where(
                    SalesAnalysis.project_id == project_id, SalesAnalysis.mode == mode,
                    SalesAnalysis.status == "reviewed")).all()
        selected, groups, gaps, topics = [], defaultdict(list), Counter(), Counter()
        for analysis, review in rows:
            metadata = analysis.snapshot["conversation"]["body"]
            if playbook_id and analysis.playbook_id != playbook_id:
                continue
            if salesperson and metadata["salesperson"] != salesperson or stage and metadata["stage"] != stage:
                continue
            day = metadata.get("occurred_on")
            if (start or end) and (not day or start and day < start or end and day > end):
                continue
            selected.append((analysis, review))
        # Latest reviewed analysis per conversation; repeated analysis must not inflate samples.
        latest = {}
        for analysis, review in selected:
            previous = latest.get(analysis.conversation_id)
            if not previous or review.created_at > previous[1].created_at:
                latest[analysis.conversation_id] = (analysis, review)
        for analysis, review in latest.values():
            metadata = analysis.snapshot["conversation"]["body"]
            metrics = review.body["metrics"]
            groups[(metadata["salesperson"], analysis.playbook_id)].append(metrics)
            for item in review.body["decisions"]:
                if item["status"] in {"partial", "unmet"}:
                    rule = next(rule for rule in analysis.snapshot["playbook"]["body"]["rules"]
                                if rule["id"] == item["rule_id"])
                    gaps[(analysis.playbook_id, rule["title"])] += 1
            # Count unique conversations mentioning a topic, not raw mentions.
            for topic in {item["topic"] for item in analysis.report["insights"]}:
                topics[topic] += 1
        with self.sessions() as session:
            pending = session.scalar(select(SalesAnalysis.id).where(SalesAnalysis.project_id == project_id,
                SalesAnalysis.mode == mode, SalesAnalysis.status == "waiting_review").limit(1)) is not None
        def average(values, key):
            numbers = [value[key] for value in values if value[key] is not None]
            return round(sum(numbers) / len(numbers), 1) if numbers else None
        return {"mode": mode, "reviewed_conversations": len(latest), "has_pending_reviews": pending,
                "people": [{"salesperson": person, "playbook_id": card, "samples": len(values),
                    "average_score": average(values, "score"), "average_coverage": average(values, "coverage")}
                    for (person, card), values in groups.items()],
                "gaps": [{"playbook_id": card, "title": title, "conversations": count}
                         for (card, title), count in gaps.most_common()],
                "topics": [{"topic": topic, "conversations": count} for topic, count in topics.most_common()],
                "notice": "仅统计已复核会话，每会话采用筛选范围内最近一次复核；按评分卡版本分组，不含成交或收入结论。"}

    def export(self, analysis_id):
        detail = self.detail(analysis_id)
        row, review = detail["analysis"], detail["review"]
        if not row["report"]:
            raise DomainError("质检报告尚未生成", 409)
        metadata = row["snapshot"]["conversation"]["body"]
        report = review["body"] if review else row["report"]
        card = row["snapshot"]["playbook"]
        lines = [f"# {metadata['title']}", "", "规则演示（非模型语义结论）" if row["mode"] == "demo" else "真实模型分析",
                 "主管已复核" if review else "待主管复核草稿", f"评分卡：{card['body']['name']} v{card['version']}",
                 f"销售：{metadata['salesperson']}；客户：{metadata['customer']}；阶段：{metadata['stage']}",
                 f"评分与覆盖：{report['metrics']}", ""]
        if detail["stale"]:
            lines.append("注意：会话已有新版本，本报告仍基于冻结旧版本。")
        rules = {rule["id"]: rule for rule in card["body"]["rules"]}
        for item in report["decisions"]:
            lines += [f"## {rules[item['rule_id']]['title']} · {LABELS[item['status']]}", item["reason"],
                      f"权重：{item['weight']}；得分：{item['points']}；扣分：{item['deduction']}（None 表示不计分）",
                      f"复核说明：{item.get('review_reason', '保留机器判断')}",
                      f"辅导建议：{item.get('guidance', '')}", f"原文引用：{', '.join(item['evidence'])}", ""]
        lines += ["## 客户信号（自动提取，未逐条人工核准）"]
        lines += [f"- {item['text']}（{', '.join(item['evidence'])}）" for item in row["report"]["insights"]]
        lines += ["", "## 原文依据"]
        lines += [f"- [{span['id']}] {span['speaker']}：{span['text']}" for span in metadata["spans"]]
        return "\n\n".join(lines)

    def demo(self, project_id):
        from .sales_domain import AnalyzeInput, PlaybookInput, template
        card = self.publish(PlaybookInput(project_id=project_id, **template()))
        conversation = self.create_conversation(ConversationInput(project_id=project_id,
            title="客户需求访谈 · 合成演示", salesperson="演示销售小林", customer="演示客户A", stage="需求访谈",
            complete=True, transcript="销售：您目前怎么整理跨部门的采购需求？\n客户：现在靠表格，重复录入影响很大。\n"
            "销售：您说的数据重复影响审批效率，我理解对吗？\n客户：是，不过你们价格比另一家贵。\n"
            "销售：下一步安排演示，下周再联系您。\n客户：时间暂时还不能确认。"))
        analysis = self.analyze(conversation["id"], AnalyzeInput(playbook_id=card["id"], mode="demo"))
        return {"conversation": conversation, "analysis": analysis, "playbook": card}
