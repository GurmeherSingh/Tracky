from pydantic import BaseModel

from tracker.classify.schemas import EmailClassification, EmailIn
from tracker.models import Application


def _join_key(name: str) -> str:
    """Case/whitespace variance is LLM output noise, not naming signal."""
    return " ".join(name.lower().split())

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
        if len(candidates) == 1 and (not role or
                _role_overlap(role, candidates[0].role_title) > 0.5):
            app = candidates[0]
            _touch(app, email)
            return ResolveResult(application_id=app.id)
        if not role:
            return ResolveResult(needs_review=True,
                                 review_reason="ambiguous_company_match")

    app = Application(
        company_canonical=canonical, company_display=cls.company,
        role_title=role, source="lead" if cls.category == "recruiter_lead" else "applied",
        first_seen_at=email.received_at, last_contact_at=email.received_at,
        status_derived="applied", human_engaged=_looks_human(email.from_addr))
    session.add(app)
    session.flush()
    return ResolveResult(application_id=app.id, created=True)
