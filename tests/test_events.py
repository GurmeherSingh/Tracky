from datetime import UTC, datetime, timedelta

from tracker.models import Application
from tracker.state.events import append_event, derive_status

T0 = datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def t(days):
    return T0 + timedelta(days=days)


def test_normal_progression():
    assert derive_status([("confirmation", t(0))]) == "applied"
    assert derive_status([("confirmation", t(0)), ("assessment", t(1))]) == "assessment"
    assert derive_status([("confirmation", t(0)), ("assessment", t(1)),
                          ("interview_invite", t(2))]) == "interviewing"


def test_out_of_sequence_arrival_does_not_regress():
    # rejection processed first, assessment email arrives late
    assert derive_status([("rejection", t(3)), ("assessment", t(1))]) == "rejected"


def test_offer_beats_everything_but_rejection():
    assert derive_status([("assessment", t(0)), ("offer", t(5))]) == "offer"


def test_lead_only():
    assert derive_status([("recruiter_lead", t(0))]) == "lead"


def test_append_event_updates_application(session):
    app = Application(company_canonical="stripe", company_display="Stripe",
                      role_title="SWE", source="applied", first_seen_at=T0,
                      last_contact_at=T0, status_derived="applied")
    session.add(app)
    session.flush()
    append_event(session, app.id, "interview_invite", t(2))
    assert app.status_derived == "interviewing"
