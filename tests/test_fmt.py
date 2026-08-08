from datetime import UTC, datetime, timedelta

from tracker.models import Obligation
from tracker.notify.fmt import render_alert, render_auth_alarm

NOW = datetime(2026, 8, 10, 5, 30, tzinfo=UTC)
TZ = "America/Los_Angeles"


def make_ob(due_confidence="stated", status="open"):
    return Obligation(application_id=1, type="assessment", title="Assessment — HRT",
                      due_at=NOW + timedelta(hours=11, minutes=30),
                      due_confidence=due_confidence, status=status,
                      effort_minutes=120)


def test_alert_contains_company_tier_and_countdown():
    text = render_alert(make_ob(), "Hudson River Trading", "t12", NOW, TZ)
    assert "Hudson River Trading" in text
    assert "🚨" in text
    assert "11h" in text


def test_assumed_deadline_is_labeled():
    text = render_alert(make_ob(due_confidence="assumed"), "HRT", "t48", NOW, TZ)
    assert "[assumed deadline]" in text


def test_possibly_missed_is_flagged():
    text = render_alert(make_ob(status="unconfirmed_possibly_missed"), "HRT",
                        "last_call", NOW, TZ)
    assert "possibly missed" in text


def test_overdue_shows_overdue_not_negative_countdown():
    ob = make_ob()
    text = render_alert(ob, "HRT", "last_call", NOW + timedelta(hours=20), TZ)
    assert "OVERDUE" in text


def test_auth_alarm_is_loud():
    assert "Gmail auth expired" in render_auth_alarm()
