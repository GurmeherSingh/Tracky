from datetime import UTC, datetime, timedelta

from tracker.models import Alert, Application, Obligation
from tracker.notify.slack import SlackNotifier
from tracker.obligations.sweep import run_sweep
from tests.test_slack import FakeWebClient

NOW = datetime(2026, 8, 10, 17, 0, tzinfo=UTC)  # 10:00 PDT — waking hours
TZ = "America/Los_Angeles"


def seed(session, due_in_hours=24, next_alert_delta=-1, tier="detection"):
    app = Application(company_canonical="hrt", company_display="HRT", role_title="SWE",
                      source="applied", first_seen_at=NOW, last_contact_at=NOW,
                      status_derived="assessment")
    session.add(app)
    session.flush()
    ob = Obligation(application_id=app.id, type="assessment", title="Assessment — HRT",
                    due_at=NOW + timedelta(hours=due_in_hours), due_confidence="stated",
                    status="open", effort_minutes=120,
                    next_alert_at=NOW + timedelta(hours=next_alert_delta),
                    alert_tier=tier)
    session.add(ob)
    session.flush()
    return ob


def notifier():
    return SlackNotifier(FakeWebClient(), "C-A", "C-T")


def test_due_alert_fires_and_advances_tier(session):
    ob = seed(session, due_in_hours=24, next_alert_delta=-1, tier="detection")
    sent = run_sweep(session, notifier(), NOW, TZ)
    assert sent == 1
    assert session.query(Alert).filter_by(tier="detection").count() == 1
    assert ob.alert_tier == "t12"           # t48 already past for a 24h-out deadline
    assert ob.next_alert_at == NOW + timedelta(hours=12)


def test_not_due_yet_stays_silent(session):
    seed(session, due_in_hours=100, next_alert_delta=+5, tier="detection")
    assert run_sweep(session, notifier(), NOW, TZ) == 0


def test_passed_deadline_sends_possibly_missed(session):
    ob = seed(session, due_in_hours=-2, next_alert_delta=-3, tier="t12")
    n = notifier()
    sent = run_sweep(session, n, NOW, TZ)
    assert sent == 1
    assert ob.status == "unconfirmed_possibly_missed"
    assert "possibly missed" in n.web.posts[0]["text"]


def test_exhausted_tiers_stop_alerting(session):
    ob = seed(session, due_in_hours=1, next_alert_delta=-1, tier="t12")
    run_sweep(session, notifier(), NOW, TZ)
    # last_call for a 1h-out deadline w/ 120min effort is already past → exhausted
    assert ob.next_alert_at is None
