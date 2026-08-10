from datetime import UTC, datetime

from tracker.classify.schemas import EmailClassification, EmailIn
from tracker.models import (Application, Classification, Email, FailedJob,
                            Obligation, get_state, wipe_all_data)
from tracker.ingest.pipeline import (process_email, record_failure,
                                     retry_failed, run_backfill)

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
TZ = "America/Los_Angeles"


def make_email(gmail_id="m1", from_addr="no-reply@hackerrank.com"):
    return EmailIn(gmail_id=gmail_id, thread_id="t", from_addr=from_addr,
                   subject="Stripe assessment", body_text="complete in 7 days",
                   received_at=NOW, headers={})


class FakeAnthropic:
    """Injected in place of anthropic.Anthropic; pipeline only calls messages.parse.

    Accepts either a fixed classification or a callable(user_content) -> classification.
    """
    def __init__(self, cls):
        self._cls = cls

    @property
    def messages(self):
        outer = self

        class M:
            def parse(self, **kwargs):
                result = outer._cls
                if callable(result):
                    result = result(kwargs["messages"][0]["content"])
                return type("R", (), {"parsed_output": result})()
        return M()


GOOD = EmailClassification(
    is_my_application=True, category="assessment", company="Stripe",
    role_title="SWE Intern", deadline_utc=None, deadline_basis="none",
    actionable=True, confidence=0.95, reasoning="r")


def test_full_pipeline_creates_everything(session):
    disp = process_email(session, make_email(), FakeAnthropic(GOOD), TZ)
    assert disp == "processed"
    assert session.query(Email).count() == 1
    assert session.query(Classification).count() == 1
    assert session.query(Application).count() == 1
    assert session.query(Obligation).count() == 1


def test_duplicate_is_skipped(session):
    process_email(session, make_email(), FakeAnthropic(GOOD), TZ)
    assert process_email(session, make_email(), FakeAnthropic(GOOD), TZ) == "duplicate"
    assert session.query(Classification).count() == 1


def test_noise_stops_before_llm(session):
    email = make_email(from_addr="jobalerts-noreply@linkedin.com")

    class Exploding:
        @property
        def messages(self):
            raise AssertionError("LLM must not be called for gate-1 noise")

    assert process_email(session, email, Exploding(), TZ) == "noise"


class Flaky:
    """Raises on the first `fail_calls` parse attempts, then classifies fine."""
    def __init__(self, fail_calls, cls):
        self.fail_calls, self._cls, self.calls = fail_calls, cls, 0

    @property
    def messages(self):
        outer = self

        class M:
            def parse(self, **kwargs):
                outer.calls += 1
                if outer.calls <= outer.fail_calls:
                    raise RuntimeError("model unreachable")
                return type("R", (), {"parsed_output": outer._cls})()
        return M()


def test_transient_classification_failure_is_recovered_next_pass(session):
    llm = Flaky(2, GOOD)  # classify_email retries twice before giving up
    assert process_email(session, make_email(), llm, TZ) == "failed"
    assert session.query(FailedJob).count() == 1

    counts = retry_failed(session, FakeGmail([]), llm, TZ)

    assert counts.get("processed") == 1
    assert session.query(FailedJob).count() == 0  # cleared once it succeeds
    assert session.query(Obligation).count() == 1


def test_retries_park_a_permanently_broken_email(session):
    llm = Flaky(99, GOOD)
    process_email(session, make_email(), llm, TZ)
    for _ in range(2):
        retry_failed(session, FakeGmail([]), llm, TZ)
    job = session.query(FailedJob).one()
    assert job.strikes == 3 and job.parked is True
    retry_failed(session, FakeGmail([]), llm, TZ)
    assert session.query(FailedJob).one().strikes == 3  # parked means left alone


def test_three_strikes_parks_job(session):
    record_failure(session, "m9", "classify", "boom")
    record_failure(session, "m9", "classify", "boom")
    job = record_failure(session, "m9", "classify", "boom")
    assert job.strikes == 3 and job.parked is True


class FakeGmail:
    def __init__(self, emails):
        self._emails = {e.gmail_id: e for e in emails}

    def list_message_ids(self, query, after=None):
        return iter(self._emails.keys())

    def fetch_email(self, message_id):
        return self._emails[message_id]

    def current_history_id(self):
        return "424242"

    def profile_email(self):
        return "me@example.com"


def test_backfill_counts_and_history_cursor(session):
    gmail = FakeGmail([make_email("a"), make_email("b")])
    counts = run_backfill(session, gmail, FakeAnthropic(GOOD), "2026/01/01", TZ)
    assert counts["processed"] == 2
    assert get_state(session, "gmail_history_id") == "424242"


def test_backfill_processes_oldest_first_so_receipts_close(session):
    from datetime import timedelta
    from tracker.models import Obligation
    assessment_email = EmailIn(
        gmail_id="older", thread_id="t", from_addr="no-reply@hackerrank.com",
        subject="Stripe assessment", body_text="complete in 7 days",
        received_at=NOW, headers={})
    receipt_email = EmailIn(
        gmail_id="newer", thread_id="t", from_addr="no-reply@hackerrank.com",
        subject="Submission received",
        body_text="Thank you for completing your assessment",
        received_at=NOW + timedelta(days=1), headers={})

    def classify(user_content):
        if "Thank you for completing" in user_content:
            return EmailClassification(
                is_my_application=True, category="confirmation", company="Stripe",
                role_title="SWE Intern", deadline_utc=None, deadline_basis="none",
                actionable=False, confidence=0.95, reasoning="receipt")
        return GOOD

    # Gmail lists NEWEST first — receipt before the assessment that it closes
    gmail = FakeGmail([receipt_email, assessment_email])
    run_backfill(session, gmail, FakeAnthropic(classify), "2026/01/01", TZ)
    obs = session.query(Obligation).all()
    assert len(obs) == 1
    assert obs[0].status == "completed" and obs[0].closed_by == "receipt"


def test_backfill_self_reply_closes_scheduling_without_recreating(session):
    from datetime import timedelta
    invite = EmailIn(
        gmail_id="older", thread_id="t", from_addr="scheduling@ats.rippling.com",
        subject="Please provide your interview availability",
        body_text="pick a slot", received_at=NOW, headers={})
    reply = EmailIn(
        gmail_id="newer", thread_id="t", from_addr="me@example.com",
        subject="Re: Please provide your interview availability",
        body_text="I am free Monday 10am", received_at=NOW + timedelta(hours=2),
        headers={})
    invite_cls = EmailClassification(
        is_my_application=True, category="interview_invite", company="Stripe",
        role_title="SWE Intern", deadline_utc=None, deadline_basis="none",
        actionable=True, confidence=0.9, reasoning="r")
    gmail = FakeGmail([reply, invite])  # Gmail lists newest first
    run_backfill(session, gmail, FakeAnthropic(invite_cls), "2026/01/01", TZ)
    obs = session.query(Obligation).all()
    assert len(obs) == 1, [o.type for o in obs]
    assert obs[0].status == "completed" and obs[0].closed_by == "self_reply"


def test_confirmed_interview_email_closes_scheduling_in_pipeline(session):
    from datetime import timedelta
    invite = EmailIn(
        gmail_id="older", thread_id="t", from_addr="scheduling@ats.rippling.com",
        subject="Please provide your interview availability",
        body_text="pick a slot", received_at=NOW, headers={})
    confirmation = EmailIn(
        gmail_id="newer", thread_id="t", from_addr="mail@ats.rippling.com",
        subject="Interview with Stripe",
        body_text="Your interview is confirmed for Aug 10 at 2pm",
        received_at=NOW + timedelta(hours=3), headers={})

    def classify(user_content):
        confirmed = "confirmed" in user_content
        return EmailClassification(
            is_my_application=True, category="interview_invite", company="Stripe",
            role_title="SWE Intern", deadline_utc=None, deadline_basis="none",
            actionable=not confirmed, confidence=0.9, reasoning="r",
            is_confirmation=confirmed)

    gmail = FakeGmail([confirmation, invite])
    run_backfill(session, gmail, FakeAnthropic(classify), "2026/01/01", TZ)
    obs = session.query(Obligation).all()
    assert len(obs) == 1, [o.type for o in obs]
    assert obs[0].status == "completed" and obs[0].closed_by == "next_stage"


def test_tracked_applications_reach_the_llm(session):
    seen = []

    def classify(user_content):
        seen.append(user_content)
        return GOOD

    first = EmailIn(gmail_id="m1", thread_id="t1", from_addr="no-reply@hackerrank.com",
                    subject="Stripe assessment", body_text="complete in 7 days",
                    received_at=NOW, headers={})
    second = EmailIn(gmail_id="m2", thread_id="t2", from_addr="no-reply@hackerrank.com",
                     subject="Stripe assessment", body_text="complete in 7 days",
                     received_at=NOW, headers={})
    process_email(session, first, FakeAnthropic(classify), TZ)
    process_email(session, second, FakeAnthropic(classify), TZ)
    assert "tracked-applications: (none yet)" in seen[0]
    assert "Stripe" in seen[1] and "id=" in seen[1]


def test_resends_collapse_and_stated_deadline_wins(session):
    """The Millennium sequence: three identical Workday resends whose bodies
    mention 'submitted', then a HackerRank invite with the real stated deadline."""
    from datetime import timedelta
    stated_due = NOW + timedelta(days=7, minutes=90)
    resends = [EmailIn(
        gmail_id=f"resend{i}", thread_id="tw", from_addr="mlp@myworkday.com",
        subject="Next Steps: Technical Assessment",
        body_text="Complete your assessment. Once submitted, results follow.",
        received_at=NOW + timedelta(hours=i), headers={}) for i in range(3)]
    invite = EmailIn(
        gmail_id="hr", thread_id="th", from_addr="support@hackerrankforwork.com",
        subject="Millennium Test Invitation",
        body_text="Your test is due August 13.",
        received_at=NOW + timedelta(hours=2, minutes=30), headers={})

    def classify(user_content):
        received = datetime.fromisoformat(
            user_content.splitlines()[0].split(": ", 1)[1])
        stated = "Test Invitation" in user_content
        return EmailClassification(
            is_my_application=True, category="assessment", company="Millennium",
            role_title="AI Intern",
            deadline_utc=stated_due if stated else received + timedelta(days=7),
            deadline_basis="stated" if stated else "relative",
            actionable=True, confidence=0.9, reasoning="r")

    gmail = FakeGmail([invite, resends[2], resends[1], resends[0]])  # newest first
    run_backfill(session, gmail, FakeAnthropic(classify), "2026/01/01", TZ)
    obs = session.query(Obligation).all()
    assert len(obs) == 1, [(o.type, o.status, o.closed_by) for o in obs]
    assert obs[0].status == "open"
    assert obs[0].due_at == stated_due and obs[0].due_confidence == "stated"


def test_wipe_all_data_empties_every_table(session):
    process_email(session, make_email(), FakeAnthropic(GOOD), TZ)
    record_failure(session, "m9", "classify", "boom")
    assert session.query(Email).count() == 1
    wipe_all_data(session)
    assert session.query(Email).count() == 0
    assert session.query(Application).count() == 0
    assert session.query(Obligation).count() == 0
    assert session.query(FailedJob).count() == 0
