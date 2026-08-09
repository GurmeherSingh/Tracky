from datetime import UTC, datetime

from tracker.classify.schemas import EmailClassification, EmailIn
from tracker.models import Application, Email
from tracker.resolve.resolver import resolve_application
from tracker.state.events import append_event

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def make_cls(company, role="SWE Intern", category="confirmation",
             match_basis="new", matched_application_id=None):
    return EmailClassification(
        is_my_application=True, category=category, company=company,
        role_title=role, deadline_utc=None, deadline_basis="none",
        actionable=False, confidence=0.9, reasoning="r",
        match_basis=match_basis, matched_application_id=matched_application_id)


def make_email(gmail_id="m1", thread_id="t1", from_addr="no-reply@greenhouse.io"):
    return EmailIn(gmail_id=gmail_id, thread_id=thread_id, from_addr=from_addr,
                   subject="s", body_text="b", received_at=NOW, headers={})


def add_email_row(session, gmail_id, thread_id):
    session.add(Email(gmail_id=gmail_id, thread_id=thread_id, from_addr="x@y.com",
                      subject="s", body_text="b", received_at=NOW, raw={}))
    session.flush()


def test_new_basis_creates_application(session):
    r = resolve_application(session, make_cls("Stripe"), make_email())
    assert r.created is True and r.application_id is not None
    app = session.get(Application, r.application_id)
    assert app.company_canonical == "stripe"


def test_llm_match_attaches_despite_different_spelling(session):
    r1 = resolve_application(session, make_cls("North"), make_email("m1", "t1"))
    r2 = resolve_application(
        session,
        make_cls("North Cloud", match_basis="existing",
                 matched_application_id=r1.application_id),
        make_email("m2", "t2"))
    assert r2.created is False and r2.application_id == r1.application_id
    assert session.query(Application).count() == 1


def test_llm_match_with_unknown_id_goes_to_review(session):
    r = resolve_application(
        session, make_cls("Stripe", match_basis="existing",
                          matched_application_id=999),
        make_email())
    assert r.needs_review is True
    assert r.review_reason == "llm_matched_unknown_id"


def test_llm_unsure_goes_to_review(session):
    r = resolve_application(session, make_cls("Stripe", match_basis="unsure"),
                            make_email())
    assert r.needs_review is True and r.review_reason == "llm_unsure_match"


def test_thread_inheritance_wins_over_llm_verdict(session):
    r1 = resolve_application(session, make_cls("North"), make_email("m1", "t9"))
    add_email_row(session, "m1", "t9")
    append_event(session, r1.application_id, "interview_invite", NOW, email_id="m1")
    # LLM says brand-new company, but the email is in an already-attached thread
    r2 = resolve_application(session, make_cls("Totally Different Co"),
                             make_email("m2", "t9"))
    assert r2.created is False and r2.application_id == r1.application_id
    assert session.query(Application).count() == 1


def test_exact_duplicate_guard_catches_llm_new_slip(session):
    resolve_application(session, make_cls("Stripe", "SWE Intern"),
                        make_email("m1", "t1"))
    r2 = resolve_application(session, make_cls("stripe, Inc.", "SWE Intern"),
                             make_email("m2", "t2"))
    assert r2.created is False
    assert session.query(Application).count() == 1


def test_two_roles_same_company_stay_separate(session):
    resolve_application(session, make_cls("Stripe", "SWE Intern"),
                        make_email("m1", "t1"))
    r2 = resolve_application(session, make_cls("Stripe", "Data Analyst Intern"),
                             make_email("m2", "t2"))
    assert r2.created is True
    assert session.query(Application).count() == 2


def test_no_company_goes_to_review(session):
    r = resolve_application(session, make_cls(None), make_email())
    assert r.needs_review is True and r.review_reason == "no_company"


def test_human_sender_marks_engaged(session):
    r = resolve_application(session, make_cls("Stripe"),
                            make_email(from_addr="jane@stripe.com"))
    app = session.get(Application, r.application_id)
    assert app.human_engaged is True
