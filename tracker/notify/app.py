import re
import threading

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from tracker.db import session_scope
from tracker.models import Alert, Obligation, utcnow
from tracker.notify.review import (handle_review_assign, handle_review_ignore,
                                   handle_review_new, is_review_resolved,
                                   resolved_blocks)


def _mark_resolved(client, body, note: str) -> None:
    container = body.get("container", {})
    channel, ts = container.get("channel_id"), container.get("message_ts")
    if not channel or not ts:
        return
    blocks = resolved_blocks(body.get("message", {}).get("blocks", []), note)
    client.chat_update(channel=channel, ts=ts, text=note, blocks=blocks)


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
                ob.status = "completed"
                ob.closed_by = "manual"
                ob.closed_at = utcnow()
                ob.next_alert_at = None

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
