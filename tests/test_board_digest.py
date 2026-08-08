from datetime import UTC, datetime, timedelta

from tracker.models import Application, Obligation, get_state
from tracker.notify.board import update_board
from tracker.notify.digest import build_digest_data, send_digest_if_due
from tracker.notify.fmt import DigestData, render_board, render_digest
from tracker.notify.slack import SlackNotifier
from tests.test_slack import FakeWebClient

NOW = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)  # 8:00 AM PDT, a Monday
TZ = "America/Los_Angeles"


def seed(session):
    app = Application(company_canonical="hrt", company_display="HRT", role_title="SWE",
                      source="applied", first_seen_at=NOW, last_contact_at=NOW,
                      status_derived="assessment", human_engaged=True)
    session.add(app)
    session.flush()
    ob = Obligation(application_id=app.id, type="assessment", title="Assessment — HRT",
                    due_at=NOW + timedelta(hours=20), due_confidence="stated",
                    status="open", effort_minutes=120)
    session.add(ob)
    session.flush()
    return app, ob


def test_render_board_empty_state():
    assert "No open obligations" in render_board([], [], NOW, TZ)


def test_update_board_creates_pins_then_edits(session):
    seed(session)
    web = FakeWebClient()
    notifier = SlackNotifier(web, "C-A", "C-T")
    update_board(session, notifier, NOW, TZ)
    assert len(web.posts) == 1 and get_state(session, "board_ts") == "171.1"
    assert web.pins[0]["timestamp"] == "171.1"
    update_board(session, notifier, NOW, TZ)
    assert len(web.posts) == 1 and len(web.updates) == 1  # edited, not reposted


def test_digest_fires_once_at_8am_local(session):
    seed(session)
    web = FakeWebClient()
    notifier = SlackNotifier(web, "C-A", "C-T")
    assert send_digest_if_due(session, notifier, NOW, TZ) is True
    assert send_digest_if_due(session, notifier, NOW + timedelta(minutes=30), TZ) is False
    assert send_digest_if_due(session, notifier, NOW + timedelta(hours=2), TZ) is False


def test_monday_digest_has_trend(session):
    seed(session)
    data = build_digest_data(session, NOW, TZ)  # Aug 10 2026 is a Monday
    assert data.monday_trend is not None and "Response rate" in data.monday_trend


def test_render_digest_lists_obligations():
    data = DigestData(open_obs=[("Assessment", "HRT", "due Mon 10 AM")],
                      movement=["HRT → assessment"], today_interviews=[],
                      unconfirmed=[], review_count=2, monday_trend=None)
    text = render_digest(data, NOW, TZ)
    assert "HRT" in text and "review" in text.lower()
