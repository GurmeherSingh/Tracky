from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tracker.models import (Application, Event, FailedJob, Obligation,
                            get_state, set_state)
from tracker.notify.fmt import DigestData, _countdown, _fmt_local, render_digest
from tracker.notify.slack import SlackNotifier

DIGEST_HOUR_LOCAL = 8


def build_digest_data(session, now: datetime, tz: str) -> DigestData:
    local = now.astimezone(ZoneInfo(tz))
    obs = (session.query(Obligation, Application.company_display)
           .join(Application, Obligation.application_id == Application.id)
           .filter(Obligation.status.in_(["open", "unconfirmed"]))
           .order_by(Obligation.due_at).all())
    open_obs = [(ob.title.split(" — ")[0], c,
                 f"due {_fmt_local(ob.due_at, tz)} ({_countdown(ob.due_at, now)})")
                for ob, c in obs]
    movement = [f"{c} → {e.kind}" for e, c in
                session.query(Event, Application.company_display)
                .join(Application, Event.application_id == Application.id)
                .filter(Event.occurred_at >= now - timedelta(hours=24)).all()]
    today = local.date()
    today_interviews = [f"{c}: {ob.title}" for ob, c in obs
                        if ob.type == "scheduling_reply"
                        and ob.due_at.astimezone(ZoneInfo(tz)).date() == today]
    unconfirmed = [f"{c}: {ob.title}" for ob, c in
                   session.query(Obligation, Application.company_display)
                   .join(Application, Obligation.application_id == Application.id)
                   .filter(Obligation.status.in_(
                       ["unconfirmed", "unconfirmed_possibly_missed"])).all()]
    review_count = session.query(FailedJob).filter_by(
        stage="review_pending", parked=False).count()
    processing = session.query(FailedJob).filter(
        FailedJob.stage != "review_pending").all()
    monday_trend = None
    if local.weekday() == 0:
        apps = session.query(Application).filter_by(source="applied").all()
        responded_ids = {e.application_id for e in session.query(Event)
                         .filter(Event.kind != "confirmation").all()}
        responded = sum(1 for a in apps if a.id in responded_ids)
        pct = round(100 * responded / len(apps)) if apps else 0
        monday_trend = (f"Response rate: {responded} of {len(apps)} applications "
                        f"have any non-confirmation event ({pct}%)")
    return DigestData(open_obs=open_obs, movement=movement,
                      today_interviews=today_interviews, unconfirmed=unconfirmed,
                      review_count=review_count, monday_trend=monday_trend,
                      failed_count=len(processing),
                      parked_count=sum(1 for j in processing if j.parked))


def send_digest_if_due(session, notifier: SlackNotifier, now: datetime,
                       tz: str) -> bool:
    local = now.astimezone(ZoneInfo(tz))
    if local.hour != DIGEST_HOUR_LOCAL:
        return False
    today_key = local.date().isoformat()
    if get_state(session, "last_digest_date") == today_key:
        return False
    notifier.post_tracker(render_digest(build_digest_data(session, now, tz), now, tz))
    set_state(session, "last_digest_date", today_key)
    return True
