from datetime import UTC, datetime

from tracker.models import Application, Email, Obligation, insert_email_idempotent

NOW = datetime(2026, 8, 6, 12, 0, tzinfo=UTC)


def _email_cols(gmail_id="m1"):
    return dict(
        gmail_id=gmail_id, thread_id="t1", from_addr="no-reply@greenhouse.io",
        subject="Your application", body_text="Thanks for applying",
        received_at=NOW, raw={"headers": {}},
    )


def test_email_insert_idempotent(session):
    assert insert_email_idempotent(session, **_email_cols()) is True
    assert insert_email_idempotent(session, **_email_cols()) is False
    assert session.query(Email).count() == 1


def test_obligation_lifecycle_columns(session):
    app = Application(company_canonical="hrt", company_display="Hudson River Trading",
                      role_title="SWE Intern", source="applied",
                      first_seen_at=NOW, last_contact_at=NOW, status_derived="applied")
    session.add(app)
    session.flush()
    ob = Obligation(application_id=app.id, type="assessment", title="OA",
                    due_at=NOW, due_confidence="stated", status="open",
                    effort_minutes=120, next_alert_at=NOW, alert_tier="detection")
    session.add(ob)
    session.flush()
    assert ob.id is not None and ob.closed_at is None
