import logging
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session

from parti import notify
from parti.cli import run_digest
from parti.config import Settings
from parti.models import PilotSignup
from tests.conftest import make_client

TEAM = "team@example.edu, lead@example.edu"
SIGNUP = {"name": "Ana López", "school": "Business School", "email": "ana@university.edu"}


class FakeMailer:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[EmailMessage] = []
        self.fail = fail

    def send(self, message: EmailMessage) -> None:
        if self.fail:
            raise ConnectionRefusedError("mail server down")
        self.sent.append(message)


def client_with_mailer(mailer: FakeMailer, **settings: Any) -> TestClient:
    client = make_client(**{"notify_to": TEAM, **settings})
    client.app.state.mailer = mailer  # type: ignore[attr-defined]
    return client


def test_each_signup_emails_the_team_only(db: Session) -> None:
    mailer = FakeMailer()
    assert client_with_mailer(mailer).post("/api/signups", json=SIGNUP).status_code == 201
    [message] = mailer.sent
    assert message["To"] == "team@example.edu, lead@example.edu"
    assert "ana@university.edu" not in message["To"]
    assert message["Reply-To"] == "ana@university.edu"
    assert message["Subject"] == "New pilot sign-up: Business School"
    body = message.get_content()
    assert "Ana López" in body and "Business School" in body and "ana@university.edu" in body


def test_signing_up_again_says_updated(db: Session) -> None:
    mailer = FakeMailer()
    client = client_with_mailer(mailer)
    client.post("/api/signups", json=SIGNUP)
    client.post("/api/signups", json={**SIGNUP, "school": "Law School"})
    assert mailer.sent[1]["Subject"] == "Updated pilot sign-up: Law School"
    assert "signed up before" in mailer.sent[1].get_content()


@pytest.mark.parametrize("settings", [{"notify_to": ""}, {"notify_each_signup": False}])
def test_no_email_when_turned_off(db: Session, settings: dict[str, Any]) -> None:
    mailer = FakeMailer()
    assert client_with_mailer(mailer, **settings).post("/api/signups", json=SIGNUP).status_code == 201
    assert mailer.sent == []


def test_mail_failure_does_not_break_the_signup(db: Session, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="parti.notify"):
        res = client_with_mailer(FakeMailer(fail=True)).post("/api/signups", json=SIGNUP)
    assert res.status_code == 201
    assert db.query(PilotSignup).count() == 1
    assert "Couldn't send" in caplog.text


def test_line_breaks_cannot_inject_headers(db: Session) -> None:
    mailer = FakeMailer()
    evil = {**SIGNUP, "school": "Evil\r\nBcc: victim@example.com"}
    client_with_mailer(mailer).post("/api/signups", json=evil)
    [message] = mailer.sent
    assert message["Bcc"] is None
    assert message["Subject"] == "New pilot sign-up: Evil Bcc: victim@example.com"


def _settings(**kw: Any) -> Settings:
    return Settings(_env_file=None, env="test", **{"notify_to": TEAM, **kw})


def test_digest_lists_recent_signups_only(db: Session) -> None:
    db.add_all(
        [
            PilotSignup(name="Recent One", school="A", email="one@example.edu"),
            PilotSignup(name="Old One", school="B", email="old@example.edu"),
        ]
    )
    db.commit()
    db.execute(
        update(PilotSignup)
        .where(PilotSignup.email == "old@example.edu")
        .values(updated_at=datetime.now(UTC) - timedelta(days=3))
    )
    db.commit()

    mailer = FakeMailer()
    result = run_digest(db, _settings(), 24, mailer)
    assert result.startswith("Sent a summary of 1 sign-up")
    [message] = mailer.sent
    assert message["Subject"] == "Parti: 1 pilot sign-up in the last day"
    body = message.get_content()
    assert "Recent One" in body and "Old One" not in body


def test_digest_sends_nothing_when_quiet_or_unconfigured(db: Session) -> None:
    mailer = FakeMailer()
    assert "nothing sent" in run_digest(db, _settings(), 24, mailer)
    db.add(PilotSignup(name="X", school="Y", email="x@example.edu"))
    db.commit()
    assert "PARTI_NOTIFY_TO is unset" in run_digest(db, _settings(notify_to=""), 24, mailer)
    assert mailer.sent == []
    assert "was logged, not sent" in run_digest(db, _settings(), 24, notify.LogMailer())


def test_without_smtp_host_messages_are_logged_without_content(caplog: pytest.LogCaptureFixture) -> None:
    mailer = notify.make_mailer(_settings())
    assert isinstance(mailer, notify.LogMailer)
    signup = PilotSignup(name="Ana López", school="Business School", email="ana@university.edu")
    message = notify.signup_message(_settings(), signup, updated=False)
    assert message is not None
    with caplog.at_level(logging.WARNING, logger="parti.notify"):
        mailer.send(message)
    assert "Email not sent" in caplog.text
    assert "Ana" not in caplog.text and "ana@university.edu" not in caplog.text


def test_smtp_mailer_uses_starttls_and_login(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, Any]] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: int) -> None:
            calls.append(("connect", (host, port)))

        def __enter__(self) -> "FakeSMTP":
            return self

        def __exit__(self, *exc: object) -> None:
            calls.append(("quit", None))

        def starttls(self, context: object) -> None:
            calls.append(("starttls", None))

        def login(self, user: str, password: str) -> None:
            calls.append(("login", user))

        def send_message(self, message: EmailMessage) -> None:
            calls.append(("send", message["To"]))

    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    settings = _settings(smtp_host="smtp.example.edu", smtp_username="parti", smtp_password="pw")
    mailer = notify.make_mailer(settings)
    assert isinstance(mailer, notify.SmtpMailer)
    message = notify.digest_message(
        settings,
        [PilotSignup(name="A", school="B", email="a@example.edu", updated_at=datetime.now(UTC))],
        datetime.now(UTC),
    )
    assert message is not None
    mailer.send(message)
    assert [c[0] for c in calls] == ["connect", "starttls", "login", "send", "quit"]
    assert calls[0][1] == ("smtp.example.edu", 587)
