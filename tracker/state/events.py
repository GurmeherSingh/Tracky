from datetime import datetime

from tracker.models import Application, Event

_PRECEDENCE = ["rejected", "offer", "interviewing", "assessment", "applied", "lead"]
_KIND_TO_STATUS = {
    "rejection": "rejected",
    "offer": "offer",
    "interview_invite": "interviewing",
    "assessment": "assessment",
    "confirmation": "applied",
    "recruiter_lead": "lead",
}


def derive_status(kinds_with_times: list[tuple[str, datetime]]) -> str:
    statuses = {_KIND_TO_STATUS[k] for k, _ in kinds_with_times if k in _KIND_TO_STATUS}
    for status in _PRECEDENCE:
        if status in statuses:
            return status
    return "applied"


def append_event(session, application_id: int, kind: str, occurred_at: datetime,
                 email_id: str | None = None) -> Event:
    event = Event(application_id=application_id, email_id=email_id,
                  kind=kind, occurred_at=occurred_at)
    session.add(event)
    session.flush()
    rows = session.query(Event.kind, Event.occurred_at).filter_by(
        application_id=application_id).all()
    app = session.get(Application, application_id)
    app.status_derived = derive_status([(r[0], r[1]) for r in rows])
    return event
