from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from .domain import now


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[str] = mapped_column(default=now)


class Meeting(Base):
    __tablename__ = "meetings"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    meeting_time: Mapped[str | None]
    timezone: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=now)
    audio_path: Mapped[str | None]


class MeetingContact(Base):
    """Optional sales attribution; an additive table preserves existing meeting rows."""
    __tablename__ = "meeting_contacts"
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), primary_key=True)
    salesperson: Mapped[str] = mapped_column(String(100))
    customer: Mapped[str] = mapped_column(String(150))


class Source(Base):
    __tablename__ = "sources"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    meeting_id: Mapped[str | None] = mapped_column(ForeignKey("meetings.id"))
    title: Mapped[str]
    kind: Mapped[str]
    version: Mapped[int] = mapped_column(default=1)
    content_hash: Mapped[str]
    body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(default=now)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    mode: Mapped[str]
    status: Mapped[str] = mapped_column(default="queued", index=True)
    phase: Mapped[str] = mapped_column(default="等待处理")
    manifest: Mapped[dict] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    model_calls: Mapped[int] = mapped_column(default=0)
    tool_calls: Mapped[int] = mapped_column(default=0)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(default=now)
    updated_at: Mapped[str] = mapped_column(default=now)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("run_id", "task_key"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    task_key: Mapped[str]
    role: Mapped[str]
    body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(default=now)


class RunEvent(Base):
    __tablename__ = "run_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    kind: Mapped[str]
    message: Mapped[str]
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(default=now)


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("run_id", "revision"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    revision: Mapped[int]
    body: Mapped[dict] = mapped_column(JSON)
    content_hash: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=now)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    meeting_id: Mapped[str | None] = mapped_column(ForeignKey("meetings.id"))
    title: Mapped[str]
    owner: Mapped[str | None]
    due: Mapped[str | None]
    status: Mapped[str]
    conditions: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    version: Mapped[int] = mapped_column(default=1)
    updated_at: Mapped[str] = mapped_column(default=now)


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), index=True)
    report_id: Mapped[str] = mapped_column(ForeignKey("reports.id"))
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    request_hash: Mapped[str]
    body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(default=now)


class ProjectEvent(Base):
    __tablename__ = "project_events"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    kind: Mapped[str]
    body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(default=now)


class Audit(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    action: Mapped[str]
    target_id: Mapped[str]
    body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(default=now)


class SalesPlaybook(Base):
    __tablename__ = "sales_playbooks"
    __table_args__ = (UniqueConstraint("family_id", "version"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    family_id: Mapped[str]
    version: Mapped[int]
    body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(default=now)


class SalesConversation(Base):
    __tablename__ = "sales_conversations"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    body: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(default="ready")
    error: Mapped[str | None] = mapped_column(Text)
    audio_path: Mapped[str | None]
    created_at: Mapped[str] = mapped_column(default=now)


class SalesAnalysis(Base):
    __tablename__ = "sales_analyses"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("sales_conversations.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    playbook_id: Mapped[str] = mapped_column(ForeignKey("sales_playbooks.id"))
    snapshot: Mapped[dict] = mapped_column(JSON)
    mode: Mapped[str]
    status: Mapped[str] = mapped_column(default="queued")
    phase: Mapped[str] = mapped_column(default="等待质检")
    artifacts: Mapped[dict] = mapped_column(JSON, default=dict)
    events: Mapped[list] = mapped_column(JSON, default=list)
    report: Mapped[dict | None] = mapped_column(JSON)
    report_hash: Mapped[str | None]
    error: Mapped[str | None] = mapped_column(Text)
    model_calls: Mapped[int] = mapped_column(default=0)
    tool_calls: Mapped[int] = mapped_column(default=0)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(default=now)


class SalesReview(Base):
    __tablename__ = "sales_reviews"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    analysis_id: Mapped[str] = mapped_column(ForeignKey("sales_analyses.id"), unique=True)
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    request_hash: Mapped[str]
    body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(default=now)


class Company(Base):
    __tablename__ = "access_companies"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str]


class Team(Base):
    __tablename__ = "access_teams"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("access_companies.id"), index=True)
    name: Mapped[str]


class Account(Base):
    __tablename__ = "access_accounts"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("access_companies.id"), index=True)
    team_id: Mapped[str | None] = mapped_column(ForeignKey("access_teams.id"))
    username: Mapped[str] = mapped_column(String(120), unique=True)
    name: Mapped[str]
    role: Mapped[str]
    password_hash: Mapped[str]
    active: Mapped[bool] = mapped_column(default=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"))
    created_at: Mapped[str] = mapped_column(default=now)


class AccessSession(Base):
    __tablename__ = "access_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[str] = mapped_column(ForeignKey("access_accounts.id"), index=True)
    expires_at: Mapped[str]


class Invitation(Base):
    __tablename__ = "access_invitations"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("access_companies.id"))
    team_id: Mapped[str] = mapped_column(ForeignKey("access_teams.id"))
    created_by: Mapped[str] = mapped_column(ForeignKey("access_accounts.id"))
    expires_at: Mapped[str]
    used_by: Mapped[str | None] = mapped_column(ForeignKey("access_accounts.id"))
    revoked: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(default=now)


class PersonalRecording(Base):
    __tablename__ = "workspace_recordings"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("access_accounts.id"), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("access_companies.id"), index=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("access_teams.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), unique=True)
    body: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(default=1)
    deleted: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(default=now)


class Submission(Base):
    __tablename__ = "workspace_submissions"
    __table_args__ = (UniqueConstraint("recording_id"), UniqueConstraint("request_key"))
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    recording_id: Mapped[str] = mapped_column(ForeignKey("workspace_recordings.id"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("access_accounts.id"))
    company_id: Mapped[str] = mapped_column(ForeignKey("access_companies.id"), index=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("access_teams.id"))
    snapshot: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(default=1)
    history: Mapped[list] = mapped_column(JSON, default=list)
    request_key: Mapped[str]
    request_hash: Mapped[str]
    deleted: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(default=now)


class Customer(Base):
    __tablename__ = "workspace_customers"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("access_companies.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("access_accounts.id"))
    team_id: Mapped[str] = mapped_column(ForeignKey("access_teams.id"))
    name: Mapped[str]
    created_at: Mapped[str] = mapped_column(default=now)


class Opportunity(Base):
    __tablename__ = "workspace_opportunities"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    customer_id: Mapped[str] = mapped_column(ForeignKey("workspace_customers.id"))
    company_id: Mapped[str] = mapped_column(ForeignKey("access_companies.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("access_accounts.id"))
    team_id: Mapped[str] = mapped_column(ForeignKey("access_teams.id"))
    title: Mapped[str]
    stage: Mapped[str] = mapped_column(default="待确认")
    version: Mapped[int] = mapped_column(default=1)
    history: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[str] = mapped_column(default=now)


class Followup(Base):
    __tablename__ = "workspace_followups"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    scope_key: Mapped[str] = mapped_column(unique=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("access_companies.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("access_accounts.id"))
    team_id: Mapped[str] = mapped_column(ForeignKey("access_teams.id"))
    source_kind: Mapped[str]
    source_id: Mapped[str]
    source_revision: Mapped[int]
    body: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(default=1)
    history: Mapped[list] = mapped_column(JSON, default=list)
    updated_at: Mapped[str] = mapped_column(default=now)


class UploadReceipt(Base):
    __tablename__ = "workspace_upload_receipts"
    __table_args__ = (UniqueConstraint("owner_id", "request_key"), UniqueConstraint("owner_id", "content_hash"))
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("access_accounts.id"))
    request_key: Mapped[str]
    request_hash: Mapped[str]
    content_hash: Mapped[str | None]
    recording_id: Mapped[str] = mapped_column(ForeignKey("workspace_recordings.id"))
    created_at: Mapped[str] = mapped_column(default=now)


class SalesReport(Base):
    __tablename__ = "workspace_sales_reports"
    __table_args__ = (UniqueConstraint("owner_id", "request_key"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("access_accounts.id"))
    company_id: Mapped[str] = mapped_column(ForeignKey("access_companies.id"), index=True)
    body: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(default=1)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("workspace_sales_reports.id"))
    request_key: Mapped[str]
    request_hash: Mapped[str]
    confirmed_at: Mapped[str | None]
    created_at: Mapped[str] = mapped_column(default=now)


class MeetingClarification(Base):
    __tablename__ = "workspace_meeting_clarifications"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    recording_id: Mapped[str] = mapped_column(ForeignKey("workspace_recordings.id"), index=True)
    actor_id: Mapped[str] = mapped_column(ForeignKey("access_accounts.id"))
    source_version: Mapped[int]
    body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(default=now)


class QualityPolicy(Base):
    __tablename__ = "workspace_quality_policies"
    company_id: Mapped[str] = mapped_column(ForeignKey("access_companies.id"), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    cards: Mapped[dict] = mapped_column(JSON, default=dict)


class QualityCase(Base):
    __tablename__ = "workspace_quality_cases"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    scope_key: Mapped[str] = mapped_column(unique=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("access_companies.id"), index=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("access_accounts.id"))
    team_id: Mapped[str] = mapped_column(ForeignKey("access_teams.id"))
    source_kind: Mapped[str]
    source_id: Mapped[str]
    source_revision: Mapped[int]
    body: Mapped[dict] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(default=1)
    analysis_ids: Mapped[list] = mapped_column(JSON, default=list)
    active_analysis_id: Mapped[str | None] = mapped_column(ForeignKey("sales_analyses.id"))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(default=now)
    updated_at: Mapped[str] = mapped_column(default=now)


class QualityReview(Base):
    __tablename__ = "workspace_quality_reviews"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("workspace_quality_cases.id"))
    analysis_id: Mapped[str] = mapped_column(ForeignKey("sales_analyses.id"))
    actor_id: Mapped[str] = mapped_column(ForeignKey("access_accounts.id"))
    request_hash: Mapped[str] = mapped_column(unique=True)
    body: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(default=now)


class QualityFeedback(Base):
    __tablename__ = "workspace_quality_feedback"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("workspace_quality_cases.id"))
    analysis_id: Mapped[str] = mapped_column(ForeignKey("sales_analyses.id"))
    requester_id: Mapped[str] = mapped_column(ForeignKey("access_accounts.id"))
    body: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(default="pending")
    resolution: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("access_accounts.id"))
    created_at: Mapped[str] = mapped_column(default=now)


class Correction(Base):
    __tablename__ = "workspace_corrections"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    submission_id: Mapped[str] = mapped_column(ForeignKey("workspace_submissions.id"))
    requester_id: Mapped[str] = mapped_column(ForeignKey("access_accounts.id"))
    base_revision: Mapped[int]
    proposed: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str]
    status: Mapped[str] = mapped_column(default="pending")
    reviewed_by: Mapped[str | None] = mapped_column(ForeignKey("access_accounts.id"))
    created_at: Mapped[str] = mapped_column(default=now)


class WorkLease(Base):
    __tablename__ = 'work_leases'
    id: Mapped[str] = mapped_column(String(160), primary_key=True)
    owner: Mapped[str | None] = mapped_column(String(120))
    generation: Mapped[int] = mapped_column(default=0)
    checkpoint_epoch: Mapped[int] = mapped_column(default=0)
    expires_at: Mapped[float] = mapped_column(Float, default=0)
    attempts: Mapped[int] = mapped_column(default=0)


class AgentConversation(Base):
    __tablename__ = 'agent_conversations'
    __table_args__ = (UniqueConstraint('run_id', 'task_key'),)
    id: Mapped[str] = mapped_column(String(240), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('runs.id'), index=True)
    task_key: Mapped[str]
    fingerprint: Mapped[str] = mapped_column(String(64))
    steps: Mapped[int]
    messages: Mapped[list] = mapped_column(JSON)


def make_database(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False, "timeout": 30} if settings.db_url.startswith("sqlite") else {}
    engine = create_engine(settings.db_url, connect_args=connect_args)
    if settings.db_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def sqlite_setup(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
    from .schema import migrate
    from .workspace_lock import workspace_lock
    with workspace_lock(settings.data_dir):
        migrate(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)


def row_dict(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}
