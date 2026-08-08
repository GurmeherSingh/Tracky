from datetime import datetime, timedelta

from tracker.models import Application

QUIET_DAYS = 14
_TERMINAL = {"rejected", "offer"}


def gone_quiet_applications(session, now: datetime) -> list[Application]:
    cutoff = now - timedelta(days=QUIET_DAYS)
    return [a for a in session.query(Application)
            .filter(Application.human_engaged.is_(True),
                    Application.last_contact_at < cutoff)
            .order_by(Application.last_contact_at)
            .all() if a.status_derived not in _TERMINAL]
