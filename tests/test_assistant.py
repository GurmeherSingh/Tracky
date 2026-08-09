from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from tracker.models import Application, Event, Obligation
from tracker.notify.assistant import (answer_question, application_status,
                                      list_open_obligations, set_reminder,
                                      strip_mention)

NOW = datetime(2026, 8, 9, 18, 0, tzinfo=UTC)
TZ = "America/Los_Angeles"


def seed(session):
    app = Application(company_canonical="millennium", company_display="Millennium",
                      role_title="AI Intern", source="applied", first_seen_at=NOW,
                      last_contact_at=NOW, status_derived="assessment")
    session.add(app)
    session.flush()
    session.add(Event(application_id=app.id, kind="assessment", occurred_at=NOW))
    ob = Obligation(application_id=app.id, type="assessment",
                    title="Assessment — Millennium", due_at=NOW + timedelta(hours=30),
                    due_confidence="stated", status="open", effort_minutes=120)
    session.add(ob)
    session.flush()
    return app, ob


def test_strip_mention():
    assert strip_mention("<@U123ABC> what's due?") == "what's due?"


def test_list_open_obligations_reports_hours_left(session):
    seed(session)
    out = list_open_obligations(session, NOW, TZ)
    assert len(out) == 1
    assert out[0]["company"] == "Millennium" and out[0]["hours_left"] == 30.0


def test_application_status_matches_fuzzy_case(session):
    seed(session)
    out = application_status(session, "millen", TZ)
    assert out[0]["status"] == "assessment"
    assert out[0]["recent_events"][0]["kind"] == "assessment"
    assert len(out[0]["open_obligations"]) == 1


def test_set_reminder_validates_and_writes(session):
    _, ob = seed(session)
    bad = set_reminder(session, ob.id, (NOW + timedelta(hours=29)).isoformat(),
                       NOW, TZ)
    assert bad["ok"] is False and "Too late" in bad["error"]
    good = set_reminder(session, ob.id, (NOW + timedelta(hours=5)).isoformat(),
                        NOW, TZ)
    assert good["ok"] is True
    assert ob.next_alert_at == NOW + timedelta(hours=5)
    assert ob.alert_tier == "reminder"


class ScriptedClient:
    """Yields pre-scripted responses; records what the loop sent."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    @property
    def messages(self):
        outer = self

        class M:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                return outer._responses.pop(0)
        return M()


def _tool_use(name, args):
    return SimpleNamespace(stop_reason="tool_use", content=[
        SimpleNamespace(type="tool_use", name=name, input=args, id="tu_1")])


def _text(text):
    return SimpleNamespace(stop_reason="end_turn",
                           content=[SimpleNamespace(type="text", text=text)])


def test_answer_question_runs_tools_then_answers(session):
    seed(session)
    client = ScriptedClient([_tool_use("list_open_obligations", {}),
                             _text("You owe Millennium an assessment.")])
    answer = answer_question(session, client, "what's due?", NOW, TZ)
    assert answer == "You owe Millennium an assessment."
    # the tool result actually flowed back into the second call
    second = client.calls[1]["messages"]
    assert second[-1]["content"][0]["type"] == "tool_result"
    assert "Millennium" in second[-1]["content"][0]["content"]


def test_answer_question_survives_bad_tool_args(session):
    seed(session)
    client = ScriptedClient([_tool_use("set_reminder", {"obligation_id": "x",
                                                        "when_iso": "?"}),
                             _text("Sorry, that didn't work.")])
    answer = answer_question(session, client, "remind me", NOW, TZ)
    assert answer == "Sorry, that didn't work."
    result = client.calls[1]["messages"][-1]["content"][0]["content"]
    assert "error" in result
