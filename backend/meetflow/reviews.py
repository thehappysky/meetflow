from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from .database import Approval, Meeting, ProjectEvent, Report, Run, Task, row_dict
from .domain import Action, DomainError, ReviewInput, SourceInput, digest, now, uid


def approve(store, run_id: str, request: ReviewInput):
    request_hash = digest({"run_id": run_id, **request.model_dump(mode="json")})
    try:
        with store.sessions.begin() as session:
            if store.settings.db_url.startswith("sqlite"):
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            existing = session.scalar(select(Approval).where(Approval.idempotency_key == request.idempotency_key))
            if existing:
                if existing.request_hash != request_hash:
                    raise DomainError("同一幂等键不能用于不同的批准内容", 409)
                return row_dict(existing)
            run = session.get(Run, run_id)
            if not run:
                raise DomainError("运行不存在", 404)
            if run.status != "waiting_review":
                raise DomainError("当前运行不在等待审核状态", 409)
            report = session.scalar(select(Report).where(Report.run_id == run_id).order_by(Report.revision.desc()))
            if not report or report.revision != request.revision or report.content_hash != request.report_hash:
                raise DomainError("结果已变化，请刷新后确认当前版本", 409)
            if len(set(request.selected_actions)) != len(request.selected_actions):
                raise DomainError("重复选择行动项")
            actions = {action["id"]: action for action in report.body["actions"]}
            if set(request.selected_actions) - set(actions):
                raise DomainError("所选行动项不存在")
            blocked = {item["action_id"] for item in report.body["challenges"] if item["severity"] == "blocking"}
            if blocked.intersection(request.selected_actions):
                raise DomainError("所选行动仍有阻断性审查问题，请暂不选择该项或重新分析", 409)
            if request.selected_actions and any(item["severity"] == "blocking" and item["action_id"] is None
                                                for item in report.body["challenges"]):
                raise DomainError("方案仍有整体阻断问题，当前只可保存纪要", 409)
            for key, edits in request.edits.items():
                if key not in request.selected_actions or set(edits) - {"title", "owner", "due"}:
                    raise DomainError("只能编辑所选任务的标题、负责人和日期")
            applied, changes = [], []
            for key in request.selected_actions:
                raw = {**actions[key], **request.edits.get(key, {})}
                for question in report.body["questions"]:
                    answer = request.answers.get(question["key"], "").strip()
                    if question["key"].startswith("owner:") and key in question["affected_actions"] and answer and answer != "暂不确定":
                        raw["owner"] = answer
                action = Action.model_validate(raw)
                values = {"title": action.title, "owner": action.owner, "due": action.due.isoformat() if action.due else None,
                          "status": action.status, "conditions": action.conditions, "evidence": action.evidence,
                          "updated_at": now()}
                if action.target_task_id:
                    task = session.get(Task, action.target_task_id)
                    if not task or task.project_id != run.project_id or task.version != action.expected_version:
                        raise DomainError("源任务已变化，请重新核对相关操作", 409)
                    before = row_dict(task)
                    result = session.execute(update(Task).where(Task.id == task.id, Task.version == action.expected_version)
                                             .values(**values, version=Task.version + 1))
                    if result.rowcount != 1:
                        raise DomainError("任务被同时修改，请刷新后核对", 409)
                    task_id = task.id
                else:
                    before = None
                    task_id = uid("t_")
                    session.add(Task(id=task_id, project_id=run.project_id, meeting_id=run.meeting_id,
                                     version=1, **values))
                applied.append({"action_id": key, "task_id": task_id})
                changes.append({"task_id": task_id, "before": before, "after": action.model_dump(mode="json")})
                session.add(ProjectEvent(id=uid("e_"), project_id=run.project_id, kind="task.changed",
                                         body={"task_id": task_id, "run_id": run_id, "change": changes[-1]}))
            approval = Approval(id=uid("approval_"), run_id=run_id, report_id=report.id,
                                idempotency_key=request.idempotency_key, request_hash=request_hash,
                                body={"applied": applied, "changes": changes, "answers": request.answers,
                                      "operation_hash": digest(changes)})
            session.add(approval)
            run.status, run.updated_at = "approved", now()
            if changes:
                meeting = session.get(Meeting, run.meeting_id)
                lines = [f"会议：{meeting.title}；实际时间：{meeting.meeting_time or '未确认'}。以下仅为用户确认的任务变更。"]
                lines.extend(str(change["after"]) for change in changes)
                store.add_source(session, run.project_id, SourceInput(title=f"{meeting.title} · 已确认任务变化",
                                 kind="history", text="\n".join(lines)), run.meeting_id)
            store.audit(session, "review.approved", approval.id,
                        {"run_id": run_id, "report_hash": report.content_hash, "changes": changes})
            session.flush()
            return row_dict(approval)
    except IntegrityError:
        with store.sessions() as session:
            existing = session.scalar(select(Approval).where(Approval.idempotency_key == request.idempotency_key))
            if existing and existing.request_hash == request_hash:
                return row_dict(existing)
        raise DomainError("并发审核冲突，请刷新后重试", 409) from None


def render_markdown(meeting, run, report, approval=None, evidence=None):
    body = report["body"]
    lines = [f"# {meeting['title']}", "", f"会议时间：{meeting['meeting_time'] or '待确认'}",
             f"状态：{'已确认任务变更' if run['status'] == 'completed' else '草稿，尚未完成任务确认'}",
             f"分析模式：{'离线规则演示（无大模型）' if run['mode'] == 'demo' else '真实模型'}", "",
             "## 纪要", "", body["summary"], "", "## 任务候选", ""]
    for action in body["actions"]:
        lines.append(f"- {action['title']}｜负责人：{action.get('owner') or '待分配'}｜日期：{action.get('due') or '待确认'}")
        lines.extend(f"  - 条件：{condition}" for condition in action["conditions"])
    lines.extend(["", "## 风险与待确认", ""])
    lines.extend(f"- {item['text']}" for item in body["risks"] + body["questions"] + body["challenges"])
    if approval:
        lines.extend(["", "## 已确认任务变更", ""])
        for change in approval["body"]["changes"]:
            action = change["after"]
            lines.append(f"- {action['title']}｜负责人：{action.get('owner') or '待分配'}｜日期：{action.get('due') or '待确认'}")
    if evidence:
        lines.extend(["", "## 原文依据", ""])
        referenced = {ref for item in body["claims"] + body["actions"] + body["risks"] for ref in item["evidence"]}
        for ref in sorted(referenced):
            if ref in evidence:
                item = evidence[ref]
                lines.extend([f"- {ref} · {item['source_title']} · 版本 {item['source_version']}", f"  > {item['text']}"])
    return "\n".join(lines) + "\n"
