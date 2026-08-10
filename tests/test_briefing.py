import pytest

from tracker.research.briefing import (WEB_SEARCH_TOOL, BriefingFailed,
                                       build_briefing)


class Text:
    type = "text"

    def __init__(self, text):
        self.text = text


class SearchError:
    """What .content becomes when a server-side search fails — an object, not
    a list, returned inside a perfectly successful HTTP 200."""
    type = "web_search_tool_result_error"

    def __init__(self, code):
        self.error_code = code


class SearchResult:
    type = "web_search_tool_result"

    def __init__(self, content):
        self.content = content


class Msg:
    def __init__(self, content, stop_reason="end_turn"):
        self.content, self.stop_reason = content, stop_reason


class FakeStream:
    def __init__(self, msg):
        self._msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._msg


class FakeMessages:
    def __init__(self, script):
        self._script, self.calls = list(script), []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return FakeStream(self._script.pop(0))


class FakeClient:
    def __init__(self, script):
        self.messages = FakeMessages(script)


def test_declares_the_current_search_tool_and_no_code_execution():
    client = FakeClient([Msg([Text("## What they do\n- payments")])])
    build_briefing(client, "Stripe", "SWE Intern")
    tools = client.messages.calls[0]["tools"]
    assert tools == [WEB_SEARCH_TOOL]
    # pinned deliberately: the 20250305 form is for pre-4.6 models and would
    # fail at runtime on opus-5
    assert WEB_SEARCH_TOOL["type"] == "web_search_20260209"
    assert not any("code_execution" in t["type"] for t in tools)


def test_the_company_and_role_reach_the_prompt():
    client = FakeClient([Msg([Text("x")])])
    build_briefing(client, "Epic Games", "Gameplay Intern")
    sent = client.messages.calls[0]["messages"][0]["content"]
    assert "Epic Games" in sent and "Gameplay Intern" in sent


def test_pause_turn_is_resumed_and_both_halves_are_kept():
    first = Msg([Text("## What they do")], stop_reason="pause_turn")
    second = Msg([Text("- payments infrastructure")])
    client = FakeClient([first, second])

    md = build_briefing(client, "Stripe")

    assert md == "## What they do\n- payments infrastructure"
    assert len(client.messages.calls) == 2
    resumed = client.messages.calls[1]["messages"][-1]
    assert resumed["role"] == "assistant"
    assert resumed["content"] is first.content


def test_a_search_error_raises_rather_than_returning_unsourced_prose():
    client = FakeClient([Msg([
        SearchResult(SearchError("max_uses_exceeded")),
        Text("Stripe is a payments company."),
    ])])
    with pytest.raises(BriefingFailed, match="max_uses_exceeded"):
        build_briefing(client, "Stripe")


def test_a_search_that_never_finishes_gives_up():
    client = FakeClient([Msg([Text("x")], stop_reason="pause_turn")
                         for _ in range(12)])
    with pytest.raises(BriefingFailed, match="converge"):
        build_briefing(client, "Stripe")


def test_an_empty_briefing_is_a_failure_not_a_blank_page():
    client = FakeClient([Msg([Text("   ")])])
    with pytest.raises(BriefingFailed, match="empty"):
        build_briefing(client, "Stripe")
