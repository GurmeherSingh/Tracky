from datetime import UTC, datetime, timedelta

from tracker.models import Application
from tracker.state.quiet import gone_quiet_applications

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def make_app(session, engaged, days_silent, status="interviewing"):
    app = Application(company_canonical="x", company_display="X", role_title="r",
                      source="applied", first_seen_at=NOW - timedelta(days=30),
                      last_contact_at=NOW - timedelta(days=days_silent),
                      status_derived=status, human_engaged=engaged)
    session.add(app)
    session.flush()
    return app


def test_engaged_and_silent_is_quiet(session):
    app = make_app(session, engaged=True, days_silent=15)
    assert gone_quiet_applications(session, NOW) == [app]


def test_never_engaged_is_not_quiet_even_if_silent(session):
    make_app(session, engaged=False, days_silent=40)
    assert gone_quiet_applications(session, NOW) == []


def test_terminal_states_are_not_quiet(session):
    make_app(session, engaged=True, days_silent=40, status="rejected")
    assert gone_quiet_applications(session, NOW) == []
