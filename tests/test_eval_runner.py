from datetime import UTC, datetime

from tracker.classify.schemas import EmailClassification
from tracker.evals.runner import export_labels, instrumentation_summary, run_eval
from tracker.models import Classification, Email, EvalLabel
from tests.test_pipeline import FakeAnthropic

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)

PREDICTED = EmailClassification(
    is_my_application=True, category="assessment", company="Stripe",
    role_title="SWE", deadline_utc=None, deadline_basis="none",
    actionable=True, confidence=0.9, reasoning="r")


def seed_email(session, gmail_id="m1", category="assessment"):
    session.add(Email(gmail_id=gmail_id, thread_id="t",
                      from_addr="no-reply@hackerrank.com",
                      subject="s", body_text="b", received_at=NOW,
                      raw={"headers": {}}))
    session.add(Classification(email_id=gmail_id, prompt_version="v1",
                               model="claude-sonnet-5", category=category,
                               confidence=0.9,
                               extracted=PREDICTED.model_dump(mode="json")))
    session.flush()


def test_export_labels_bootstraps_from_predictions(session):
    seed_email(session)
    export_labels(session)
    label = session.query(EvalLabel).one()
    assert label.true_category == "assessment"


def test_export_labels_skips_already_labeled(session):
    seed_email(session)
    export_labels(session)
    export_labels(session)
    assert session.query(EvalLabel).count() == 1


def test_run_eval_scores_against_labels(session):
    seed_email(session)
    session.add(EvalLabel(email_id="m1", true_category="assessment"))
    session.flush()
    report = run_eval(session, FakeAnthropic(PREDICTED))
    assert report.n == 1 and report.overall_accuracy == 1.0
    # replay wrote a second classification row for the same email
    assert session.query(Classification).filter_by(email_id="m1").count() == 2


def test_gate_noise_counts_without_api_call(session):
    session.add(Email(gmail_id="m2", thread_id="t",
                      from_addr="jobalerts-noreply@linkedin.com",
                      subject="jobs!", body_text="b", received_at=NOW,
                      raw={"headers": {}}))
    session.add(EvalLabel(email_id="m2", true_category="noise"))
    session.flush()

    class Exploding:
        @property
        def messages(self):
            raise AssertionError("gate noise must not hit the API")

    report = run_eval(session, Exploding())
    assert report.overall_accuracy == 1.0


def test_instrumentation_summary_renders(session):
    seed_email(session)
    text = instrumentation_summary(session)
    assert "manual data entries: 0" in text
