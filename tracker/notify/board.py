from datetime import datetime

from slack_sdk.errors import SlackApiError

from tracker.models import Application, Obligation, get_state, set_state
from tracker.notify.fmt import render_board
from tracker.notify.slack import SlackNotifier
from tracker.state.quiet import gone_quiet_applications

BOARD_MARKER = "Obligation Board*"


def _open_obs_with_company(session) -> list[tuple[Obligation, str, str | None]]:
    rows = (session.query(Obligation, Application.company_display,
                          Application.notion_page_id)
            .join(Application, Obligation.application_id == Application.id)
            # the board is what you can still act on; a passed deadline is
            # bookkeeping and belongs to the daily digest instead
            .filter(Obligation.status.in_(["open", "unconfirmed"]))
            .all())
    return [(ob, company, page_id) for ob, company, page_id in rows]


def _is_board(message: dict) -> bool:
    """Slack stores the header's emoji as `:clipboard:`, so a board read back
    from history is never byte-identical to the text it was posted with. Match
    the run of header Slack does not rewrite."""
    first_line = (message.get("text") or "").split("\n", 1)[0]
    return bool(message.get("bot_id")) and first_line.endswith(BOARD_MARKER)


def _adopt_or_post(session, notifier: SlackNotifier, text: str) -> None:
    """The board pointer can be lost (wipe, restored backup). Rather than
    littering the channel with a new pinned board, adopt the bot's most recent
    board message from channel history and unpin any older strays."""
    resp = notifier.web.conversations_history(channel=notifier.tracker_channel,
                                              limit=50)
    boards = [m for m in resp.get("messages", []) if _is_board(m)]
    if boards:
        newest, strays = boards[0], boards[1:]  # history is newest-first
        notifier.update_message(notifier.tracker_channel, newest["ts"], text)
        if not newest.get("pinned_to"):
            try:
                notifier.web.pins_add(channel=notifier.tracker_channel,
                                      timestamp=newest["ts"])
            except SlackApiError:
                pass
        for m in strays:
            if m.get("pinned_to"):
                try:
                    notifier.web.pins_remove(channel=notifier.tracker_channel,
                                             timestamp=m["ts"])
                except SlackApiError:
                    pass
        set_state(session, "board_ts", newest["ts"])
        return
    ts = notifier.post_tracker(text)
    notifier.web.pins_add(channel=notifier.tracker_channel, timestamp=ts)
    set_state(session, "board_ts", ts)


def update_board(session, notifier: SlackNotifier, now: datetime, tz: str) -> None:
    text = render_board(_open_obs_with_company(session),
                        gone_quiet_applications(session, now), now, tz)
    ts = get_state(session, "board_ts")
    if ts:
        try:
            notifier.update_message(notifier.tracker_channel, ts, text)
            return
        except SlackApiError:
            pass  # pointer is stale — fall through to adoption
    _adopt_or_post(session, notifier, text)
