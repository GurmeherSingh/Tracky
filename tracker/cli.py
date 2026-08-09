import argparse

from tracker.config import get_settings
from tracker.db import make_engine, session_scope
from tracker.log import configure_logging


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(prog="tracker")
    sub = parser.add_subparsers(dest="command", required=True)
    p_backfill = sub.add_parser("backfill", help="validation run over real mail")
    p_backfill.add_argument("--after", required=True, help="YYYY/MM/DD")
    sub.add_parser("run", help="start scheduler + socket mode")
    sub.add_parser("resync", help="force date-ranged re-ingest")
    p_wipe = sub.add_parser("wipe", help="delete ALL data (post-validation cleanup)")
    p_wipe.add_argument("--yes", action="store_true", help="skip confirmation prompt")
    args = parser.parse_args()

    settings = get_settings()
    engine = make_engine(settings.database_url)

    if args.command == "backfill":
        import anthropic

        from tracker.ingest.gmail_client import build_gmail_client
        from tracker.ingest.pipeline import run_backfill
        gmail = build_gmail_client()
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        with session_scope(engine) as session:
            counts = run_backfill(session, gmail, client, args.after, settings.timezone)
        print(counts)
    elif args.command == "run":
        from tracker.jobs import run_forever
        run_forever(engine, settings)
    elif args.command == "resync":
        import anthropic

        from tracker.ingest.gmail_client import build_gmail_client
        from tracker.ingest.pipeline import run_backfill
        gmail = build_gmail_client()
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        with session_scope(engine) as session:
            counts = run_backfill(session, gmail, client, "2026/01/01", settings.timezone)
        print(counts)
    elif args.command == "wipe":
        from tracker.models import wipe_all_data
        if not args.yes:
            typed = input("This deletes ALL rows in every table. Type 'wipe' to confirm: ")
            if typed.strip().lower() != "wipe":
                print("aborted")
                return
        with session_scope(engine) as session:
            wipe_all_data(session)
        print("all data wiped")


if __name__ == "__main__":
    main()
