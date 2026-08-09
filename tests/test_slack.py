from datetime import UTC, datetime, timedelta

from slack_sdk.errors import SlackApiError

from tracker.models import Alert, Application, Obligation
from tracker.notify.slack import SlackNotifier

NOW = datetime(2026, 8, 10, 5, 30, tzinfo=UTC)
TZ = "America/Los_Angeles"


class FakeWebClient:
    def __init__(self):
        self.posts = []
        self.updates = []
        self.pins = []
        self.unpins = []
        self.history = []
        self.fail_update_once = False

    def chat_postMessage(self, channel, text, blocks=None, unfurl_links=False,
                         **kwargs):
        self.posts.append({"channel": channel, "text": text, "blocks": blocks,
                           **kwargs})
        return {"ts": f"171.{len(self.posts)}"}

    def chat_update(self, channel, ts, text, blocks=None):
        if self.fail_update_once:
            self.fail_update_once = False
            raise SlackApiError("message_not_found",
                                {"error": "message_not_found"})
        self.updates.append({"channel": channel, "ts": ts, "text": text,
                             "blocks": blocks})
        return {"ok": True}

    def pins_add(self, channel, timestamp):
        self.pins.append({"channel": channel, "timestamp": timestamp})
        return {"ok": True}

    def pins_remove(self, channel, timestamp):
        self.unpins.append({"channel": channel, "timestamp": timestamp})
        return {"ok": True}

    def conversations_history(self, channel, limit=50):
        return {"messages": self.history}


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


def seed_ob(session, source_email=None):
    app = Application(company_canonical="hrt", company_display="HRT",
                      role_title="SWE", source="applied", first_seen_at=NOW,
                      last_contact_at=NOW, status_derived="assessment")
    session.add(app)
    session.flush()
    ob = Obligation(application_id=app.id, type="assessment", title="OA",
                    due_at=NOW + timedelta(hours=30), due_confidence="stated",
                    status="open", effort_minutes=120,
                    source_email_id=source_email)
    session.add(ob)
    session.flush()
    return ob


def test_alert_carries_action_buttons(session):
    ob = seed_ob(session)
    web = FakeWebClient()
    SlackNotifier(web, "C-A", "C-T").send_alert(session, ob, "HRT", "detection",
                                                NOW, TZ)
    actions = [b for b in web.posts[0]["blocks"] if b["type"] == "actions"][0]
    ids = [e["action_id"] for e in actions["elements"]]
    assert ids == ["alert_done", "alert_remind", "alert_junk"]
    alert = session.query(Alert).one()
    assert actions["elements"][0]["value"] == f"{alert.id}:{ob.id}"


def test_alert_with_source_email_gets_open_button(session):
    ob = seed_ob(session, source_email="abc123")
    web = FakeWebClient()
    SlackNotifier(web, "C-A", "C-T").send_alert(session, ob, "HRT", "detection",
                                                NOW, TZ)
    actions = [b for b in web.posts[0]["blocks"] if b["type"] == "actions"][0]
    assert actions["elements"][-1]["url"] == "https://mail.google.com/mail/#all/abc123"


def test_escalation_threads_under_detection_and_broadcasts(session):
    ob = seed_ob(session)
    web = FakeWebClient()
    notifier = SlackNotifier(web, "C-A", "C-T")
    notifier.send_alert(session, ob, "HRT", "detection", NOW, TZ)
    notifier.send_alert(session, ob, "HRT", "t12", NOW + timedelta(hours=18), TZ)
    assert "thread_ts" not in web.posts[0]
    assert web.posts[1]["thread_ts"] == "171.1"
    assert web.posts[1]["reply_broadcast"] is True


def test_new_alert_retires_previous_buttons(session):
    ob = seed_ob(session)
    web = FakeWebClient()
    notifier = SlackNotifier(web, "C-A", "C-T")
    notifier.send_alert(session, ob, "HRT", "detection", NOW, TZ)
    notifier.send_alert(session, ob, "HRT", "t12", NOW + timedelta(hours=18), TZ)
    assert web.updates[0]["ts"] == "171.1"      # detection message edited
    assert web.updates[0]["blocks"] is None     # buttons gone, text only


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
