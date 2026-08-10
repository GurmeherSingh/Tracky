from datetime import datetime

from slack_sdk.errors import SlackApiError

from tracker.models import Alert, Obligation
from tracker.notify.fmt import render_alert, render_alert_blocks, render_auth_alarm


class SlackNotifier:
    def __init__(self, web_client, alerts_channel: str, tracker_channel: str):
        self.web = web_client
        self.alerts_channel = alerts_channel
        self.tracker_channel = tracker_channel

    def send_alert(self, session, ob: Obligation, company_display: str,
                   tier: str, now: datetime, tz: str) -> Alert:
        # every escalation threads under the obligation's first alert; broadcast
        # keeps channel notifications intact (plain thread replies would get
        # QUIETER as the deadline approaches — the opposite of escalation)
        root = (session.query(Alert)
                .filter(Alert.obligation_id == ob.id, Alert.slack_ts.isnot(None))
                .order_by(Alert.sent_at.asc(), Alert.id.asc()).first())
        prev = (session.query(Alert)
                .filter(Alert.obligation_id == ob.id, Alert.slack_ts.isnot(None))
                .order_by(Alert.sent_at.desc(), Alert.id.desc()).first())
        # flush first: the buttons carry the alert row's id
        alert = Alert(obligation_id=ob.id, channel=self.alerts_channel, tier=tier)
        session.add(alert)
        session.flush()
        text = render_alert(ob, company_display, tier, now, tz)
        blocks = render_alert_blocks(ob, company_display, tier, now, tz, alert.id)
        kwargs = ({"thread_ts": root.slack_ts, "reply_broadcast": True}
                  if root else {})
        try:
            resp = self.web.chat_postMessage(channel=self.alerts_channel,
                                             text=text, blocks=blocks,
                                             unfurl_links=False, **kwargs)
        except SlackApiError:
            if not kwargs:
                raise
            # thread root was deleted — start a new top-level story
            resp = self.web.chat_postMessage(channel=self.alerts_channel,
                                             text=text, blocks=blocks,
                                             unfurl_links=False)
        alert.slack_ts = resp["ts"]
        session.flush()
        if prev and prev.slack_ts:
            # the newest alert is the live handle — retire the previous buttons
            try:
                self.web.chat_update(
                    channel=prev.channel, ts=prev.slack_ts,
                    text=render_alert(ob, company_display, prev.tier, now, tz))
            except SlackApiError:
                pass  # cosmetic; stale buttons answer "already closed" anyway
        return alert

    def post_tracker(self, text: str, blocks: list | None = None) -> str:
        resp = self.web.chat_postMessage(channel=self.tracker_channel, text=text,
                                         blocks=blocks, unfurl_links=False)
        return resp["ts"]

    def update_message(self, channel: str, ts: str, text: str,
                       blocks: list | None = None) -> None:
        self.web.chat_update(channel=channel, ts=ts, text=text, blocks=blocks)

    def post_alert(self, text: str) -> None:
        self.web.chat_postMessage(channel=self.alerts_channel, text=text,
                                  unfurl_links=False)

    def send_auth_alarm(self) -> None:
        self.web.chat_postMessage(channel=self.alerts_channel,
                                  text=render_auth_alarm(), unfurl_links=False)
