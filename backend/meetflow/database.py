from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, event
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


def make_database(settings):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False, "timeout": 30} if settings.db_url.startswith("sqlite") else {}
    engine = create_engine(settings.db_url, connect_args=connect_args)
    if settings.db_url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def sqlite_setup(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(engine, expire_on_commit=False)


def row_dict(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}
