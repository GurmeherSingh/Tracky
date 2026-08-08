from datetime import UTC, datetime, timedelta

from tracker.models import Alert, Application, Obligation
from tracker.notify.slack import SlackNotifier

NOW = datetime(2026, 8, 10, 5, 30, tzinfo=UTC)
TZ = "America/Los_Angeles"


class FakeWebClient:
    def __init__(self):
        self.posts = []
        self.updates = []
        self.pins = []

    def chat_postMessage(self, channel, text, blocks=None, unfurl_links=False):
        self.posts.append({"channel": channel, "text": text, "blocks": blocks})
        return {"ts": f"171.{len(self.posts)}"}

    def chat_update(self, channel, ts, text, blocks=None):
        self.updates.append({"channel": channel, "ts": ts, "text": text})
        return {"ok": True}

    def pins_add(self, channel, timestamp):
        self.pins.append({"channel": channel, "timestamp": timestamp})
        return {"ok": True}


def test_send_alert_posts_and_records(session):
    app = Application(company_canonical="hrt", company_display="HRT", role_title="SWE",
                      source="applied", first_seen_at=NOW, last_contact_at=NOW,
                      status_derived="assessment")
    session.add(app)
    session.flush()
    ob = Obligation(application_id=app.id, type="assessment", title="OA",
                    due_at=NOW + timedelta(hours=12), due_confidence="stated",
                    status="open", effort_minutes=120)
    session.add(ob)
    session.flush()
    web = FakeWebClient()
    notifier = SlackNotifier(web, "C-ALERTS", "C-TRACKER")
    alert = notifier.send_alert(session, ob, "HRT", "t12", NOW, TZ)
    assert web.posts[0]["channel"] == "C-ALERTS"
    assert session.query(Alert).count() == 1
    assert alert.slack_ts == "171.1" and alert.tier == "t12"


def test_post_tracker_goes_to_tracker_channel():
    web = FakeWebClient()
    notifier = SlackNotifier(web, "C-ALERTS", "C-TRACKER")
    ts = notifier.post_tracker("board text")
    assert web.posts[0]["channel"] == "C-TRACKER"
    assert ts == "171.1"


def test_auth_alarm_goes_to_alerts_channel():
    web = FakeWebClient()
    notifier = SlackNotifier(web, "C-ALERTS", "C-TRACKER")
    notifier.send_auth_alarm()
    assert web.posts[0]["channel"] == "C-ALERTS"
    assert "Gmail auth expired" in web.posts[0]["text"]
