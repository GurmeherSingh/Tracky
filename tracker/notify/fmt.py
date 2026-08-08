from datetime import datetime
from zoneinfo import ZoneInfo

from tracker.models import Obligation

TIER_EMOJI = {"detection": "🆕", "t48": "⏳", "t12": "🚨", "last_call": "🔥"}
TIER_LABEL = {"detection": "New obligation", "t48": "T-48h", "t12": "T-12h",
              "last_call": "LAST CALL"}


def _fmt_local(dt: datetime, tz: str) -> str:
    local = dt.astimezone(ZoneInfo(tz))
    return (f"{local.strftime('%a %b')} {local.day}, "
            f"{local.strftime('%I:%M %p %Z').lstrip('0')}")


def _countdown(due_at: datetime, now: datetime) -> str:
    total_min = int((due_at - now).total_seconds() // 60)
    if total_min < 0:
        return "OVERDUE"
    return f"in {total_min // 60}h {total_min % 60}m"


def render_alert(ob: Obligation, company_display: str, tier: str,
                 now: datetime, tz: str) -> str:
    parts = [
        f"{TIER_EMOJI[tier]} {TIER_LABEL[tier]} — {ob.title.split(' — ')[0]} — "
        f"{company_display} — due {_fmt_local(ob.due_at, tz)} "
        f"({_countdown(ob.due_at, now)})"
    ]
    if ob.due_confidence == "assumed":
        parts.append("[assumed deadline]")
    if ob.status == "unconfirmed_possibly_missed":
        parts.append("⚠️ possibly missed")
    return " ".join(parts)


def render_auth_alarm() -> str:
    return ("⚠️ *Gmail auth expired* — ingestion is DOWN. "
            "Re-run the OAuth flow locally to refresh token.json, then restart. "
            "No email has been read since this message.")
