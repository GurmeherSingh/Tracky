from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JSONVariant = sa.JSON().with_variant(JSONB(), "postgresql")


class TZDateTime(sa.TypeDecorator):
    """timestamptz semantics on every backend: aware in, aware out.

    SQLite drops tzinfo on round-trip; without this, loaded rows compare
    as naive against aware values and raise TypeError.
    """
    impl = sa.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is None:
            raise ValueError("naive datetime written to database")
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


class Email(Base):
    __tablename__ = "emails"
    gmail_id: Mapped[str] = mapped_column(sa.String, primary_key=True)
    thread_id: Mapped[str] = mapped_column(sa.String, index=True)
    from_addr: Mapped[str] = mapped_column(sa.String)
    subject: Mapped[str] = mapped_column(sa.String, default="")
    body_text: Mapped[str] = mapped_column(sa.Text, default="")
    received_at: Mapped[datetime] = mapped_column(TZDateTime)
    raw: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    ingested_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class Classification(Base):
    __tablename__ = "classifications"
    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    email_id: Mapped[str] = mapped_column(sa.ForeignKey("emails.gmail_id"), index=True)
    prompt_version: Mapped[str] = mapped_column(sa.String)
    model: Mapped[str] = mapped_column(sa.String)
    category: Mapped[str] = mapped_column(sa.String)
    confidence: Mapped[float] = mapped_column(sa.Float)
    extracted: Mapped[dict] = mapped_column(JSONVariant, default=dict)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class Application(Base):
    __tablename__ = "applications"
    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    company_canonical: Mapped[str] = mapped_column(sa.String, index=True)
    company_display: Mapped[str] = mapped_column(sa.String)
    role_title: Mapped[str] = mapped_column(sa.String, default="")
    source: Mapped[str] = mapped_column(sa.String)  # applied | lead
    first_seen_at: Mapped[datetime] = mapped_column(TZDateTime)
    last_contact_at: Mapped[datetime] = mapped_column(TZDateTime)
    status_derived: Mapped[str] = mapped_column(sa.String, default="applied")
    human_engaged: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    notion_page_id: Mapped[str | None] = mapped_column(sa.String, nullable=True)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(sa.ForeignKey("applications.id"), index=True)
    email_id: Mapped[str | None] = mapped_column(sa.ForeignKey("emails.gmail_id"), nullable=True)
    kind: Mapped[str] = mapped_column(sa.String)
    occurred_at: Mapped[datetime] = mapped_column(TZDateTime)


class Obligation(Base):
    __tablename__ = "obligations"
    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(sa.ForeignKey("applications.id"), index=True)
    type: Mapped[str] = mapped_column(sa.String)
    title: Mapped[str] = mapped_column(sa.String)
    due_at: Mapped[datetime] = mapped_column(TZDateTime)
    due_confidence: Mapped[str] = mapped_column(sa.String)  # stated | assumed
    status: Mapped[str] = mapped_column(sa.String, default="open")
    effort_minutes: Mapped[int] = mapped_column(sa.Integer)
    next_alert_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True, index=True)
    alert_tier: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    closed_by: Mapped[str | None] = mapped_column(sa.String, nullable=True)


class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    obligation_id: Mapped[int] = mapped_column(sa.ForeignKey("obligations.id"), index=True)
    channel: Mapped[str] = mapped_column(sa.String)
    tier: Mapped[str] = mapped_column(sa.String)
    sent_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)
    slack_ts: Mapped[str | None] = mapped_column(sa.String, nullable=True)
    model_confidence: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    marked_junk: Mapped[bool] = mapped_column(sa.Boolean, default=False)


class EvalLabel(Base):
    __tablename__ = "eval_labels"
    email_id: Mapped[str] = mapped_column(sa.ForeignKey("emails.gmail_id"), primary_key=True)
    true_category: Mapped[str] = mapped_column(sa.String)
    true_deadline: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    labeled_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)


class SyncState(Base):
    __tablename__ = "sync_state"
    key: Mapped[str] = mapped_column(sa.String, primary_key=True)
    value: Mapped[str] = mapped_column(sa.String)


class FailedJob(Base):
    __tablename__ = "failed_jobs"
    id: Mapped[int] = mapped_column(sa.Integer, primary_key=True)
    email_id: Mapped[str] = mapped_column(sa.String, index=True)
    stage: Mapped[str] = mapped_column(sa.String)
    error: Mapped[str] = mapped_column(sa.Text)
    strikes: Mapped[int] = mapped_column(sa.Integer, default=1)
    parked: Mapped[bool] = mapped_column(sa.Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow, onupdate=utcnow)


def insert_email_idempotent(session, **cols) -> bool:
    """Portable insert-if-absent keyed on gmail_id (works on SQLite and Postgres)."""
    if session.get(Email, cols["gmail_id"]) is not None:
        return False
    try:
        with session.begin_nested():
            session.add(Email(**cols))
        return True
    except IntegrityError:
        return False


def wipe_all_data(session) -> None:
    """Delete every row in every table (validation runs only — data never persists).

    Reversed sorted_tables order respects FK dependencies (children first).
    """
    for table in reversed(Base.metadata.sorted_tables):
        session.execute(table.delete())
    session.flush()


def get_state(session, key: str) -> str | None:
    row = session.get(SyncState, key)
    return row.value if row else None


def set_state(session, key: str, value: str) -> None:
    row = session.get(SyncState, key)
    if row:
        row.value = value
    else:
        session.add(SyncState(key=key, value=value))
