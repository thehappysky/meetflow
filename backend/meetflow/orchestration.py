import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt
from sqlalchemy import select

from .agents import AgentRunner
from .database import Approval, Run
from .domain import ROLE_NAMES, Delegation, DomainError


def merge_refs(left, right):
    result = dict(left)
    for key, value in right.items():
        if key in result and result[key] != value:
            raise DomainError("并行任务产物引用冲突", 409)
        result[key] = value
    return result


def validate_plan(jobs):
    values = [Delegation.model_validate(job).model_dump() for job in jobs]
    index = {value["id"]: value for value in values}
    if not values or len(index) != len(values) or len(values) > 12:
        raise DomainError("委派计划为空、编号重复或任务数超限")
    visiting, done = set(), set()

    def visit(key):
        if key not in index:
            raise DomainError("计划引用不存在的依赖")
        if key in visiting:
            raise DomainError("计划存在依赖环")
        if key in done:
            return
        visiting.add(key)
        for dependency in index[key]["depends_on"]:
            visit(dependency)
        visiting.remove(key)
        done.add(key)

    for key in index:
        visit(key)
    auditors = [value for value in values if value["role"] == "auditor"]
    if len(auditors) != 1 or not any(value["role"] == "planner" for value in values):
        raise DomainError("计划须包含一个审查任务和执行规划任务")
    ancestors = set()

    def collect(key):
        for dependency in index[key]["depends_on"]:
            if dependency not in ancestors:
                ancestors.add(dependency)
                collect(dependency)

    collect(auditors[0]["id"])
    if ancestors != set(index) - {auditors[0]["id"]}:
        raise DomainError("审查任务未依赖全部前置调查结果")
    return values


class ParentState(TypedDict, total=False):
    run_id: str
    generation: int
    plan: list[dict]
    results: Annotated[dict[str, str], merge_refs]
    report_id: str
    approval_id: str


def build_graph(store, checkpointer, provider=None):
    agents = AgentRunner(store, provider)

    def upstream(run_id):
        return {row["task_key"]: {"role": row["role"], "result": row["body"]}
                for row in store.artifacts(run_id)}

    def plan(state):
        run_id = state["run_id"]
        store.update_run(run_id, phase="安排协作调查")
        cached = next((row for row in store.artifacts(run_id) if row["task_key"] == "coordinator"), None)
        if cached:
            result = cached["body"]
        else:
            result = agents.run(run_id, "coordinator", "根据当前资料规划本次协作任务", {})
            store.commit_artifact(run_id, "coordinator", "coordinator", result)
        jobs = validate_plan(result["delegations"])
        store.event(run_id, "plan", "协作任务已安排", {"tasks": jobs})
        return {"generation": 0, "plan": jobs}

    def dispatch(state):
        generation = state["generation"]
        ready = [job for job in state["plan"] if f"{generation}:{job['id']}" not in state["results"]
                 and all(f"{generation}:{key}" in state["results"] for key in job["depends_on"])]
        if ready:
            return [Send("specialist", {"run_id": state["run_id"], "generation": generation, "job": job})
                    for job in ready]
        if all(f"{generation}:{job['id']}" in state["results"] for job in state["plan"]):
            return "inspect"
        raise DomainError("没有可执行任务且仍有缺失结果")

    def specialist(state):
        run_id, job = state["run_id"], state["job"]
        key = f"{state['generation']}:{job['id']}"
        cached = next((row for row in store.artifacts(run_id) if row["task_key"] == key), None)
        if cached:
            return {"results": {key: cached["id"]}}
        store.check_active(run_id)
        store.update_run(run_id, phase=ROLE_NAMES[job["role"]])
        store.event(run_id, "agent_started", f"{ROLE_NAMES[job['role']]}开始处理", {"task": job, "generation": state["generation"]})
        result = agents.run(run_id, job["role"], job["goal"], upstream(run_id))
        artifact_id = store.commit_artifact(run_id, key, job["role"], result)
        store.event(run_id, "agent_finished", f"{ROLE_NAMES[job['role']]}已提交结果", {"role": job["role"], "artifact_id": artifact_id})
        return {"results": {key: artifact_id}}

    def inspect(state):
        artifacts = store.artifacts(state["run_id"])
        auditor = next(row for row in reversed(artifacts) if row["role"] == "auditor")
        if any(item["severity"] == "blocking" for item in auditor["body"]["challenges"]) and state["generation"] < 1:
            store.event(state["run_id"], "repair", "审查发现问题，定向修订执行方案", {"challenges": auditor["body"]["challenges"]})
            jobs = validate_plan([
                {"id": "planner-repair", "role": "planner", "goal": "结合审查反证修订执行方案，保留有效行动ID", "depends_on": []},
                {"id": "auditor-recheck", "role": "auditor", "goal": "复核修订方案是否解决具体挑战", "depends_on": ["planner-repair"]},
            ])
            return Command(update={"generation": state["generation"] + 1, "plan": jobs}, goto="dispatch")
        return Command(goto="report")

    def make_report(state):
        run_id = state["run_id"]
        artifacts = store.artifacts(run_id)
        latest = {row["role"]: row["body"] for row in artifacts}
        meeting, planner, risk, auditor = (latest.get(role, {}) for role in ("meeting", "planner", "risk", "auditor"))
        questions = {}
        for result in latest.values():
            for question in result.get("questions", []):
                if question["key"] not in questions:
                    questions[question["key"]] = question
                else:
                    current = questions[question["key"]]
                    for key in ("affected_actions", "evidence", "options"):
                        current[key] = sorted(set(current[key] + question[key]))
        run = store.get_run(run_id)
        body = {"mode": run["mode"], "summary": meeting.get("summary", ""),
                "claims": meeting.get("claims", []), "actions": planner.get("actions", []),
                "risks": risk.get("risks", []), "questions": list(questions.values()),
                "challenges": auditor.get("challenges", []), "audit_summary": auditor.get("summary", ""),
                "source_count": len(run["manifest"]["sources"])}
        report = store.save_report(run_id, body)
        store.update_run(run_id, status="waiting_review", phase="等待确认")
        store.event(run_id, "report_ready", "结果已整理，请核对任务变化", {"revision": report["revision"]})
        return {"report_id": report["id"]}

    def review(state):
        answer = interrupt({"report_id": state["report_id"], "message": "确认所选任务变化后继续"})
        with store.sessions() as session:
            approval = session.get(Approval, answer.get("approval_id"))
            if not approval or approval.run_id != state["run_id"] or approval.report_id != state["report_id"]:
                raise DomainError("恢复值未匹配已保存的审批", 409)
        return {"approval_id": answer["approval_id"]}

    def finish(state):
        store.update_run(state["run_id"], status="completed", phase="已完成")
        store.event(state["run_id"], "completed", "所选任务已记录，会议处理完成", {})
        return {}

    graph = StateGraph(ParentState)
    graph.add_node("plan", plan)
    graph.add_node("dispatch", lambda _: {})
    graph.add_node("specialist", specialist)
    graph.add_node("inspect", inspect)
    graph.add_node("report", make_report)
    graph.add_node("review", review)
    graph.add_node("finish", finish)
    graph.add_edge(START, "plan")
    graph.add_edge("plan", "dispatch")
    graph.add_conditional_edges("dispatch", dispatch)
    graph.add_edge("specialist", "dispatch")
    graph.add_edge("report", "review")
    graph.add_edge("review", "finish")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer)


class RunDriver:
    """One local driver; no distributed failover claim."""
    def __init__(self, store, provider=None):
        self.store, self.provider = store, provider
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="meetflow-runner")
        self.active, self.lock = set(), threading.Lock()

    def submit(self, run_id, resume=None):
        with self.lock:
            if run_id in self.active:
                return
            self.active.add(run_id)
        self.executor.submit(self._work, run_id, resume)

    def _work(self, run_id, resume=None):
        try:
            self.process(run_id, resume)
        finally:
            with self.lock:
                self.active.discard(run_id)

    def process(self, run_id, resume=None):
        self.store.begin_processing(run_id)
        try:
            run = self.store.get_run(run_id)
            if run["status"] in {"completed", "cancelled"}:
                return
            self.store.check_active(run_id)
            self.store.update_run(run_id, status="running", error=None)
            from .audio import ensure_transcript
            ensure_transcript(self.store, run_id)
            with SqliteSaver.from_conn_string(str(self.store.settings.data_dir / "checkpoints.db")) as saver:
                graph = build_graph(self.store, saver, self.provider)
                config = {"configurable": {"thread_id": run_id}, "max_concurrency": 2, "recursion_limit": 100}
                snapshot = graph.get_state(config)
                if resume is None and run["status"] == "approved":
                    with self.store.sessions() as session:
                        approval = session.scalar(select(Approval).where(Approval.run_id == run_id))
                        resume = {"approval_id": approval.id} if approval else None
                value = Command(resume=resume) if resume else (None if snapshot.values else {
                    "run_id": run_id, "generation": 0, "results": {}})
                graph.invoke(value, config)
        except Exception as exc:  # noqa: BLE001 -- worker boundary must persist unexpected failures
            cancelled = self.store.get_run(run_id)["cancel_requested"]
            error = str(exc)[:2000]
            self.store.update_run(run_id, status="cancelled" if cancelled else "failed",
                                  phase="已停止" if cancelled else "处理失败", error=error)
            self.store.event(run_id, "failed", "处理已停止" if cancelled else "处理失败，可查看原因并重试",
                             {"error": error, "type": type(exc).__name__})
        finally:
            self.store.end_processing(run_id)

    def recover(self):
        with self.store.sessions() as session:
            rows = session.scalars(select(Run).where(Run.status.in_(["queued", "running", "approved"]))).all()
            for run in rows:
                self.submit(run.id)

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
