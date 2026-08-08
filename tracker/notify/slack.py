from datetime import datetime

from tracker.models import Alert, Obligation
from tracker.notify.fmt import render_alert, render_auth_alarm


class SlackNotifier:
    def __init__(self, web_client, alerts_channel: str, tracker_channel: str):
        self.web = web_client
        self.alerts_channel = alerts_channel
        self.tracker_channel = tracker_channel

    def send_alert(self, session, ob: Obligation, company_display: str,
                   tier: str, now: datetime, tz: str,
                   model_confidence: float | None = None) -> Alert:
        text = render_alert(ob, company_display, tier, now, tz)
        resp = self.web.chat_postMessage(channel=self.alerts_channel, text=text,
                                         unfurl_links=False)
        alert = Alert(obligation_id=ob.id, channel=self.alerts_channel, tier=tier,
                      slack_ts=resp["ts"], model_confidence=model_confidence)
        session.add(alert)
        session.flush()
        return alert

    def post_tracker(self, text: str, blocks: list | None = None) -> str:
        resp = self.web.chat_postMessage(channel=self.tracker_channel, text=text,
                                         blocks=blocks, unfurl_links=False)
        return resp["ts"]

    def update_message(self, channel: str, ts: str, text: str,
                       blocks: list | None = None) -> None:
        self.web.chat_update(channel=channel, ts=ts, text=text, blocks=blocks)

    def send_auth_alarm(self) -> None:
        self.web.chat_postMessage(channel=self.alerts_channel,
                                  text=render_auth_alarm(), unfurl_links=False)
