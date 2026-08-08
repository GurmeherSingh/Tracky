from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from tracker.db import session_scope
from tracker.models import Alert, Obligation, utcnow
from tracker.notify.review import (handle_review_assign, handle_review_ignore,
                                   handle_review_new)


def build_app(settings, engine) -> App:
    app = App(token=settings.slack_bot_token)

    @app.action("review_new")
    def _new(ack, body):
        ack()
        email_id = body["actions"][0]["value"]
        with session_scope(engine) as session:
            handle_review_new(session, email_id, settings.timezone)

    @app.action("review_assign")
    def _assign(ack, body):
        ack()
        email_id, app_id = body["actions"][0]["value"].split(":")
        with session_scope(engine) as session:
            handle_review_assign(session, email_id, int(app_id), settings.timezone)

    @app.action("review_ignore")
    def _ignore(ack, body):
        ack()
        with session_scope(engine) as session:
            handle_review_ignore(session, body["actions"][0]["value"])

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


def run_socket_mode(settings, engine) -> None:
    handler = SocketModeHandler(build_app(settings, engine), settings.slack_app_token)
    handler.start()
