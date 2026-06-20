import hashlib
import json
from datetime import date, datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

ROLES = ("meeting", "context", "planner", "risk", "auditor")
ROLE_NAMES = {"coordinator": "协作协调", "meeting": "会议理解", "context": "项目上下文",
              "planner": "执行规划", "risk": "风险与依赖", "auditor": "证据审查"}


def uid(prefix: str = "") -> str:
    return prefix + uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), default=str).encode()).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Span(Contract):
    id: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=20000)
    speaker: str | None = None
    start: float | None = Field(default=None, ge=0)
    end: float | None = Field(default=None, ge=0)


class SourceInput(Contract):
    title: str = Field(min_length=1, max_length=200)
    kind: Literal["transcript", "history", "requirement", "task_snapshot", "members"]
    text: str = Field(default="", max_length=200000)
    spans: list[Span] = Field(default_factory=list, max_length=2000)


class MeetingInput(Contract):
    project_id: str
    title: str = Field(default="未命名会议", min_length=1, max_length=200)
    meeting_time: datetime | None = None
    timezone: str = "Asia/Shanghai"
    transcript: str = Field(min_length=2, max_length=200000)
    mode: Literal["demo", "live"] = "demo"

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("无效时区") from None
        return value


class Delegation(Contract):
    id: str = Field(min_length=1, max_length=80)
    role: Literal["meeting", "context", "planner", "risk", "auditor"]
    goal: str = Field(min_length=1, max_length=1500)
    depends_on: list[str] = Field(default_factory=list, max_length=12)


class Claim(Contract):
    text: str = Field(min_length=1, max_length=3000)
    kind: Literal["fact", "decision", "commitment", "suggestion", "condition", "unresolved"]
    evidence: list[str] = Field(min_length=1, max_length=20)


class Action(Contract):
    id: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=300)
    owner: str | None = None
    due: date | None = None
    status: Literal["todo", "in_progress", "blocked", "paused", "done", "cancelled"] = "todo"
    conditions: list[str] = Field(default_factory=list, max_length=20)
    evidence: list[str] = Field(min_length=1, max_length=20)
    target_task_id: str | None = None
    expected_version: int | None = Field(default=None, ge=1)
    rationale: str = Field(default="", max_length=3000)


class Question(Contract):
    key: str = Field(min_length=1, max_length=120)
    text: str = Field(min_length=1, max_length=1200)
    options: list[str] = Field(default_factory=list, max_length=20)
    affected_actions: list[str] = Field(default_factory=list, max_length=30)
    evidence: list[str] = Field(default_factory=list, max_length=20)


class Challenge(Contract):
    text: str = Field(min_length=1, max_length=2000)
    action_id: str | None = None
    severity: Literal["warning", "blocking"] = "warning"
    evidence: list[str] = Field(min_length=1, max_length=20)


class AgentResult(Contract):
    summary: str = Field(default="", max_length=8000)
    claims: list[Claim] = Field(default_factory=list, max_length=100)
    actions: list[Action] = Field(default_factory=list, max_length=50)
    risks: list[Claim] = Field(default_factory=list, max_length=50)
    questions: list[Question] = Field(default_factory=list, max_length=30)
    challenges: list[Challenge] = Field(default_factory=list, max_length=30)
    delegations: list[Delegation] = Field(default_factory=list, max_length=12)


class ReviewInput(Contract):
    revision: int = Field(ge=1)
    report_hash: str
    selected_actions: list[str] = Field(max_length=50)
    edits: dict[str, dict] = Field(default_factory=dict)
    answers: dict[str, str] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=8, max_length=120)


class TaskUpdate(Contract):
    version: int = Field(ge=1)
    status: Literal["todo", "in_progress", "blocked", "paused", "done", "cancelled"]


class DomainError(Exception):
    def __init__(self, message: str, status: int = 400):
        self.message, self.status = message, status
        super().__init__(message)
