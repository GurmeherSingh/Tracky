from datetime import datetime

from tracker.models import Application, Obligation
from tracker.notify.board import update_board
from tracker.notify.slack import SlackNotifier
from tracker.obligations.escalation import next_alert_after
from tracker.obligations.lifecycle import mark_deadline_passed


def _company(session, ob: Obligation) -> str:
    app = session.get(Application, ob.application_id)
    return app.company_display if app else "unknown"


def run_sweep(session, notifier: SlackNotifier, now: datetime, tz: str) -> int:
    sent = 0
    for ob in mark_deadline_passed(session, now):
        notifier.send_alert(session, ob, _company(session, ob), "last_call", now, tz)
        sent += 1
    due = session.query(Obligation).filter(
        Obligation.next_alert_at.isnot(None),
        Obligation.next_alert_at <= now,
        Obligation.status.in_(["open", "unconfirmed"])).all()
    for ob in due:
        notifier.send_alert(session, ob, _company(session, ob),
                            ob.alert_tier or "detection", now, tz)
        sent += 1
        nxt = next_alert_after(ob.alert_tier, ob.due_at, ob.effort_minutes, now, tz)
        if nxt:
            ob.alert_tier, ob.next_alert_at = nxt
        else:
            ob.next_alert_at = None
    if sent:
        update_board(session, notifier, now, tz)
    return sent
