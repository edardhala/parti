import logging
import re
import smtplib
import ssl
from collections.abc import Sequence
from datetime import datetime
from email.message import EmailMessage
from typing import Protocol
from zoneinfo import ZoneInfo

from parti.config import Settings
from parti.models import PilotSignup

log = logging.getLogger("parti.notify")

_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


class Mailer(Protocol):
    def send(self, message: EmailMessage) -> None: ...


class SmtpMailer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def send(self, message: EmailMessage) -> None:
        s = self.settings
        assert s.smtp_host
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
            if s.smtp_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if s.smtp_username:
                smtp.login(s.smtp_username, s.smtp_password or "")
            smtp.send_message(message)


class LogMailer:
    def send(self, message: EmailMessage) -> None:
        log.warning("Email not sent (PARTI_SMTP_HOST is unset): %r to %s", message["Subject"], message["To"])


def make_mailer(settings: Settings) -> Mailer:
    return SmtpMailer(settings) if settings.smtp_host else LogMailer()


def send_safely(mailer: Mailer, message: EmailMessage) -> None:
    try:
        mailer.send(message)
    except Exception:
        log.exception("Couldn't send %r", message["Subject"])


def _clean(value: str, limit: int = 200) -> str:
    return _CONTROL.sub(" ", value).strip()[:limit]


def _message(settings: Settings, subject: str, body: str, reply_to: str | None = None) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = _clean(subject)
    message["From"] = settings.notify_from
    message["To"] = ", ".join(settings.notify_recipients)
    if reply_to:
        message["Reply-To"] = _clean(reply_to)
    message.set_content(body)
    return message


def signup_message(settings: Settings, signup: PilotSignup, *, updated: bool) -> EmailMessage | None:
    if not settings.notify_each_signup or not settings.notify_recipients:
        return None
    what = "Updated pilot sign-up" if updated else "New pilot sign-up"
    body = (
        f"{what}\n\n"
        f"Name:   {_clean(signup.name)}\n"
        f"School: {_clean(signup.school)}\n"
        f"Email:  {_clean(signup.email)}\n\n"
        "Reply to this email to contact them directly.\n"
        + ("They had signed up before with this email; the entry now has these details.\n" if updated else "")
    )
    return _message(settings, f"{what}: {_clean(signup.school, 80)}", body, reply_to=signup.email)


def digest_message(
    settings: Settings, signups: Sequence[PilotSignup], since: datetime
) -> EmailMessage | None:
    if not signups or not settings.notify_recipients:
        return None
    tz = ZoneInfo(settings.display_timezone)
    lines = [
        f"- {_clean(s.name)}, {_clean(s.school)} <{_clean(s.email)}> "
        f"({s.updated_at.astimezone(tz):%d %b %H:%M})"
        for s in signups
    ]
    count = f"{len(signups)} pilot sign-up{'s' if len(signups) != 1 else ''}"
    body = f"{count} since {since.astimezone(tz):%d %b %Y %H:%M}:\n\n" + "\n".join(lines) + "\n"
    return _message(settings, f"Parti: {count} in the last day", body)
