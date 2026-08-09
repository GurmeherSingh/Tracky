from datetime import UTC, datetime, timedelta

from tracker.classify.schemas import EmailClassification, EmailIn
from tracker.models import Application
from tracker.obligations.lifecycle import (apply_closures,
                                           create_obligation_if_needed,
                                           mark_deadline_passed,
                                           obligation_type_for)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
TZ = "America/Los_Angeles"


def make_cls(category, actionable=True, confidence=0.9, deadline=None):
    return EmailClassification(
        is_my_application=True, category=category, company="Stripe",
        role_title="SWE Intern",
        deadline_utc=deadline, deadline_basis="stated" if deadline else "none",
        actionable=actionable, confidence=confidence, reasoning="r")


def make_email(gmail_id="m1", from_addr="no-reply@hackerrank.com", body="do it"):
    return EmailIn(gmail_id=gmail_id, thread_id="t", from_addr=from_addr,
                   subject="s", body_text=body, received_at=NOW, headers={})


def make_app(session):
    app = Application(company_canonical="stripe", company_display="Stripe",
                      role_title="SWE Intern", source="applied", first_seen_at=NOW,
                      last_contact_at=NOW, status_derived="applied")
    session.add(app)
    session.flush()
    return app


def test_actionable_assessment_creates_open_obligation(session):
    app = make_app(session)
    ob = create_obligation_if_needed(session, app.id,
                                     make_cls("assessment", deadline=NOW + timedelta(days=7)),
                                     make_email(), TZ)
    assert ob.status == "open" and ob.type == "assessment"
    assert ob.alert_tier == "detection" and ob.next_alert_at is not None
    assert ob.due_confidence == "stated"


def test_announced_not_actionable_creates_nothing(session):
    app = make_app(session)
    assert create_obligation_if_needed(
        session, app.id, make_cls("assessment", actionable=False), make_email(), TZ) is None


def test_low_confidence_creates_unconfirmed(session):
    app = make_app(session)
    ob = create_obligation_if_needed(session, app.id,
                                     make_cls("assessment", confidence=0.5),
                                     make_email(), TZ)
    assert ob.status == "unconfirmed"


def test_missing_deadline_gets_assumed_72h(session):
    app = make_app(session)
    ob = create_obligation_if_needed(session, app.id, make_cls("assessment"),
                                     make_email(), TZ)
    assert ob.due_confidence == "assumed"
    assert ob.due_at == NOW + timedelta(hours=72)


def test_receipt_closes_assessment(session):
    app = make_app(session)
    create_obligation_if_needed(session, app.id, make_cls("assessment"), make_email(), TZ)
    closed = apply_closures(session, app.id, make_cls("noise", actionable=False),
                            make_email("m2", body="Thank you for completing your assessment"))
    assert len(closed) == 1 and closed[0].closed_by == "receipt"
    assert closed[0].status == "completed"


def test_next_stage_closes_earlier_obligation(session):
    app = make_app(session)
    create_obligation_if_needed(session, app.id, make_cls("assessment"), make_email(), TZ)
    closed = apply_closures(session, app.id, make_cls("interview_invite"), make_email("m3"))
    assert closed and closed[0].closed_by == "next_stage"


def test_rejection_moots_everything(session):
    app = make_app(session)
    create_obligation_if_needed(session, app.id, make_cls("assessment"), make_email(), TZ)
    closed = apply_closures(session, app.id, make_cls("rejection", actionable=False),
                            make_email("m4"))
    assert closed[0].status == "moot" and closed[0].closed_by == "rejection"


def test_deadline_pass_marks_unconfirmed_possibly_missed(session):
    app = make_app(session)
    ob = create_obligation_if_needed(session, app.id, make_cls("assessment"), make_email(), TZ)
    missed = mark_deadline_passed(session, NOW + timedelta(hours=73))
    assert missed == [ob]
    assert ob.status == "unconfirmed_possibly_missed" and ob.next_alert_at is None


def test_reminder_does_not_duplicate_open_obligation(session):
    app = make_app(session)
    ob1 = create_obligation_if_needed(session, app.id, make_cls("assessment"),
                                      make_email("m1"), TZ)
    ob2 = create_obligation_if_needed(session, app.id, make_cls("assessment"),
                                      make_email("m2"), TZ)
    assert ob2.id == ob1.id
    from tracker.models import Obligation
    assert session.query(Obligation).count() == 1


def test_reminder_with_stated_deadline_upgrades_assumed(session):
    app = make_app(session)
    ob = create_obligation_if_needed(session, app.id, make_cls("assessment"),
                                     make_email("m1"), TZ)
    assert ob.due_confidence == "assumed"
    stated_due = NOW + timedelta(days=5)
    create_obligation_if_needed(session, app.id,
                                make_cls("assessment", deadline=stated_due),
                                make_email("m2"), TZ)
    assert ob.due_confidence == "stated" and ob.due_at == stated_due


def test_obligation_type_mapping():
    assert obligation_type_for(make_cls("assessment")) == "assessment"
    assert obligation_type_for(make_cls("assessment"),
                               subject_and_body="your take-home project") == "take_home"
    assert obligation_type_for(make_cls("interview_invite")) == "scheduling_reply"
    assert obligation_type_for(make_cls("offer")) == "offer_response"
    assert obligation_type_for(make_cls("rejection", actionable=False)) is None
