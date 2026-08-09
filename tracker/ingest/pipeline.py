from datetime import timedelta

import sqlalchemy as sa

from tracker.classify.gates import GMAIL_QUERY, run_gates
from tracker.classify.llm import ClassificationFailed, classify_email
from tracker.classify.prompts import PROMPT_VERSION
from tracker.classify.schemas import EmailIn
from tracker.ingest.gmail_client import HistoryExpired
from tracker.log import get_logger
from tracker.models import (Application, Classification, Email, FailedJob,
                            get_state, insert_email_idempotent, set_state)
from tracker.obligations.lifecycle import apply_closures, create_obligation_if_needed
from tracker.resolve.resolver import resolve_application
from tracker.state.events import append_event

MAX_STRIKES = 3
RESYNC_OVERLAP = timedelta(days=1)


def record_failure(session, email_id: str, stage: str, error: str) -> FailedJob:
    job = session.query(FailedJob).filter_by(email_id=email_id, stage=stage).one_or_none()
    if job:
        job.strikes += 1
        job.error = error
    else:
        # column defaults apply at INSERT, not construction — set strikes explicitly
        job = FailedJob(email_id=email_id, stage=stage, error=error, strikes=1)
        session.add(job)
    if job.strikes >= MAX_STRIKES:
        job.parked = True
    session.flush()
    return job


def process_email(session, email: EmailIn, anthropic_client, tz: str,
                  self_addr: str | None = None) -> str:
    log = get_logger(gmail_id=email.gmail_id)
    inserted = insert_email_idempotent(
        session, gmail_id=email.gmail_id, thread_id=email.thread_id,
        from_addr=email.from_addr, subject=email.subject,
        body_text=email.body_text, received_at=email.received_at,
        raw={"headers": email.headers})
    if not inserted:
        return "duplicate"

    gate = run_gates(email)
    if gate.route == "noise":
        log.info("gated_noise", reason=gate.reason)
        return "noise"

    candidates = (session.query(Application)
                  .order_by(Application.last_contact_at.desc())
                  .limit(100).all())
    try:
        cls, model = classify_email(email, gate, anthropic_client, candidates)
    except ClassificationFailed as e:
        record_failure(session, email.gmail_id, "classify", str(e))
        log.warning("classification_failed", error=str(e))
        return "failed"

    session.add(Classification(
        email_id=email.gmail_id, prompt_version=PROMPT_VERSION, model=model,
        category=cls.category, confidence=cls.confidence,
        extracted=cls.model_dump(mode="json")))

    if not cls.is_my_application and cls.category == "noise":
        return "noise"

    result = resolve_application(session, cls, email)
    if result.needs_review:
        record_failure(session, email.gmail_id, "review_pending",
                       result.review_reason or "unknown")
        log.info("sent_to_review", reason=result.review_reason)
        return "review"

    append_event(session, result.application_id, cls.category,
                 email.received_at, email_id=email.gmail_id)
    apply_closures(session, result.application_id, cls, email, self_addr=self_addr)
    # the user's own sent mail can close obligations but never owes them one
    if not (self_addr and email.from_addr.lower() == self_addr.lower()):
        create_obligation_if_needed(session, result.application_id, cls, email, tz)
    log.info("processed", application_id=result.application_id,
             category=cls.category, model=model)
    return "processed"


def _run_ids(session, gmail, anthropic_client, ids, tz: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    self_addr = gmail.profile_email()
    for message_id in ids:
        try:
            email = gmail.fetch_email(message_id)
            disp = process_email(session, email, anthropic_client, tz,
                                 self_addr=self_addr)
        except Exception as e:  # one bad email must never kill the run
            record_failure(session, message_id, "ingest", str(e))
            disp = "failed"
        counts[disp] = counts.get(disp, 0) + 1
        session.commit()
    return counts


def run_backfill(session, gmail, anthropic_client, after: str, tz: str) -> dict[str, int]:
    # Gmail lists newest-first; closure inference (receipts, next-stage) assumes
    # chronological arrival, so process oldest-first
    ids = list(gmail.list_message_ids(GMAIL_QUERY, after=after))
    counts = _run_ids(session, gmail, anthropic_client, reversed(ids), tz)
    set_state(session, "gmail_history_id", gmail.current_history_id())
    session.commit()
    return counts


def run_incremental(session, gmail, anthropic_client, tz: str) -> dict[str, int]:
    cursor = get_state(session, "gmail_history_id")
    if cursor is None:
        raise RuntimeError("no history cursor — run backfill first")
    try:
        ids, latest = gmail.list_history_ids(cursor)
        counts = _run_ids(session, gmail, anthropic_client, ids, tz)
        set_state(session, "gmail_history_id", latest)
    except HistoryExpired:
        get_logger().warning("history_expired_falling_back_to_date_resync")
        last = session.execute(sa.select(sa.func.max(Email.received_at))).scalar()
        after = (last - RESYNC_OVERLAP).strftime("%Y/%m/%d") if last else "2026/01/01"
        counts = _run_ids(session, gmail, anthropic_client,
                          gmail.list_message_ids(GMAIL_QUERY, after=after), tz)
        set_state(session, "gmail_history_id", gmail.current_history_id())
    session.commit()
    return counts
