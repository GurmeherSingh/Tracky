from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from tracker.models import Application, Obligation

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


def render_board(open_obs: list[tuple[Obligation, str]],
                 quiet: list[Application], now: datetime, tz: str) -> str:
    lines = ["*📋 Obligation Board*", ""]
    if not open_obs:
        lines.append("✅ No open obligations. Nothing is owed.")
    else:
        for ob, company in sorted(open_obs, key=lambda p: p[0].due_at):
            flag = " ⚠️ possibly missed" if ob.status == "unconfirmed_possibly_missed" else ""
            assumed = " [assumed]" if ob.due_confidence == "assumed" else ""
            lines.append(f"• {ob.title.split(' — ')[0]} — *{company}* — "
                         f"due {_fmt_local(ob.due_at, tz)} "
                         f"({_countdown(ob.due_at, now)}){assumed}{flag}")
    if quiet:
        lines += ["", "*🔇 Gone quiet (>14d after human contact)*"]
        lines += [f"• {a.company_display} — {a.role_title}" for a in quiet]
    lines += ["", f"_Updated {_fmt_local(now, tz)}_"]
    return "\n".join(lines)


class DigestData(BaseModel):
    open_obs: list[tuple[str, str, str]]
    movement: list[str]
    today_interviews: list[str]
    unconfirmed: list[str]
    review_count: int
    monday_trend: str | None = None


def render_digest(data: DigestData, now: datetime, tz: str) -> str:
    lines = [f"*☀️ Daily digest — {_fmt_local(now, tz)}*", ""]
    if data.open_obs:
        lines.append("*Open obligations (by urgency):*")
        lines += [f"• {t} — *{c}* — {d}" for t, c, d in data.open_obs]
    else:
        lines.append("✅ Nothing open.")
    if data.today_interviews:
        lines += ["", "*Today:*"] + [f"• {s}" for s in data.today_interviews]
    if data.movement:
        lines += ["", "*Last 24h:*"] + [f"• {s}" for s in data.movement]
    if data.unconfirmed:
        lines += ["", "*⚠️ Unconfirmed / possibly missed:*"] + [f"• {s}" for s in data.unconfirmed]
    if data.review_count:
        lines += ["", f"*🔎 {data.review_count} item(s) waiting in the review queue*"]
    if data.monday_trend:
        lines += ["", f"*📈 {data.monday_trend}*"]
    return "\n".join(lines)


def render_auth_alarm() -> str:
    return ("⚠️ *Gmail auth expired* — ingestion is DOWN. "
            "Re-run the OAuth flow locally to refresh token.json, then restart. "
            "No email has been read since this message.")
