import re

from pydantic import BaseModel

from tracker.classify.schemas import EmailClassification, EmailIn
from tracker.models import Application

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


def _role_overlap(a: str, b: str) -> float:
    ta, tb = set(a.lower().split()), set(b.lower().split())
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


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


def resolve_application(session, cls: EmailClassification,
                        email: EmailIn) -> ResolveResult:
    if not cls.company:
        return ResolveResult(needs_review=True, review_reason="no_company")
    canonical = _join_key(cls.company)
    candidates = session.query(Application).filter_by(
        company_canonical=canonical).all()
    role = (cls.role_title or "").strip()

    if role:
        for app in candidates:
            if app.role_title.lower() == role.lower():
                _touch(app, email)
                return ResolveResult(application_id=app.id)
    if candidates:
        if not role:
            if len(candidates) == 1:
                app = candidates[0]
                _touch(app, email)
                return ResolveResult(application_id=app.id)
            return ResolveResult(needs_review=True,
                                 review_reason="ambiguous_company_match")
        fuzzy = [c for c in candidates if _role_overlap(role, c.role_title) > 0.5]
        if len(fuzzy) == 1:
            _touch(fuzzy[0], email)
            return ResolveResult(application_id=fuzzy[0].id)
        if len(fuzzy) > 1:
            return ResolveResult(needs_review=True,
                                 review_reason="ambiguous_company_match")
        # distinct role at a known company → new application (P13)
    else:
        variants = [a for a in session.query(Application).all()
                    if _is_variant(canonical, a.company_canonical)]
        if variants:
            return ResolveResult(needs_review=True,
                                 review_reason="possible_company_variant")

    app = Application(
        company_canonical=canonical, company_display=cls.company,
        role_title=role, source="lead" if cls.category == "recruiter_lead" else "applied",
        first_seen_at=email.received_at, last_contact_at=email.received_at,
        status_derived="applied", human_engaged=_looks_human(email.from_addr))
    session.add(app)
    session.flush()
    return ResolveResult(application_id=app.id, created=True)
