from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

EFFORT_MINUTES = {"assessment": 120, "take_home": 240,
                  "scheduling_reply": 5, "offer_response": 60}
BUFFER_MINUTES = 60
TIERS = ["detection", "t48", "t12", "last_call"]


def tier_time(tier: str, due_at: datetime, effort_minutes: int) -> datetime:
    if tier == "t48":
        return due_at - timedelta(hours=48)
    if tier == "t12":
        return due_at - timedelta(hours=12)
    if tier == "last_call":
        return due_at - timedelta(minutes=effort_minutes + BUFFER_MINUTES)
    raise ValueError(tier)


def clamp_to_waking(dt: datetime, tz: str) -> datetime:
    """No alerts 01:00-07:00 local. Pull earlier (to 00:59), never later."""
    local = dt.astimezone(ZoneInfo(tz))
    if 1 <= local.hour < 7:
        local = local.replace(hour=0, minute=59, second=0, microsecond=0)
    return local.astimezone(dt.tzinfo)


def next_alert_after(current_tier: str | None, due_at: datetime,
                     effort_minutes: int, now: datetime,
                     tz: str) -> tuple[str, datetime] | None:
    start = TIERS.index(current_tier) + 1 if current_tier else 0
    for tier in TIERS[start:]:
        if tier == "detection":
            continue  # detection fires at creation time, not by schedule
        at = clamp_to_waking(tier_time(tier, due_at, effort_minutes), tz)
        if at > now:
            return tier, at
    return None
