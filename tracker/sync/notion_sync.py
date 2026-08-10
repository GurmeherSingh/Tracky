import time

from tracker.log import get_logger
from tracker.models import Application, Obligation

RATE_LIMIT_SLEEP = 0.35


def application_properties(app: Application, open_ob_count: int) -> dict:
    return {
        "Name": {"title": [{"text": {
            "content": f"{app.company_display} — {app.role_title}".strip(" —")}}]},
        "Status": {"select": {"name": app.status_derived}},
        "Company": {"rich_text": [{"text": {"content": app.company_display}}]},
        "Role": {"rich_text": [{"text": {"content": app.role_title}}]},
        "Source": {"select": {"name": app.source}},
        "Last contact": {"date": {"start": app.last_contact_at.isoformat()}},
        "Open obligations": {"number": open_ob_count},
    }


def reconcile(session, notion, database_id: str, sleep=time.sleep,
              only_missing: bool = False) -> dict[str, int]:
    log = get_logger(component="notion_sync")
    counts = {"created": 0, "updated": 0, "skipped": 0}
    query = session.query(Application)
    if only_missing:
        query = query.filter(Application.notion_page_id.is_(None))
    for app in query.all():
        open_count = session.query(Obligation).filter(
            Obligation.application_id == app.id,
            Obligation.status.in_(["open", "unconfirmed"])).count()
        props = application_properties(app, open_count)
        try:
            if app.notion_page_id is None:
                resp = notion.pages.create(
                    parent={"database_id": database_id}, properties=props)
                app.notion_page_id = resp["id"]
                counts["created"] += 1
            else:
                notion.pages.update(page_id=app.notion_page_id, properties=props)
                counts["updated"] += 1
        except Exception as e:  # projection failure must never propagate (P9)
            counts["skipped"] += 1
            log.warning("notion_sync_skipped", application_id=app.id, error=str(e))
        sleep(RATE_LIMIT_SLEEP)
    return counts
