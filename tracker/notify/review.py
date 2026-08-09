from tracker.classify.schemas import EmailClassification, EmailIn
from tracker.models import (Application, Classification, Email, FailedJob,
                            get_state, set_state)
from tracker.notify.fmt import gmail_link
from tracker.notify.slack import SlackNotifier
from tracker.obligations.lifecycle import apply_closures, create_obligation_if_needed
from tracker.resolve.resolver import _is_variant, _join_key
from tracker.state.events import append_event

EXCERPT_CHARS = 160


def _rehydrate(session, email_id: str) -> tuple[EmailIn, EmailClassification]:
    row = session.get(Email, email_id)
    email = EmailIn(gmail_id=row.gmail_id, thread_id=row.thread_id,
                    from_addr=row.from_addr, subject=row.subject,
                    body_text=row.body_text, received_at=row.received_at,
                    headers=row.raw.get("headers", {}))
    cls_row = (session.query(Classification).filter_by(email_id=email_id)
               .order_by(Classification.created_at.desc()).first())
    return email, EmailClassification.model_validate(cls_row.extracted)


def is_review_resolved(session, email_id: str) -> bool:
    job = session.query(FailedJob).filter_by(
        email_id=email_id, stage="review_pending").one_or_none()
    return bool(job and job.parked)


def resolved_blocks(blocks: list, note: str, keep_actions: bool = False) -> list:
    """Stamp a decision on a message; strip its buttons unless told to keep
    them (a refused snooze must leave the alert actionable). Old context lines
    are replaced, so repeated updates don't stack."""
    kept = [b for b in blocks
            if b.get("type") != "context"
            and (keep_actions or b.get("type") != "actions")]
    kept.append({"type": "context",
                 "elements": [{"type": "mrkdwn", "text": note}]})
    return kept


def _park(session, email_id: str, note: str | None = None) -> None:
    job = session.query(FailedJob).filter_by(
        email_id=email_id, stage="review_pending").one_or_none()
    if job:
        job.parked = True
        if note:
            job.error = note


def post_review_item(session, notifier: SlackNotifier, email_row: Email,
                     cls_row: Classification,
                     candidates: list[Application]) -> str:
    first_line = next(
        (ln.strip() for ln in email_row.body_text.splitlines() if ln.strip()), "")
    header = (f"🔎 *Review needed* — `{cls_row.category}` "
              f"(confidence {cls_row.confidence:.2f})\n"
              f"*From:* {email_row.from_addr}\n*Subject:* {email_row.subject} "
              f"(<{gmail_link(email_row.gmail_id)}|open email>)\n"
              f"_{first_line[:EXCERPT_CHARS]}_")
    elements = [{"type": "button", "action_id": "review_new",
                 "text": {"type": "plain_text", "text": "New application"},
                 "value": email_row.gmail_id}]
    for app in candidates[:3]:
        label = f"Belongs to {app.company_display} — {app.role_title}"[:75]
        # slack requires unique action_ids per message; bolt matches by regex
        elements.append({"type": "button", "action_id": f"review_assign_{app.id}",
                         "text": {"type": "plain_text", "text": label},
                         "value": f"{email_row.gmail_id}:{app.id}"})
    elements.append({"type": "button", "action_id": "review_ignore",
                     "text": {"type": "plain_text", "text": "Ignore"},
                     "value": email_row.gmail_id})
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": header}},
              {"type": "actions", "elements": elements}]
    return notifier.post_tracker(f"Review needed: {email_row.subject}", blocks=blocks)


def pump_review_queue(session, notifier: SlackNotifier) -> int:
    posted = 0
    jobs = session.query(FailedJob).filter_by(stage="review_pending",
                                              parked=False).all()
    for job in jobs:
        if get_state(session, f"review_posted:{job.email_id}"):
            continue
        email_row = session.get(Email, job.email_id)
        cls_row = (session.query(Classification).filter_by(email_id=job.email_id)
                   .order_by(Classification.created_at.desc()).first())
        if email_row is None or cls_row is None:
            continue
        company = (cls_row.extracted or {}).get("company") or ""
        candidates = []
        if company:
            key = _join_key(company)
            candidates = [a for a in session.query(Application).all()
                          if a.company_canonical == key
                          or _is_variant(key, a.company_canonical)]
        ts = post_review_item(session, notifier, email_row, cls_row, candidates)
        set_state(session, f"review_posted:{job.email_id}", ts)
        posted += 1
    return posted


def handle_review_new(session, email_id: str, tz: str) -> None:
    email, cls = _rehydrate(session, email_id)
    app = Application(
        company_canonical=_join_key(cls.company or email.from_addr),
        company_display=cls.company or email.from_addr,
        role_title=cls.role_title or "",
        source="lead" if cls.category == "recruiter_lead" else "applied",
        first_seen_at=email.received_at, last_contact_at=email.received_at,
        status_derived="applied")
    session.add(app)
    session.flush()
    append_event(session, app.id, cls.category, email.received_at, email_id=email_id)
    apply_closures(session, app.id, cls, email)
    create_obligation_if_needed(session, app.id, cls, email, tz)
    _park(session, email_id, "resolved_as_new")


def handle_review_assign(session, email_id: str, application_id: int, tz: str) -> None:
    email, cls = _rehydrate(session, email_id)
    append_event(session, application_id, cls.category, email.received_at,
                 email_id=email_id)
    apply_closures(session, application_id, cls, email)
    create_obligation_if_needed(session, application_id, cls, email, tz)
    _park(session, email_id, "resolved_as_assigned")


def handle_review_ignore(session, email_id: str) -> None:
    _park(session, email_id, "ignored_by_user")
