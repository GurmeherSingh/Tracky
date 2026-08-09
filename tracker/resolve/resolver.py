import re

import sqlalchemy as sa
from pydantic import BaseModel

from tracker.classify.schemas import EmailClassification, EmailIn
from tracker.models import Application, Email, Event

_LEGAL_SUFFIXES = re.compile(r"\b(inc|llc|corp|corporation|ltd|plc|gmbh)\b\.?$")


def _join_key(name: str) -> str:
    """Punctuation/case/suffix variance is naming noise, not company identity.

    'North.Cloud' / 'North Cloud' / 'north cloud, Inc.' → 'north cloud'.
    """
    n = re.sub(r"[^\w\s]", " ", name.lower())
    n = " ".join(n.split())
    n = _LEGAL_SUFFIXES.sub("", n)
    return " ".join(n.split())


def _is_variant(a: str, b: str) -> bool:
    """'millennium' vs 'millennium management' — same words, one extends the other."""
    return a != b and (a.startswith(b + " ") or b.startswith(a + " "))

_BOT_LOCALPARTS = ("no-reply", "noreply", "do-not-reply", "donotreply",
                   "notifications", "notification", "mailer")


def _looks_human(from_addr: str) -> bool:
    local = from_addr.split("@")[0].lower()
    return not any(b in local for b in _BOT_LOCALPARTS)


class ResolveResult(BaseModel):
    application_id: int | None = None
    needs_review: bool = False
    review_reason: str | None = None
    created: bool = False


def _touch(app: Application, email: EmailIn) -> None:
    if email.received_at > app.last_contact_at:
        app.last_contact_at = email.received_at
    if _looks_human(email.from_addr):
        app.human_engaged = True


def _thread_application_id(session, thread_id: str) -> int | None:
    if not thread_id:
        return None
    row = (session.query(Event.application_id)
           .join(Email, Email.gmail_id == Event.email_id)
           .filter(Email.thread_id == thread_id)
           .order_by(Event.occurred_at.desc())
           .first())
    return row[0] if row else None


def resolve_application(session, cls: EmailClassification,
                        email: EmailIn) -> ResolveResult:
    """Three-tier ladder: deterministic evidence, then LLM judgment, then human.

    1. Thread inheritance — an email in an already-attached Gmail thread belongs
       to that application, whatever the classifier says. Provable, so it wins.
    2. The classifier's match verdict against the tracked-applications list it
       was shown. An id it names must actually exist — a hallucinated id is
       treated as unsure, never trusted.
    3. Human review only for what the model itself calls unsure.
    """
    thread_app_id = _thread_application_id(session, email.thread_id)
    if thread_app_id is not None:
        app = session.get(Application, thread_app_id)
        _touch(app, email)
        return ResolveResult(application_id=app.id)

    if cls.match_basis == "existing":
        app = (session.get(Application, cls.matched_application_id)
               if cls.matched_application_id is not None else None)
        if app is None:
            return ResolveResult(needs_review=True,
                                 review_reason="llm_matched_unknown_id")
        _touch(app, email)
        return ResolveResult(application_id=app.id)

    if cls.match_basis == "unsure":
        return ResolveResult(needs_review=True, review_reason="llm_unsure_match")

    if not cls.company:
        return ResolveResult(needs_review=True, review_reason="no_company")

    canonical = _join_key(cls.company)
    role = (cls.role_title or "").strip()
    # provable-duplicate guard: identical canonical name AND role means the
    # model said "new" about an application we already track
    dup = (session.query(Application)
           .filter_by(company_canonical=canonical)
           .filter(sa.func.lower(Application.role_title) == role.lower())
           .first()) if role else None
    if dup is not None:
        _touch(dup, email)
        return ResolveResult(application_id=dup.id)

    app = Application(
        company_canonical=canonical, company_display=cls.company,
        role_title=role, source="lead" if cls.category == "recruiter_lead" else "applied",
        first_seen_at=email.received_at, last_contact_at=email.received_at,
        status_derived="applied", human_engaged=_looks_human(email.from_addr))
    session.add(app)
    session.flush()
    return ResolveResult(application_id=app.id, created=True)
