import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa

from tracker.models import Application, Event, Obligation
from tracker.obligations.escalation import remind_error

MODEL = "claude-sonnet-5"
MAX_TOOL_ROUNDS = 5

SYSTEM = """You are Tracky, a Slack assistant for one job-seeker's application
ledger. Answer ONLY from tool results — never invent applications, deadlines,
or statuses. Be concise; use Slack mrkdwn (*bold*, bullets). Times shown to the
user should be in their local timezone ({tz}). The current time is {now}.
For set_reminder, pass an ISO-8601 datetime; interpret the user's casual times
("at 6", "tonight") in their timezone."""


def strip_mention(text: str) -> str:
    return re.sub(r"<@[^>]+>", "", text or "").strip()


def _fmt(dt: datetime, tz: str) -> str:
    local = dt.astimezone(ZoneInfo(tz))
    return f"{local.strftime('%a %b')} {local.day}, {local.strftime('%I:%M %p').lstrip('0')}"


def list_open_obligations(session, now: datetime, tz: str) -> list[dict]:
    rows = (session.query(Obligation, Application.company_display)
            .join(Application, Obligation.application_id == Application.id)
            .filter(Obligation.status.in_(["open", "unconfirmed",
                                           "unconfirmed_possibly_missed"]))
            .order_by(Obligation.due_at).all())
    return [{"obligation_id": ob.id, "company": company, "type": ob.type,
             "title": ob.title, "due_local": _fmt(ob.due_at, tz),
             "hours_left": round((ob.due_at - now).total_seconds() / 3600, 1),
             "due_confidence": ob.due_confidence, "status": ob.status}
            for ob, company in rows]


def application_status(session, company: str, tz: str) -> list[dict]:
    pattern = f"%{company.lower()}%"
    apps = (session.query(Application)
            .filter(sa.or_(sa.func.lower(Application.company_display).like(pattern),
                           Application.company_canonical.like(pattern)))
            .all())
    out = []
    for app in apps:
        events = (session.query(Event).filter_by(application_id=app.id)
                  .order_by(Event.occurred_at.desc()).limit(5).all())
        obs = (session.query(Obligation)
               .filter(Obligation.application_id == app.id,
                       Obligation.status.in_(["open", "unconfirmed"])).all())
        out.append({"company": app.company_display, "role": app.role_title,
                    "status": app.status_derived,
                    "last_contact": _fmt(app.last_contact_at, tz),
                    "recent_events": [{"kind": e.kind,
                                       "at": _fmt(e.occurred_at, tz)}
                                      for e in events],
                    "open_obligations": [{"obligation_id": o.id, "type": o.type,
                                          "due_local": _fmt(o.due_at, tz)}
                                         for o in obs]})
    return out


def recent_activity(session, days: int, now: datetime, tz: str) -> list[dict]:
    since = now - timedelta(days=min(max(days, 1), 90))
    rows = (session.query(Event, Application.company_display)
            .join(Application, Event.application_id == Application.id)
            .filter(Event.occurred_at >= since)
            .order_by(Event.occurred_at.desc()).limit(50).all())
    return [{"company": company, "kind": ev.kind,
             "at": _fmt(ev.occurred_at, tz)} for ev, company in rows]


def set_reminder(session, obligation_id: int, when_iso: str,
                 now: datetime, tz: str) -> dict:
    ob = session.get(Obligation, obligation_id)
    if ob is None or ob.status not in {"open", "unconfirmed"}:
        return {"ok": False, "error": "no open obligation with that id"}
    chosen = datetime.fromisoformat(when_iso)
    if chosen.tzinfo is None:
        chosen = chosen.replace(tzinfo=ZoneInfo(tz))
    err = remind_error(ob.due_at, ob.effort_minutes, chosen, now)
    if err:
        return {"ok": False, "error": err}
    ob.next_alert_at = chosen
    ob.alert_tier = "reminder"
    return {"ok": True, "reminder_local": _fmt(chosen, tz), "title": ob.title}


TOOL_SCHEMAS = [
    {"name": "list_open_obligations",
     "description": "Everything currently owed: open/unconfirmed obligations "
                    "with company, due time, and hours left, soonest first.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "application_status",
     "description": "Status, recent events, and open obligations for "
                    "applications whose company name matches (fuzzy).",
     "input_schema": {"type": "object",
                      "properties": {"company": {"type": "string"}},
                      "required": ["company"]}},
    {"name": "recent_activity",
     "description": "Ledger events (confirmations, interviews, rejections...) "
                    "from the last N days, newest first.",
     "input_schema": {"type": "object",
                      "properties": {"days": {"type": "integer"}},
                      "required": ["days"]}},
    {"name": "set_reminder",
     "description": "Schedule a re-alert for an open obligation at a chosen "
                    "time (ISO-8601). Rejected if the time is in the past or "
                    "too close to the deadline to leave time to act.",
     "input_schema": {"type": "object",
                      "properties": {"obligation_id": {"type": "integer"},
                                     "when_iso": {"type": "string"}},
                      "required": ["obligation_id", "when_iso"]}},
]


def _dispatch(session, name: str, args: dict, now: datetime, tz: str):
    if name == "list_open_obligations":
        return list_open_obligations(session, now, tz)
    if name == "application_status":
        return application_status(session, str(args["company"]), tz)
    if name == "recent_activity":
        return recent_activity(session, int(args["days"]), now, tz)
    if name == "set_reminder":
        return set_reminder(session, int(args["obligation_id"]),
                            str(args["when_iso"]), now, tz)
    return {"error": f"unknown tool {name}"}


def answer_question(session, client, question: str,
                    now: datetime, tz: str) -> str:
    system = SYSTEM.format(tz=tz, now=now.astimezone(ZoneInfo(tz)).isoformat())
    messages = [{"role": "user", "content": question}]
    for _ in range(MAX_TOOL_ROUNDS):
        resp = client.messages.create(model=MODEL, max_tokens=700,
                                      system=system, tools=TOOL_SCHEMAS,
                                      messages=messages)
        if resp.stop_reason != "tool_use":
            return "".join(b.text for b in resp.content
                           if getattr(b, "type", "") == "text").strip()
        messages.append({"role": "assistant", "content": resp.content})
        results = []
        for block in resp.content:
            if getattr(block, "type", "") == "tool_use":
                try:
                    out = _dispatch(session, block.name, block.input, now, tz)
                except Exception as e:  # a bad tool call must not kill the answer
                    out = {"error": str(e)}
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(out, default=str)})
        messages.append({"role": "user", "content": results})
    return "I ran out of tool calls before finishing — try a narrower question."
