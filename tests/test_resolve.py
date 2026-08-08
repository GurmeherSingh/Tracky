from datetime import UTC, datetime

from tracker.classify.schemas import EmailClassification, EmailIn
from tracker.models import Application
from tracker.resolve.companies import canonicalize
from tracker.resolve.resolver import resolve_application

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def make_cls(company, role="SWE Intern", category="confirmation"):
    return EmailClassification(
        is_my_application=True, category=category, company=company,
        role_title=role, deadline_utc=None, deadline_basis="none",
        actionable=False, confidence=0.9, reasoning="r")


def make_email(from_addr="no-reply@greenhouse.io"):
    return EmailIn(gmail_id="m1", thread_id="t1", from_addr=from_addr,
                   subject="s", body_text="b", received_at=NOW, headers={})


def test_canonicalize_strips_suffix_and_aliases():
    assert canonicalize("Meta Platforms, Inc.") == "meta"
    assert canonicalize("Hudson River Trading LLC") == "hudson river trading"
    assert canonicalize("Stripe") == "stripe"


def test_creates_new_application(session):
    r = resolve_application(session, make_cls("Stripe"), make_email())
    assert r.created is True and r.application_id is not None


def test_two_roles_same_company_stay_separate(session):
    resolve_application(session, make_cls("Stripe", "SWE Intern"), make_email())
    r2 = resolve_application(session, make_cls("Stripe", "Data Analyst Intern"), make_email())
    assert r2.created is True
    assert session.query(Application).count() == 2


def test_ambiguous_multi_app_match_goes_to_review(session):
    resolve_application(session, make_cls("Stripe", "SWE Intern"), make_email())
    resolve_application(session, make_cls("Stripe", "Data Analyst Intern"), make_email())
    r = resolve_application(session, make_cls("Stripe", None), make_email())
    assert r.needs_review is True and r.review_reason == "ambiguous_company_match"


def test_human_sender_marks_engaged(session):
    r = resolve_application(session, make_cls("Stripe"), make_email("jane@stripe.com"))
    app = session.get(Application, r.application_id)
    assert app.human_engaged is True
