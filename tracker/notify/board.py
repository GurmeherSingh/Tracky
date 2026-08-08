from datetime import datetime

from tracker.models import Application, Obligation, get_state, set_state
from tracker.notify.fmt import render_board
from tracker.notify.slack import SlackNotifier
from tracker.state.quiet import gone_quiet_applications


def _open_obs_with_company(session) -> list[tuple[Obligation, str]]:
    rows = (session.query(Obligation, Application.company_display)
            .join(Application, Obligation.application_id == Application.id)
            .filter(Obligation.status.in_(["open", "unconfirmed",
                                           "unconfirmed_possibly_missed"]))
            .all())
    return [(ob, company) for ob, company in rows]


def update_board(session, notifier: SlackNotifier, now: datetime, tz: str) -> None:
    text = render_board(_open_obs_with_company(session),
                        gone_quiet_applications(session, now), now, tz)
    ts = get_state(session, "board_ts")
    if ts:
        notifier.update_message(notifier.tracker_channel, ts, text)
    else:
        ts = notifier.post_tracker(text)
        notifier.web.pins_add(channel=notifier.tracker_channel, timestamp=ts)
        set_state(session, "board_ts", ts)
