from datetime import datetime, timedelta

ASSUMED_DEADLINE_HOURS = 72
OBLIGATION_CATEGORIES = {"assessment", "interview_invite", "offer"}


def finalize_deadline(deadline_utc: datetime | None, deadline_basis: str,
                      received_at: datetime, category: str) -> tuple[datetime | None, str]:
    if deadline_utc is not None and deadline_basis in {"stated", "relative"}:
        return deadline_utc, deadline_basis
    if category in OBLIGATION_CATEGORIES:
        return received_at + timedelta(hours=ASSUMED_DEADLINE_HOURS), "assumed"
    return None, "assumed"
