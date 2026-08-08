from datetime import UTC, datetime

from tracker.classify.schemas import EmailClassification, EmailIn
from tracker.models import (Application, Classification, Email, FailedJob,
                            Obligation, get_state, wipe_all_data)
from tracker.ingest.pipeline import process_email, record_failure, run_backfill

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
TZ = "America/Los_Angeles"


def make_email(gmail_id="m1", from_addr="no-reply@hackerrank.com"):
    return EmailIn(gmail_id=gmail_id, thread_id="t", from_addr=from_addr,
                   subject="Stripe assessment", body_text="complete in 7 days",
                   received_at=NOW, headers={})


class FakeAnthropic:
    """Injected in place of anthropic.Anthropic; pipeline only calls messages.parse."""
    def __init__(self, cls):
        self._cls = cls

    @property
    def messages(self):
        outer = self

        class M:
            def parse(self, **kwargs):
                return type("R", (), {"parsed_output": outer._cls})()
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


def test_backfill_counts_and_history_cursor(session):
    gmail = FakeGmail([make_email("a"), make_email("b")])
    counts = run_backfill(session, gmail, FakeAnthropic(GOOD), "2026/01/01", TZ)
    assert counts["processed"] == 2
    assert get_state(session, "gmail_history_id") == "424242"


def test_wipe_all_data_empties_every_table(session):
    process_email(session, make_email(), FakeAnthropic(GOOD), TZ)
    record_failure(session, "m9", "classify", "boom")
    assert session.query(Email).count() == 1
    wipe_all_data(session)
    assert session.query(Email).count() == 0
    assert session.query(Application).count() == 0
    assert session.query(Obligation).count() == 0
    assert session.query(FailedJob).count() == 0
