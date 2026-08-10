from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import sqlalchemy as sa

from tracker.ingest.pipeline import record_failure
from tracker.log import get_logger
from tracker.models import Application, Event, FailedJob
from tracker.research.briefing import build_briefing
from tracker.sync.notion_sync import append_briefing

BRIEFING_STAGE = "briefing"
TRIGGER_KIND = "interview_invite"


def needs_briefing(session) -> list[tuple[int, str, str, str]]:
    """Applications owed a briefing, as plain tuples.

    Tuples rather than ORM rows on purpose: these cross into worker threads,
    and an Application instance stays bound to this session — touching one off
    the main thread can fire a lazy load on a session another thread is using.
    """
    parked = {job.email_id for job in session.query(FailedJob).filter_by(
        stage=BRIEFING_STAGE, parked=True)}
    rows = (session.query(Application.id, Application.notion_page_id,
                          Application.company_display, Application.role_title)
            .join(Event, Event.application_id == Application.id)
            .filter(Event.kind == TRIGGER_KIND,
                    Application.notion_page_id.isnot(None),
                    Application.briefed_at.is_(None),
                    Application.status_derived != "rejected")
            .distinct()
            .all())
    return [tuple(row) for row in rows if f"app:{row[0]}" not in parked]


def brief_one(session, client, notion, company: str, now: datetime,
              build=build_briefing, progress=None) -> dict:
    """Brief one company on demand, for the Slack agent.

    Unlike the scheduled pass this ignores the interview trigger and an earlier
    parking — a human asking by name is a stronger signal than either. It does
    refuse an already-briefed page, which is what keeps a retried answer from
    appending the briefing twice.
    """
    log = get_logger(component="briefing")
    pattern = f"%{company.lower()}%"
    apps = (session.query(Application)
            .filter(sa.or_(sa.func.lower(Application.company_display).like(pattern),
                           Application.company_canonical.like(pattern)))
            .all())
    if not apps:
        return {"ok": False, "error": f"no application matching '{company}'"}
    if len(apps) > 1:
        return {"ok": False, "matches": [a.company_display for a in apps],
                "error": f"'{company}' matches several applications — "
                         "ask which one"}

    app = apps[0]
    if app.notion_page_id is None:
        return {"ok": False,
                "error": f"{app.company_display} has no Notion page yet"}
    if app.briefed_at is not None:
        return {"ok": True, "already_briefed": True,
                "company": app.company_display,
                "notion_page_id": app.notion_page_id}

    if progress:
        progress(f"Looking into {app.company_display} — this takes a minute or two.")
    try:
        markdown = build(client, app.company_display, app.role_title)
        blocks = append_briefing(notion, app.notion_page_id, markdown)
    except Exception as e:
        job = record_failure(session, f"app:{app.id}", BRIEFING_STAGE, str(e))
        session.commit()
        log.warning("briefing_failed", application_id=app.id,
                    company=app.company_display, strikes=job.strikes, error=str(e))
        return {"ok": False, "company": app.company_display, "error": str(e)}

    app.briefed_at = now
    session.query(FailedJob).filter_by(email_id=f"app:{app.id}",
                                       stage=BRIEFING_STAGE).delete()
    session.commit()
    log.info("briefed", application_id=app.id, company=app.company_display,
             on_demand=True)
    return {"ok": True, "company": app.company_display, "blocks": blocks,
            "notion_page_id": app.notion_page_id}


def enrich_applications(session, client, notion, now: datetime,
                        max_workers: int = 4, build=build_briefing) -> dict[str, int]:
    """Research every company with a pending interview and append a briefing.

    Network work fans out across threads; every database read and write stays
    on the calling thread. `briefed_at` is stamped only on success, so a
    failure needs no recovery path — the next tick simply sees the null again.
    """
    log = get_logger(component="briefing")
    targets = needs_briefing(session)
    counts = {"briefed": 0, "failed": 0}
    if not targets:
        return counts

    def work(target):
        _, page_id, company, role = target
        append_briefing(notion, page_id, build(client, company, role))

    outcomes: list[tuple[tuple, str | None]] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(work, t): t for t in targets}
        for future in as_completed(futures):
            target = futures[future]
            try:
                future.result()
                outcomes.append((target, None))
            except Exception as e:  # one unresearchable company must not block the rest
                outcomes.append((target, str(e)))

    for (app_id, _, company, _role), error in outcomes:
        if error is None:
            session.get(Application, app_id).briefed_at = now
            counts["briefed"] += 1
            log.info("briefed", application_id=app_id, company=company)
        else:
            job = record_failure(session, f"app:{app_id}", BRIEFING_STAGE, error)
            counts["failed"] += 1
            log.warning("briefing_failed", application_id=app_id, company=company,
                        strikes=job.strikes, parked=job.parked, error=error)
    session.commit()
    return counts
