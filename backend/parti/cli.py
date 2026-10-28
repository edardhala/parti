import argparse
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from parti import notify
from parti.config import Settings, get_settings
from parti.db import make_session_factory
from parti.models import PilotSignup
from parti.seed import DemoDataExists, seed


def run_digest(db: Session, settings: Settings, hours: int, mailer: notify.Mailer) -> str:
    if not settings.notify_recipients:
        return "PARTI_NOTIFY_TO is unset, so there's no one to send the summary to."
    since = datetime.now(UTC) - timedelta(hours=hours)
    signups = db.scalars(
        select(PilotSignup).where(PilotSignup.updated_at >= since).order_by(PilotSignup.updated_at)
    ).all()
    message = notify.digest_message(settings, signups, since)
    if message is None:
        return f"No sign-ups in the last {hours} hours; nothing sent."
    mailer.send(message)
    if isinstance(mailer, notify.LogMailer):
        return f"PARTI_SMTP_HOST is unset, so the summary of {len(signups)} sign-up(s) was logged, not sent."
    return f"Sent a summary of {len(signups)} sign-up(s) to {message['To']}."


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="parti")
    commands = parser.add_subparsers(dest="command", required=True)
    seed_cmd = commands.add_parser("seed", help="Load the fictional demo course")
    seed_cmd.add_argument("--reset", action="store_true", help="Delete the existing demo data first")
    digest_cmd = commands.add_parser("digest", help="Email the team a summary of recent pilot sign-ups")
    digest_cmd.add_argument("--hours", type=int, default=24, help="How far back to look (default 24)")
    args = parser.parse_args(argv)

    settings = get_settings()
    factory = make_session_factory(settings.database_url)
    if args.command == "digest":
        with factory() as db:
            print(run_digest(db, settings, args.hours, notify.make_mailer(settings)))
        return 0

    with factory() as db:
        try:
            email = seed(db, reset=args.reset)
        except DemoDataExists as exc:
            print(exc, file=sys.stderr)
            return 1
    print(f"Demo data loaded. Run the app with PARTI_DEMO_USER_EMAIL={email}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
