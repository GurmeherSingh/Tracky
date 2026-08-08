from datetime import UTC, datetime

import pytest

from tracker.classify.gates import GateResult
from tracker.classify.llm import (CASCADE_THRESHOLD, HARD_TAIL_MODEL,
                                  WORKHORSE_MODEL, ClassificationFailed,
                                  classify_email)
from tracker.classify.schemas import EmailClassification, EmailIn

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)
EMAIL = EmailIn(gmail_id="m1", thread_id="t1", from_addr="no-reply@hackerrank.com",
                subject="Assessment", body_text="Complete within 7 days",
                received_at=NOW, headers={})
GATE = GateResult(route="llm", reason="gate1:ats:hackerrank.com", ats_hint=True)


def make_cls(confidence, category="assessment"):
    return EmailClassification(
        is_my_application=True, category=category, company="Stripe",
        role_title="SWE Intern", deadline_utc=None, deadline_basis="none",
        actionable=True, confidence=confidence, reasoning="assessment link present")


class FakeResponse:
    def __init__(self, parsed):
        self.parsed_output = parsed


class FakeClient:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []

    class _Messages:
        def __init__(self, outer):
            self._outer = outer

        def parse(self, **kwargs):
            self._outer.calls.append(kwargs)
            result = self._outer._results.pop(0)
            if isinstance(result, Exception):
                raise result
            return FakeResponse(result)

    @property
    def messages(self):
        return FakeClient._Messages(self)


def test_confident_sonnet_verdict_returned_directly():
    client = FakeClient([make_cls(0.95)])
    cls, model = classify_email(EMAIL, GATE, client)
    assert model == WORKHORSE_MODEL and cls.confidence == 0.95
    assert client.calls[0]["model"] == WORKHORSE_MODEL
    # caching: system block carries cache_control
    assert client.calls[0]["system"][0]["cache_control"] == {"type": "ephemeral"}
    # received_at is in the user turn so relative deadlines can resolve
    assert "2026-08-06" in client.calls[0]["messages"][0]["content"]


def test_low_confidence_cascades_to_opus():
    client = FakeClient([make_cls(CASCADE_THRESHOLD - 0.1), make_cls(0.9)])
    cls, model = classify_email(EMAIL, GATE, client)
    assert model == HARD_TAIL_MODEL and cls.confidence == 0.9
    assert [c["model"] for c in client.calls] == [WORKHORSE_MODEL, HARD_TAIL_MODEL]


def test_two_failures_raise_classification_failed():
    client = FakeClient([RuntimeError("boom"), RuntimeError("boom")])
    with pytest.raises(ClassificationFailed):
        classify_email(EMAIL, GATE, client)
