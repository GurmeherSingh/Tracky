from datetime import UTC, datetime

from tracker.classify.gates import GMAIL_QUERY, run_gates
from tracker.classify.schemas import EmailIn

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def make_email(from_addr, subject="hi", headers=None, body="body"):
    return EmailIn(gmail_id="m1", thread_id="t1", from_addr=from_addr,
                   subject=subject, body_text=body, received_at=NOW,
                   headers=headers or {})


def test_gate0_query_covers_core_senders():
    for domain in ["greenhouse.io", "lever.co", "hackerrank.com", "ashbyhq.com"]:
        assert domain in GMAIL_QUERY


def test_gate1_linkedin_job_alerts_is_noise():
    r = run_gates(make_email("jobalerts-noreply@linkedin.com"))
    assert r.route == "noise"


def test_gate1_ats_sender_goes_to_llm_with_hint():
    r = run_gates(make_email("no-reply@greenhouse.io", subject="Your application to Stripe"))
    assert r.route == "llm" and r.ats_hint is True


def test_gate2_bulk_promotional_is_noise():
    r = run_gates(make_email(
        "digest@handshake-mail.com",
        headers={"list-unsubscribe": "<https://x>", "precedence": "bulk"}))
    assert r.route == "noise"


def test_gate2_unsubscribe_alone_does_not_kill_ats_mail():
    # Greenhouse mail often carries list-unsubscribe; ATS evidence wins.
    r = run_gates(make_email("no-reply@greenhouse.io",
                             headers={"list-unsubscribe": "<https://x>"}))
    assert r.route == "llm"


def test_unknown_personal_sender_goes_to_llm():
    r = run_gates(make_email("jane@acmecorp.com", subject="Interview availability"))
    assert r.route == "llm" and r.ats_hint is False
