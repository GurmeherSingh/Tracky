import threading
from datetime import UTC, datetime, timedelta

import anthropic
from apscheduler.schedulers.blocking import BlockingScheduler
from slack_sdk import WebClient

from tracker.db import session_scope
from tracker.ingest.gmail_client import GmailAuthExpired, build_gmail_client
from tracker.ingest.pipeline import run_incremental
from tracker.log import get_logger
from tracker.models import get_state, set_state
from tracker.notify.app import run_socket_mode
from tracker.notify.board import update_board
from tracker.notify.digest import send_digest_if_due
from tracker.notify.review import pump_review_queue
from tracker.notify.slack import SlackNotifier
from tracker.obligations.sweep import run_sweep

AUTH_ALARM_COOLDOWN = timedelta(hours=6)


def _maybe_auth_alarm(session, notifier: SlackNotifier) -> None:
    last = get_state(session, "last_auth_alarm")
    now = datetime.now(UTC)
    if last and datetime.fromisoformat(last) > now - AUTH_ALARM_COOLDOWN:
        return
    notifier.send_auth_alarm()
    set_state(session, "last_auth_alarm", now.isoformat())


def run_forever(engine, settings) -> None:
    log = get_logger(component="jobs")
    notifier = SlackNotifier(WebClient(token=settings.slack_bot_token),
                             settings.slack_alerts_channel,
                             settings.slack_tracker_channel)
    llm = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    gmail = build_gmail_client()

    notion = None
    if settings.notion_token and settings.notion_applications_db_id:
        from notion_client import Client as NotionClient
        notion = NotionClient(auth=settings.notion_token)

    threading.Thread(target=run_socket_mode, args=(settings, engine),
                     daemon=True).start()

    def guarded(name, fn):
        def wrapper():
            try:
                fn()
            except GmailAuthExpired:
                log.error("gmail_auth_expired", job=name)
                with session_scope(engine) as session:
                    _maybe_auth_alarm(session, notifier)
            except Exception as e:  # scheduler must never die
                log.error("job_failed", job=name, error=str(e))
        return wrapper

    def ingest_job():
        with session_scope(engine) as session:
            counts = run_incremental(session, gmail, llm, settings.timezone)
            pump_review_queue(session, notifier)
            update_board(session, notifier, datetime.now(UTC), settings.timezone)
            if notion is not None:
                # reconcile swallows per-application errors; anything that
                # escaped here would roll back the ingested emails with it
                from tracker.sync.notion_sync import reconcile
                reconcile(session, notion, settings.notion_applications_db_id,
                          only_missing=True)
            log.info("ingest_done", **counts)

    def sweep_job():
        with session_scope(engine) as session:
            run_sweep(session, notifier, datetime.now(UTC), settings.timezone)

    def digest_job():
        with session_scope(engine) as session:
            send_digest_if_due(session, notifier, datetime.now(UTC), settings.timezone)

    def notion_job():
        if notion is None:
            return
        from tracker.sync.notion_sync import reconcile
        with session_scope(engine) as session:
            counts = reconcile(session, notion, settings.notion_applications_db_id)
            log.info("notion_reconciled", **counts)

    scheduler = BlockingScheduler(timezone="UTC")
    # 2 rather than 1: a catch-up run after downtime can outlast a 1-minute
    # tick, and APScheduler skips overlapping runs rather than queueing them
    scheduler.add_job(guarded("ingest", ingest_job), "interval", minutes=2)
    # 1-minute sweep: user-set reminders should land near their chosen minute;
    # the next_alert_at query is indexed, so the extra ticks cost nothing
    scheduler.add_job(guarded("sweep", sweep_job), "interval", minutes=1)
    scheduler.add_job(guarded("digest", digest_job), "interval", minutes=15)
    scheduler.add_job(guarded("notion", notion_job), "interval", minutes=5)
    log.info("scheduler_started")
    scheduler.start()
