from tenacity import retry, retry_if_exception_type, stop_after_attempt

from tracker.classify.gates import GateResult
from tracker.classify.prompts import SYSTEM_PROMPT
from tracker.classify.schemas import EmailClassification, EmailIn

WORKHORSE_MODEL = "claude-sonnet-5"
HARD_TAIL_MODEL = "claude-opus-5"
CASCADE_THRESHOLD = 0.6
MAX_TOKENS = 1024


class ClassificationFailed(Exception):
    pass


def _user_message(email: EmailIn, gate: GateResult) -> str:
    return (
        f"received-at: {email.received_at.isoformat()}\n"
        f"from: {email.from_addr}\n"
        f"subject: {email.subject}\n"
        f"deterministic-gate-evidence: {gate.reason} (ats_hint={gate.ats_hint})\n"
        f"---\n{email.body_text}"
    )


def _call(client, model: str, email: EmailIn, gate: GateResult) -> EmailClassification:
    response = client.messages.parse(
        model=model,
        max_tokens=MAX_TOKENS,
        system=[{"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": _user_message(email, gate)}],
        output_format=EmailClassification,
    )
    return response.parsed_output


@retry(stop=stop_after_attempt(2), retry=retry_if_exception_type(Exception), reraise=True)
def _call_with_retry(client, model, email, gate):
    return _call(client, model, email, gate)


def classify_email(email: EmailIn, gate: GateResult,
                   client) -> tuple[EmailClassification, str]:
    try:
        cls = _call_with_retry(client, WORKHORSE_MODEL, email, gate)
    except Exception as e:
        raise ClassificationFailed(f"{WORKHORSE_MODEL}: {e}") from e
    if cls.confidence >= CASCADE_THRESHOLD:
        return cls, WORKHORSE_MODEL
    try:
        return _call_with_retry(client, HARD_TAIL_MODEL, email, gate), HARD_TAIL_MODEL
    except Exception as e:
        raise ClassificationFailed(f"{HARD_TAIL_MODEL}: {e}") from e
