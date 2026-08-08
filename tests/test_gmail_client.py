import base64

import pytest
from googleapiclient.errors import HttpError

from tracker.ingest.gmail_client import GmailClient, HistoryExpired


def b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


class FakeRequest:
    def __init__(self, result=None, error=None):
        self._result, self._error = result, error

    def execute(self):
        if self._error:
            raise self._error
        return self._result


class FakeMessages:
    def __init__(self, pages, messages):
        self._pages, self._messages = pages, messages

    def list(self, userId, q=None, pageToken=None, maxResults=None):
        idx = 0 if pageToken is None else int(pageToken)
        return FakeRequest(result=self._pages[idx])

    def get(self, userId, id, format):
        return FakeRequest(result=self._messages[id])


class FakeHistory:
    def __init__(self, error=None, result=None):
        self._error, self._result = error, result

    def list(self, userId, startHistoryId, historyTypes=None, pageToken=None):
        return FakeRequest(result=self._result, error=self._error)


class FakeUsers:
    def __init__(self, messages, history=None):
        self._messages, self._history = messages, history

    def messages(self):
        return self._messages

    def history(self):
        return self._history

    def getProfile(self, userId):
        return FakeRequest(result={"historyId": "999"})


class FakeService:
    def __init__(self, messages, history=None):
        self._users = FakeUsers(messages, history)

    def users(self):
        return self._users


MSG = {
    "id": "m1",
    "threadId": "t1",
    "internalDate": "1754500000000",
    "payload": {
        "headers": [
            {"name": "From", "value": "HackerRank <no-reply@hackerrank.com>"},
            {"name": "Subject", "value": "Your assessment is ready"},
            {"name": "List-Unsubscribe", "value": "<https://x>"},
        ],
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": b64("Complete within 7 days.")}},
            {"mimeType": "text/html", "body": {"data": b64("<p>html ver</p>")}},
        ],
    },
}


def test_fetch_email_parses_message():
    client = GmailClient(FakeService(FakeMessages([], {"m1": MSG})))
    email = client.fetch_email("m1")
    assert email.gmail_id == "m1"
    assert email.from_addr == "no-reply@hackerrank.com"
    assert email.subject == "Your assessment is ready"
    assert email.body_text == "Complete within 7 days."
    assert email.headers["list-unsubscribe"] == "<https://x>"
    assert email.received_at.tzinfo is not None


def test_list_message_ids_paginates():
    pages = [
        {"messages": [{"id": "a"}, {"id": "b"}], "nextPageToken": "1"},
        {"messages": [{"id": "c"}]},
    ]
    client = GmailClient(FakeService(FakeMessages(pages, {})))
    assert list(client.list_message_ids("q")) == ["a", "b", "c"]


def test_history_404_raises_history_expired():
    resp = type("R", (), {"status": 404, "reason": "notFound"})()
    err = HttpError(resp=resp, content=b"expired")
    client = GmailClient(FakeService(FakeMessages([], {}), FakeHistory(error=err)))
    with pytest.raises(HistoryExpired):
        client.list_history_ids("12345")
