from datetime import datetime

import sqlalchemy as sa

from tracker.classify.gates import run_gates
from tracker.classify.llm import classify_email
from tracker.classify.prompts import PROMPT_VERSION
from tracker.classify.schemas import EmailIn
from tracker.evals.metrics import EvalReport, score
from tracker.models import (Alert, Classification, Email, EvalLabel,
                            FailedJob, Obligation)


def _to_email_in(row: Email) -> EmailIn:
    return EmailIn(gmail_id=row.gmail_id, thread_id=row.thread_id,
                   from_addr=row.from_addr, subject=row.subject,
                   body_text=row.body_text, received_at=row.received_at,
                   headers=row.raw.get("headers", {}))


def export_labels(session) -> str:
    added = 0
    emails = (session.query(Email)
              .join(Classification, Classification.email_id == Email.gmail_id)
              .outerjoin(EvalLabel, EvalLabel.email_id == Email.gmail_id)
              .filter(EvalLabel.email_id.is_(None)).distinct().all())
    for email in emails:
        latest = (session.query(Classification).filter_by(email_id=email.gmail_id)
                  .order_by(Classification.created_at.desc()).first())
        deadline = (latest.extracted or {}).get("deadline_utc")
        deadline_dt = datetime.fromisoformat(deadline) if deadline else None
        session.add(EvalLabel(email_id=email.gmail_id,
                              true_category=latest.category,
                              true_deadline=deadline_dt))
        added += 1
    session.flush()
    return (f"bootstrapped {added} labels from latest predictions — "
            "now correct wrong rows in eval_labels (label-by-correction)")


def run_eval(session, client) -> EvalReport:
    pairs, deadline_pairs = [], []
    for label in session.query(EvalLabel).all():
        row = session.get(Email, label.email_id)
        email = _to_email_in(row)
        gate = run_gates(email)
        if gate.route == "noise":
            predicted_cat, predicted_deadline = "noise", None
        else:
            cls, model = classify_email(email, gate, client)
            session.add(Classification(
                email_id=email.gmail_id, prompt_version=PROMPT_VERSION,
                model=model, category=cls.category, confidence=cls.confidence,
                extracted=cls.model_dump(mode="json")))
            predicted_cat, predicted_deadline = cls.category, cls.deadline_utc
        pairs.append((label.true_category, predicted_cat))
        deadline_pairs.append((label.true_deadline, predicted_deadline))
    session.flush()
    return score(pairs, deadline_pairs)


def instrumentation_summary(session) -> str:
    total_alerts = session.query(Alert).count()
    junk = session.query(Alert).filter_by(marked_junk=True).count()
    emails = session.query(Email).count()
    reviews = session.query(FailedJob).filter_by(stage="review_pending").count()
    by_closed = dict(session.query(Obligation.closed_by, sa.func.count())
                     .filter(Obligation.closed_by.isnot(None))
                     .group_by(Obligation.closed_by).all())
    junk_rate = f"{junk / total_alerts:.1%}" if total_alerts else "n/a"
    review_rate = f"{reviews} of {emails}" if emails else "n/a"
    return "\n".join([
        f"alerts sent: {total_alerts} (junk-marked: {junk}, rate {junk_rate})",
        f"review queue: {review_rate} emails needed adjudication",
        "manual data entries: 0 (by construction — no manual-entry path exists)",
        f"obligation closures by path: {by_closed}",
    ])
