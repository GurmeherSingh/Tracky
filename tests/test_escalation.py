from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from tracker.obligations.escalation import (BUFFER_MINUTES, clamp_to_waking,
                                            next_alert_after, tier_time)

TZ = "America/Los_Angeles"
DUE = datetime(2026, 8, 10, 17, 0, tzinfo=UTC)  # 10:00 PDT


def test_tier_times():
    assert tier_time("t48", DUE, 120) == DUE - timedelta(hours=48)
    assert tier_time("t12", DUE, 120) == DUE - timedelta(hours=12)
    assert tier_time("last_call", DUE, 120) == DUE - timedelta(minutes=120 + BUFFER_MINUTES)


def test_clamp_pulls_3am_back_to_0059_local():
    three_am_local = datetime(2026, 8, 10, 3, 0, tzinfo=ZoneInfo(TZ))
    clamped = clamp_to_waking(three_am_local.astimezone(UTC), TZ)
    local = clamped.astimezone(ZoneInfo(TZ))
    assert (local.hour, local.minute) == (0, 59)
    assert clamped < three_am_local.astimezone(UTC)  # earlier, never later


def test_clamp_leaves_waking_hours_alone():
    ten_am = datetime(2026, 8, 10, 10, 0, tzinfo=ZoneInfo(TZ)).astimezone(UTC)
    assert clamp_to_waking(ten_am, TZ) == ten_am


def test_next_alert_skips_past_tiers():
    now = DUE - timedelta(hours=13)  # t48 already past
    nxt = next_alert_after("detection", DUE, 120, now, TZ)
    assert nxt is not None and nxt[0] == "t12"


def test_next_alert_exhausted_returns_none():
    now = DUE + timedelta(hours=1)
    assert next_alert_after("last_call", DUE, 120, now, TZ) is None


def test_tier_for_reslots_by_time_remaining():
    from tracker.obligations.escalation import tier_for
    due = datetime(2026, 8, 12, 18, 0, tzinfo=UTC)
    assert tier_for(due, 120, due - timedelta(hours=50)) == "t48"
    assert tier_for(due, 120, due - timedelta(hours=10)) == "t12"
    assert tier_for(due, 120, due - timedelta(hours=2)) == "last_call"


def test_remind_accepts_time_before_last_call():
    from tracker.obligations.escalation import remind_error
    due = datetime(2026, 8, 12, 18, 0, tzinfo=UTC)
    now = due - timedelta(hours=20)
    assert remind_error(due, 120, due - timedelta(hours=5), now) is None


def test_remind_rejects_past_and_too_late():
    from tracker.obligations.escalation import remind_error
    due = datetime(2026, 8, 12, 18, 0, tzinfo=UTC)
    now = due - timedelta(hours=20)
    assert "future" in remind_error(due, 120, now - timedelta(hours=1), now)
    # last_call is due-3h; a reminder after it can't leave time to act
    assert "Too late" in remind_error(due, 120, due - timedelta(hours=1), now)
