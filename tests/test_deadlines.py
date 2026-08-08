from datetime import UTC, datetime, timedelta

from tracker.classify.deadlines import ASSUMED_DEADLINE_HOURS, finalize_deadline

RECEIVED = datetime(2026, 8, 6, 15, 30, tzinfo=UTC)


def test_stated_deadline_passes_through():
    due = datetime(2026, 8, 13, 23, 59, tzinfo=UTC)
    assert finalize_deadline(due, "stated", RECEIVED, "assessment") == (due, "stated")


def test_relative_deadline_counts_as_stated():
    due = RECEIVED + timedelta(days=7)
    assert finalize_deadline(due, "relative", RECEIVED, "assessment") == (due, "stated")


def test_missing_deadline_on_assessment_gets_assumed_default():
    due, conf = finalize_deadline(None, "none", RECEIVED, "assessment")
    assert conf == "assumed"
    assert due == RECEIVED + timedelta(hours=ASSUMED_DEADLINE_HOURS)


def test_missing_deadline_on_rejection_stays_none():
    assert finalize_deadline(None, "none", RECEIVED, "rejection")[0] is None
