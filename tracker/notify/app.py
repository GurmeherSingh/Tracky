import json
import re
import threading
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from tracker.db import session_scope
from tracker.models import Alert, Obligation, utcnow
from tracker.notify.review import (handle_review_assign, handle_review_ignore,
                                   handle_review_new, is_review_resolved,
                                   resolved_blocks)
from tracker.obligations.escalation import remind_error, tier_for
from tracker.obligations.lifecycle import close_obligation


def _mark_resolved(client, body, note: str, keep_actions: bool = False) -> None:
    container = body.get("container", {})
    channel, ts = container.get("channel_id"), container.get("message_ts")
    if not channel or not ts:
        return
    blocks = resolved_blocks(body.get("message", {}).get("blocks", []), note,
                             keep_actions=keep_actions)
    client.chat_update(channel=channel, ts=ts, text=note, blocks=blocks)


def _alert_ids(body) -> tuple[int, int]:
    alert_id, ob_id = body["actions"][0]["value"].split(":")
    return int(alert_id), int(ob_id)


def build_app(settings, engine) -> App:
    app = App(token=settings.slack_bot_token)

    @app.action("review_new")
    def _new(ack, body, client):
        ack()
        email_id = body["actions"][0]["value"]
        with session_scope(engine) as session:
            if is_review_resolved(session, email_id):
                note = "✔ already resolved"
            else:
                handle_review_new(session, email_id, settings.timezone)
                note = "✔ Created new application"
        _mark_resolved(client, body, note)

    @app.action(re.compile(r"review_assign_\d+"))
    def _assign(ack, body, client):
        ack()
        email_id, app_id = body["actions"][0]["value"].split(":")
        with session_scope(engine) as session:
            if is_review_resolved(session, email_id):
                note = "✔ already resolved"
            else:
                handle_review_assign(session, email_id, int(app_id),
                                     settings.timezone)
                note = f"✔ {body['actions'][0]['text']['text']}"
        _mark_resolved(client, body, note)

    @app.action("review_ignore")
    def _ignore(ack, body, client):
        ack()
        email_id = body["actions"][0]["value"]
        with session_scope(engine) as session:
            if is_review_resolved(session, email_id):
                note = "✔ already resolved"
            else:
                handle_review_ignore(session, email_id)
                note = "✖ Ignored"
        _mark_resolved(client, body, note)

    @app.action("alert_done")
    def _alert_done(ack, body, client):
        ack()
        _, ob_id = _alert_ids(body)
        with session_scope(engine) as session:
            ob = session.get(Obligation, ob_id)
            if ob and ob.status in {"open", "unconfirmed"}:
                close_obligation(ob, "completed", "manual", utcnow())
                note = "✔ closed manually"
            else:
                note = "✔ already closed"
        _mark_resolved(client, body, note)

    @app.action("alert_remind")
    def _alert_remind(ack, body, client):
        ack()
        _, ob_id = _alert_ids(body)
        container = body.get("container", {})
        blocks = body.get("message", {}).get("blocks", [])
        section = next((b["text"]["text"] for b in blocks
                        if b.get("type") == "section"), "")
        meta = json.dumps({"ob": ob_id,
                           "channel": container.get("channel_id", ""),
                           "ts": container.get("message_ts", ""),
                           "text": section[:2500]})
        default = utcnow() + timedelta(hours=3)
        client.views_open(trigger_id=body["trigger_id"], view={
            "type": "modal", "callback_id": "remind_submit",
            "private_metadata": meta,
            "title": {"type": "plain_text", "text": "Remind me"},
            "submit": {"type": "plain_text", "text": "Set reminder"},
            "blocks": [{"type": "input", "block_id": "when",
                        "label": {"type": "plain_text",
                                  "text": "When should I re-alert you?"},
                        "element": {"type": "datetimepicker", "action_id": "at",
                                    "initial_date_time": int(default.timestamp())}}],
        })

    @app.view("remind_submit")
    def _remind_submit(ack, view, client):
        meta = json.loads(view["private_metadata"])
        unix = view["state"]["values"]["when"]["at"]["selected_date_time"]
        chosen = datetime.fromtimestamp(unix, UTC)
        with session_scope(engine) as session:
            ob = session.get(Obligation, meta["ob"])
            if ob is None or ob.status not in {"open", "unconfirmed"}:
                ack()
                note = "✔ already closed"
            else:
                err = remind_error(ob.due_at, ob.effort_minutes, chosen, utcnow())
                if err:
                    ack(response_action="errors", errors={"when": err})
                    return
                ob.next_alert_at = chosen
                # re-slot into the rung that will be truthful when it fires
                ob.alert_tier = tier_for(ob.due_at, ob.effort_minutes, chosen)
                ack()
                local = chosen.astimezone(ZoneInfo(settings.timezone))
                note = f"⏰ reminder set for {local.strftime('%a %I:%M %p').lstrip('0')}"
        if meta["channel"] and meta["ts"]:
            blocks = [{"type": "section",
                       "text": {"type": "mrkdwn", "text": meta["text"]}},
                      {"type": "context",
                       "elements": [{"type": "mrkdwn", "text": note}]}]
            client.chat_update(channel=meta["channel"], ts=meta["ts"],
                               text=note, blocks=blocks)

    @app.action("alert_junk")
    def _alert_junk(ack, body, client):
        ack()
        alert_id, _ = _alert_ids(body)
        with session_scope(engine) as session:
            alert = session.get(Alert, alert_id)
            if alert:
                alert.marked_junk = True
        _mark_resolved(client, body, "🗑 marked junk")

    @app.action("alert_open")
    def _alert_open(ack):
        ack()  # URL button — Slack opens the link; nothing to do

    @app.event("reaction_added")
    def _reaction(event):
        ts = event.get("item", {}).get("ts")
        reaction = event.get("reaction")
        if not ts or reaction not in {"white_check_mark", "x"}:
            return
        with session_scope(engine) as session:
            alert = session.query(Alert).filter_by(slack_ts=ts).one_or_none()
            if alert is None:
                return
            if reaction == "x":
                alert.marked_junk = True
                return
            ob = session.get(Obligation, alert.obligation_id)
            if ob and ob.status in {"open", "unconfirmed"}:
                close_obligation(ob, "completed", "manual", utcnow())

    return app


def run_socket_mode(settings, engine, handler=None, block=None) -> None:
    # SocketModeHandler.start() installs a SIGINT handler, which raises
    # ValueError outside the main thread; connect() + parking this thread
    # gives identical behavior without touching signals
    if handler is None:
        handler = SocketModeHandler(build_app(settings, engine),
                                    settings.slack_app_token)
    handler.connect()
    (block or threading.Event().wait)()
