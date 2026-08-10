from datetime import UTC, datetime

from tracker.classify.deadlines import finalize_deadline
from tracker.classify.schemas import EmailClassification, EmailIn
from tracker.models import Obligation
from tracker.obligations.escalation import EFFORT_MINUTES

UNCONFIRMED_THRESHOLD = 0.6
_DUE_RANK = {"assumed": 0, "relative": 1, "stated": 2}
_COMPLETION_PHRASES = ("submitted", "completed", "received your solution",
                       "thank you for completing", "successfully finished")
_TITLES = {"assessment": "Assessment", "take_home": "Take-home",
           "scheduling_reply": "Reply to schedule interview",
           "offer_response": "Respond to offer", "interview": "Interview"}


def obligation_type_for(cls: EmailClassification,
                        subject_and_body: str = "") -> str | None:
    # a booked slot isn't a reply you owe — it's a place you have to be, which
    # is why it counts despite the classifier calling it non-actionable
    if cls.category == "interview_invite" and cls.is_confirmation:
        return "interview"
    if not cls.actionable:
        return None
    if cls.category == "assessment":
        return "take_home" if "take-home" in subject_and_body.lower() else "assessment"
    if cls.category == "interview_invite":
        return "scheduling_reply"
    if cls.category == "offer":
        return "offer_response"
    return None


def create_obligation_if_needed(session, application_id: int,
                                cls: EmailClassification, email: EmailIn,
                                tz: str) -> Obligation | None:
    ob_type = obligation_type_for(cls, email.subject + " " + email.body_text)
    if ob_type is None:
        return None
    due_at, due_confidence = finalize_deadline(
        cls.deadline_utc, cls.deadline_basis, email.received_at, cls.category)
    if ob_type == "interview" and due_confidence == "assumed":
        return None  # a meeting time is never guessed — it was stated or it wasn't
    # reminder emails must not duplicate an open obligation of the same type;
    # a better-grounded deadline (stated > relative > assumed) replaces a weaker one
    existing = session.query(Obligation).filter(
        Obligation.application_id == application_id,
        Obligation.type == ob_type,
        Obligation.status.in_(["open", "unconfirmed"])).first()
    if existing:
        if _DUE_RANK[due_confidence] > _DUE_RANK[existing.due_confidence]:
            existing.due_at = due_at
            existing.due_confidence = due_confidence
            # the upgrading email now holds the operative deadline — link to it
            existing.source_email_id = email.gmail_id
        return existing
    ob = Obligation(
        application_id=application_id, type=ob_type,
        title=f"{_TITLES[ob_type]} — {cls.company or 'unknown'}",
        due_at=due_at, due_confidence=due_confidence,
        source_email_id=email.gmail_id,
        status="open" if cls.confidence >= UNCONFIRMED_THRESHOLD else "unconfirmed",
        effort_minutes=EFFORT_MINUTES[ob_type],
        next_alert_at=datetime.now(UTC), alert_tier="detection")
    session.add(ob)
    session.flush()
    return ob


def _open_obligations(session, application_id: int) -> list[Obligation]:
    return session.query(Obligation).filter(
        Obligation.application_id == application_id,
        Obligation.status.in_(["open", "unconfirmed"])).all()


def close_obligation(ob: Obligation, status: str, closed_by: str, at: datetime) -> None:
    ob.status = status
    ob.closed_by = closed_by
    ob.closed_at = at
    ob.next_alert_at = None


def apply_closures(session, application_id: int, cls: EmailClassification,
                   email: EmailIn, self_addr: str | None = None) -> list[Obligation]:
    closed: list[Obligation] = []
    open_obs = _open_obligations(session, application_id)
    if not open_obs:
        return closed
    # mail sent by the user themselves is direct proof the scheduling reply
    # happened, whatever the classifier thinks the email is
    if self_addr and email.from_addr.lower() == self_addr.lower():
        for ob in [o for o in open_obs if o.type == "scheduling_reply"]:
            close_obligation(ob, "completed", "self_reply", email.received_at)
            closed.append(ob)
            open_obs.remove(ob)
        if not open_obs:
            return closed
    body = (email.subject + " " + email.body_text).lower()
    if cls.category == "rejection":
        for ob in open_obs:
            close_obligation(ob, "moot", "rejection", email.received_at)
            closed.append(ob)
        return closed
    if cls.category in {"interview_invite", "offer"}:
        earlier = {"interview_invite": {"assessment", "take_home"},
                   "offer": {"assessment", "take_home", "scheduling_reply"}}[cls.category]
        if cls.category == "interview_invite" and cls.is_confirmation:
            # a confirmed slot completes the scheduling loop
            earlier = earlier | {"scheduling_reply"}
        for ob in open_obs:
            if ob.type in earlier:
                close_obligation(ob, "completed", "next_stage", email.received_at)
                closed.append(ob)
        return closed
    # an email that is itself an actionable ask can't be a receipt for one —
    # assessment instructions routinely say 'once submitted...'
    if obligation_type_for(cls, body) in {"assessment", "take_home"}:
        return closed
    if any(p in body for p in _COMPLETION_PHRASES):
        for ob in open_obs:
            if ob.type in {"assessment", "take_home"}:
                close_obligation(ob, "completed", "receipt", email.received_at)
                closed.append(ob)
    return closed


def mark_deadline_passed(session, now: datetime) -> list[Obligation]:
    missed: list[Obligation] = []
    for ob in session.query(Obligation).filter(
            Obligation.status.in_(["open", "unconfirmed"]),
            Obligation.due_at < now).all():
        if ob.type == "interview":
            # a meeting whose time has passed is over, not overdue
            close_obligation(ob, "completed", "elapsed", now)
            continue
        ob.status = "unconfirmed_possibly_missed"
        ob.closed_by = "deadline"
        ob.closed_at = now
        ob.next_alert_at = None
        missed.append(ob)
    return missed
