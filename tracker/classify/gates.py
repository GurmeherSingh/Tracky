from typing import Literal

from pydantic import BaseModel

from tracker.classify.schemas import EmailIn

ATS_DOMAINS = frozenset({
    "greenhouse.io", "greenhouse-mail.io", "lever.co", "hire.lever.co",
    "myworkday.com", "myworkdayjobs.com", "ashbyhq.com", "icims.com",
    "smartrecruiters.com", "workable.com", "workablemail.com", "jobvite.com",
    "taleo.net", "successfactors.com", "bamboohr.com", "rippling.com",
})
ASSESSMENT_PLATFORM_DOMAINS = frozenset({
    "hackerrank.com", "hackerrankforwork.com", "codesignal.com",
    "karat.com", "hirevue.com", "coderpad.io", "testgorilla.com",
})
NOISE_SENDERS = frozenset({
    "jobalerts-noreply@linkedin.com", "jobs-noreply@linkedin.com",
    "invitations@linkedin.com", "alert@indeed.com", "noreply@glassdoor.com",
    "handshake@notifications.joinhandshake.com", "digest@handshake-mail.com",
})
NOISE_DOMAINS = frozenset({"e.linkedin.com", "handshake-mail.com", "match.indeed.com"})

# Gate 0 — server-side Gmail search; never fetch what can't be relevant.
_SENDER_CLAUSE = " OR ".join(sorted(ATS_DOMAINS | ASSESSMENT_PLATFORM_DOMAINS))
GMAIL_QUERY = (
    f"(from:({_SENDER_CLAUSE}) "
    "OR subject:(applied OR application OR assessment OR interview OR offer OR "
    "\"coding challenge\" OR \"take-home\" OR \"next steps\"))"
)


class GateResult(BaseModel):
    route: Literal["noise", "llm"]
    reason: str
    ats_hint: bool = False


def _sender_domain(addr: str) -> str:
    return addr.rsplit("@", 1)[-1].lower().strip(">")


def run_gates(email: EmailIn) -> GateResult:
    addr = email.from_addr.lower()
    domain = _sender_domain(addr)
    # Gate 1 — deterministic sender table
    if addr in NOISE_SENDERS or domain in NOISE_DOMAINS:
        return GateResult(route="noise", reason=f"gate1:noise_sender:{domain}")
    ats = domain in ATS_DOMAINS or domain in ASSESSMENT_PLATFORM_DOMAINS or any(
        domain.endswith("." + d) for d in ATS_DOMAINS | ASSESSMENT_PLATFORM_DOMAINS)
    if ats:
        return GateResult(route="llm", reason=f"gate1:ats:{domain}", ats_hint=True)
    # Gate 2 — structural: bulk-marked mail from non-ATS senders is promotional
    h = {k.lower(): v for k, v in email.headers.items()}
    if "list-unsubscribe" in h and h.get("precedence", "").lower() in {"bulk", "list"}:
        return GateResult(route="noise", reason="gate2:bulk_promotional")
    return GateResult(route="llm", reason="gate:default_llm")
