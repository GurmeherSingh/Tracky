from datetime import UTC, datetime

from tracker.classify.schemas import EmailClassification
from tracker.models import (Application, Classification, Email, FailedJob,
                            Obligation, get_state)
from tracker.notify.review import (handle_review_assign, handle_review_ignore,
                                   handle_review_new, post_review_item,
                                   pump_review_queue)
from tracker.notify.slack import SlackNotifier
from tests.test_slack import FakeWebClient

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
TZ = "America/Los_Angeles"

CLS = EmailClassification(
    is_my_application=True, category="assessment", company="Stripe",
    role_title="SWE Intern", deadline_utc=None, deadline_basis="none",
    actionable=True, confidence=0.9, reasoning="r")


def seed_review(session):
    email = Email(gmail_id="m1", thread_id="t", from_addr="x@y.com",
                  subject="s", body_text="line1\nline2", received_at=NOW,
                  raw={"headers": {}})
    session.add(email)
    session.add(Classification(email_id="m1", prompt_version="v1",
                               model="claude-sonnet-5", category="assessment",
                               confidence=0.9,
                               extracted=CLS.model_dump(mode="json")))
    session.add(FailedJob(email_id="m1", stage="review_pending", error="ambiguous",
                          strikes=1))
    session.flush()
    return email


def test_post_review_item_has_buttons(session):
    email = seed_review(session)
    web = FakeWebClient()
    notifier = SlackNotifier(web, "C-A", "C-T")
    cls_row = session.query(Classification).one()
    post_review_item(session, notifier, email, cls_row, [])
    blocks = web.posts[0]["blocks"]
    action_ids = [el["action_id"] for b in blocks if b["type"] == "actions"
                  for el in b["elements"]]
    assert "review_new" in action_ids and "review_ignore" in action_ids


def test_pump_posts_each_item_once(session):
    seed_review(session)
    web = FakeWebClient()
    notifier = SlackNotifier(web, "C-A", "C-T")
    assert pump_review_queue(session, notifier) == 1
    assert pump_review_queue(session, notifier) == 0
    assert get_state(session, "review_posted:m1") is not None


def test_handle_new_creates_application_and_obligation(session):
    seed_review(session)
    handle_review_new(session, "m1", TZ)
    assert session.query(Application).count() == 1
    assert session.query(Obligation).count() == 1
    assert session.query(FailedJob).one().parked is True


def test_handle_assign_attaches_to_existing(session):
    seed_review(session)
    app = Application(company_canonical="stripe", company_display="Stripe",
                      role_title="SWE Intern", source="applied", first_seen_at=NOW,
                      last_contact_at=NOW, status_derived="applied")
    session.add(app)
    session.flush()
    handle_review_assign(session, "m1", app.id, TZ)
    assert session.query(Application).count() == 1
    assert app.status_derived == "assessment"


def test_handle_ignore_parks_without_writes(session):
    seed_review(session)
    handle_review_ignore(session, "m1")
    assert session.query(Application).count() == 0
    assert session.query(FailedJob).one().parked is True
