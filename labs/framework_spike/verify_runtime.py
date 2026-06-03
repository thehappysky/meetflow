"""Executable runtime experiments; scripted nodes, no LLM or ASR evaluation.

Run: python verify_runtime.py
Uses temporary local SQLite files; performs no external actions.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.errors import InvalidUpdateError
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, Send, interrupt


def merge_immutable(left: dict, right: dict) -> dict:
    """Union immutable results; never choose a winner by arrival order."""
    merged = dict(left)
    for key, value in right.items():
        if key in merged and merged[key] != value:
            raise ValueError(f"Conflicting payload for immutable result {key}")
        merged[key] = value
    return dict(sorted(merged.items()))


class ParentState(TypedDict, total=False):
    jobs: list[dict]
    findings: Annotated[dict, merge_immutable]
    collected: bool


class SpecialistState(TypedDict, total=False):
    job: dict
    tool_reads: int
    private_note: str
    result: dict


def make_specialist():
    def observe(state: SpecialistState):
        return {
            "tool_reads": state.get("tool_reads", 0) + 1,
            "private_note": f"private:{state['job']['id']}",
        }

    def route(state: SpecialistState):
        return "observe" if state["tool_reads"] < 2 else "finish"

    def finish(state: SpecialistState):
        return {"result": {"source": state["job"]["source"], "reads": state["tool_reads"]}}

    graph = StateGraph(SpecialistState)
    graph.add_node("observe", observe)
    graph.add_node("finish", finish)
    graph.add_edge(START, "observe")
    graph.add_conditional_edges("observe", route)
    graph.add_edge("finish", END)
    return graph.compile()  # Per invocation; inherits parent's checkpointer.


def make_parent(checkpointer):
    specialist = make_specialist()

    def dispatch(state: ParentState):
        return [Send("worker", {"job": job}) for job in state["jobs"]] or "collect"

    def worker(state: SpecialistState):
        child = specialist.invoke({"job": state["job"]})
        return {"findings": {state["job"]["id"]: child["result"]}}

    def collect(state: ParentState):
        expected = {job["id"] for job in state["jobs"]}
        if set(state.get("findings", {})) != expected:
            raise ValueError("Incomplete round")
        return {"collected": True}

    graph = StateGraph(ParentState)
    graph.add_node("worker", worker)
    graph.add_node("collect", collect)
    graph.add_conditional_edges(START, dispatch)
    graph.add_edge("worker", "collect")
    graph.add_edge("collect", END)
    return graph.compile(checkpointer=checkpointer)


def make_review(checkpointer, visits):
    class ReviewState(TypedDict, total=False):
        report_revision: str
        approved: bool

    def review(state: ReviewState):
        visits.append("review_entered")
        answer = interrupt({"report_revision": state["report_revision"]})
        if answer.get("report_revision") != state["report_revision"]:
            raise ValueError("Approval refers to a different report revision")
        return {"approved": bool(answer.get("approved"))}

    graph = StateGraph(ReviewState)
    graph.add_node("review", review)
    graph.add_edge(START, "review")
    graph.add_edge("review", END)
    return graph.compile(checkpointer=checkpointer)


class RuntimeExperiments(unittest.TestCase):
    def test_dynamic_fanout_and_private_per_invocation_subgraphs(self):
        with SqliteSaver.from_conn_string(":memory:") as saver:
            graph = make_parent(saver)
            result = graph.invoke(
                {"jobs": [{"id": "history", "source": "minutes-v2"},
                          {"id": "tasks", "source": "board-v5"}], "findings": {}},
                {"configurable": {"thread_id": "parallel"}},
            )
            self.assertTrue(result["collected"])
            self.assertEqual(set(result["findings"]), {"history", "tasks"})
            self.assertEqual([r["reads"] for r in result["findings"].values()], [2, 2])
            self.assertNotIn("private_note", result)

    def test_parallel_writes_without_reducer_fail(self):
        class State(TypedDict):
            value: str

        graph = StateGraph(State)
        graph.add_node("left", lambda _: {"value": "L"})
        graph.add_node("right", lambda _: {"value": "R"})
        graph.add_edge(START, "left")
        graph.add_edge(START, "right")
        graph.add_edge("left", END)
        graph.add_edge("right", END)
        with self.assertRaises(InvalidUpdateError):
            graph.compile().invoke({"value": "initial"})

    def test_immutable_merge_is_idempotent_and_rejects_conflict(self):
        item = {"task:1": {"artifact": "hash-abc"}}
        self.assertEqual(merge_immutable(item, item), item)
        self.assertEqual(merge_immutable({"b": 2}, {"a": 1}),
                         merge_immutable({"a": 1}, {"b": 2}))
        with self.assertRaises(ValueError):
            merge_immutable(item, {"task:1": {"artifact": "hash-other"}})

    def test_explicit_join_waits_for_branches_of_different_depth(self):
        class State(TypedDict, total=False):
            left: bool
            right: bool
            joined: bool

        visits = []
        graph = StateGraph(State)
        graph.add_node("left", lambda _: {"left": True})
        graph.add_node("right_start", lambda _: {})
        graph.add_node("right_done", lambda _: {"right": True})

        def join(state):
            self.assertTrue(state["left"] and state["right"])
            visits.append("join")
            return {"joined": True}

        graph.add_node("join", join)
        graph.add_edge(START, "left")
        graph.add_edge(START, "right_start")
        graph.add_edge("right_start", "right_done")
        graph.add_edge(["left", "right_done"], "join")
        graph.add_edge("join", END)
        self.assertTrue(graph.compile().invoke({})["joined"])
        self.assertEqual(visits, ["join"])

    def test_sqlite_reopen_then_resume_reenters_interrupt_node(self):
        visits = []
        config = {"configurable": {"thread_id": "review-restart"}}
        with tempfile.TemporaryDirectory() as directory:
            db = str(Path(directory) / "checkpoints.sqlite")
            with SqliteSaver.from_conn_string(db) as saver:
                graph = make_review(saver, visits)
                paused = graph.invoke({"report_revision": "report-v3"}, config)
                self.assertTrue(paused["__interrupt__"])
                self.assertEqual(graph.get_state(config).next, ("review",))
            with SqliteSaver.from_conn_string(db) as saver:
                graph = make_review(saver, visits)
                result = graph.invoke(Command(resume={
                    "report_revision": "report-v3", "approved": True}), config)
                self.assertTrue(result["approved"])
                self.assertEqual(graph.get_state(config).next, ())
        self.assertEqual(visits, ["review_entered", "review_entered"])

    def test_stale_approval_is_rejected_by_application(self):
        with SqliteSaver.from_conn_string(":memory:") as saver:
            graph = make_review(saver, [])
            config = {"configurable": {"thread_id": "stale"}}
            graph.invoke({"report_revision": "report-v4"}, config)
            with self.assertRaisesRegex(ValueError, "different report revision"):
                graph.invoke(Command(resume={
                    "report_revision": "report-v3", "approved": True}), config)


if __name__ == "__main__":
    unittest.main(verbosity=2)
