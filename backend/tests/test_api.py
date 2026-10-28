import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from parti.config import Settings
from parti.models import AuditEvent, Grade, Note, PilotSignup, SessionSummary, User
from parti.seed import STUDENTS
from tests.conftest import make_client

VALID_SIGNUP = {"name": "Ana López", "school": "Business School", "email": "ana@university.edu"}


def dashboard(client: TestClient) -> dict:
    res = client.get("/api/dashboard")
    assert res.status_code == 200, res.text
    return res.json()


def student(data: dict, name: str) -> dict:
    return next(s for s in data["students"] if s["name"] == name)


def grade_url(data: dict, name: str) -> str:
    return f"/api/sessions/{data['session']['id']}/students/{student(data, name)['id']}/grade"


def test_index_serves_the_app_with_security_headers(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    assert "Participation grades without the guesswork" in res.text
    assert res.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in res.headers["Content-Security-Policy"]


def test_dashboard_matches_the_sample_data_spec(client: TestClient) -> None:
    data = dashboard(client)
    assert data["session"]["course"] == "Strategy & Society"
    assert data["session"]["number"] == 7
    assert data["session"]["date"] == "14 Oct 2026"

    students = data["students"]
    counts = sorted((len(s["contributions"]) for s in students), reverse=True)
    assert len(students) == 15
    assert sum(counts) == 41
    assert counts[:3] == [8, 8, 7]
    assert sum(counts[:3]) / sum(counts) > 0.5
    assert sum(1 for s in students if s["grade"] is not None) == 8

    quiet = {s["name"] for s in students if not s["contributions"]}
    assert quiet == {"Nadia Petrova", "Yusuf Demir", "Chloé Martin"}
    borderline = [s for s in students if s["borderline"]]
    assert len(borderline) == 3
    assert all(s["grade"] is None and 1 <= len(s["contributions"]) <= 2 for s in borderline)

    for s in students:
        for time, kind, depth, summary in s["contributions"]:
            assert "10:00" <= time <= "11:30"
            assert kind in {"I", "Q", "B"}
            assert 0 <= depth <= 100 and summary


def test_last_contribution_comes_from_earlier_sessions(client: TestClient) -> None:
    data = dashboard(client)
    assert student(data, "Amara Okafor")["lastContribution"] == "14 Oct 2026"
    assert student(data, "Nadia Petrova")["lastContribution"] == "30 Sep 2026"
    assert student(data, "Chloé Martin")["lastContribution"] == "7 Oct 2026"
    assert student(data, "Yusuf Demir")["lastContribution"] is None


def test_dashboard_never_sends_emails_and_summary_has_no_names(client: TestClient) -> None:
    res = client.get("/api/dashboard")
    assert "@" not in res.text
    summary = res.json()["summary"]
    text_ = " ".join(summary["points"] + summary["questions"] + [summary["reading"]])
    for s in STUDENTS:
        for part in s.name.split():
            assert part not in text_


def test_no_sign_in_configured_is_401(db: Session) -> None:
    res = make_client(email=None).get("/api/dashboard")
    assert res.status_code == 401
    assert "PARTI_DEMO_USER_EMAIL" in res.json()["detail"]


def test_unknown_demo_user_is_401(db: Session) -> None:
    assert make_client(email="nobody@example.edu").get("/api/dashboard").status_code == 401


def test_staff_of_other_courses_cannot_see_or_grade(db: Session, client: TestClient) -> None:
    data = dashboard(client)
    inst_id = db.scalar(select(User.institution_id).limit(1))
    db.add(
        User(
            institution_id=inst_id,
            sso_issuer="demo",
            sso_subject="x",
            email="x@example.edu",
            display_name="X",
        )
    )
    db.commit()
    outsider = make_client(email="x@example.edu")
    assert outsider.get("/api/dashboard").status_code == 404
    assert outsider.get(f"/api/sessions/{data['session']['id']}/dashboard").status_code == 404
    assert outsider.put(grade_url(data, "Aiko Tanaka"), json={"value": 7}).status_code == 404


def test_dashboard_by_session_id(client: TestClient) -> None:
    data = dashboard(client)
    res = client.get(f"/api/sessions/{data['session']['id']}/dashboard")
    assert res.status_code == 200
    assert client.get(f"/api/sessions/{uuid.uuid4()}/dashboard").status_code == 404


def test_saving_a_grade_persists_and_updates(client: TestClient) -> None:
    data = dashboard(client)
    url = grade_url(data, "Aiko Tanaka")
    assert client.put(url, json={"value": 7.5}).json() == {"value": 7.5}
    assert student(dashboard(client), "Aiko Tanaka")["grade"] == 7.5

    client.put(url, json={"value": 8})
    after = dashboard(client)
    assert student(after, "Aiko Tanaka")["grade"] == 8
    assert sum(1 for s in after["students"] if s["grade"] is not None) == 9


@pytest.mark.parametrize("value", [-0.5, 10.5, 7.3, "seven"])
def test_invalid_grades_are_rejected(client: TestClient, value: object) -> None:
    data = dashboard(client)
    assert client.put(grade_url(data, "Aiko Tanaka"), json={"value": value}).status_code == 422


def test_clearing_a_grade(client: TestClient) -> None:
    data = dashboard(client)
    url = grade_url(data, "Amara Okafor")
    assert client.delete(url).status_code == 204
    assert student(dashboard(client), "Amara Okafor")["grade"] is None
    assert client.delete(url).status_code == 204


def test_cannot_grade_someone_who_is_not_a_student(db: Session, client: TestClient) -> None:
    data = dashboard(client)
    professor_id = db.scalar(select(User.id).where(User.email == "professor@demo.example.edu"))
    url = f"/api/sessions/{data['session']['id']}/students/{professor_id}/grade"
    assert client.put(url, json={"value": 9}).status_code == 404


def test_database_rejects_grades_off_the_half_step_scale(db: Session) -> None:
    grade = db.scalars(select(Grade)).first()
    assert grade is not None
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE grades SET value = 7.3 WHERE id = :id"), {"id": grade.id})
    db.rollback()


def test_notes_are_saved_and_deleted(client: TestClient) -> None:
    data = dashboard(client)
    sid, aiko = data["session"]["id"], student(data, "Aiko Tanaka")["id"]
    res = client.post(f"/api/sessions/{sid}/students/{aiko}/notes", json={"body": "  Arrived late.  "})
    assert res.status_code == 201
    note = res.json()
    assert note["text"] == "Arrived late."
    assert dashboard(client)["notes"][aiko][0]["id"] == note["id"]

    assert client.delete(f"/api/notes/{note['id']}").status_code == 204
    assert aiko not in dashboard(client)["notes"]
    assert client.delete(f"/api/notes/{note['id']}").status_code == 404


def test_empty_note_is_rejected(client: TestClient) -> None:
    data = dashboard(client)
    url = f"/api/sessions/{data['session']['id']}/students/{student(data, 'Aiko Tanaka')['id']}/notes"
    assert client.post(url, json={"body": "   "}).status_code == 422


def test_only_the_author_can_delete_a_note(db: Session, client: TestClient) -> None:
    data = dashboard(client)
    other = User(
        institution_id=db.scalar(select(User.institution_id).limit(1)),
        sso_issuer="demo",
        sso_subject="ta",
        email="ta@example.edu",
        display_name="TA",
    )
    db.add(other)
    db.flush()
    note = Note(
        session_id=uuid.UUID(data["session"]["id"]),
        student_id=uuid.UUID(student(data, "Aiko Tanaka")["id"]),
        author_id=other.id,
        body="Someone else's note",
    )
    db.add(note)
    db.commit()
    assert client.delete(f"/api/notes/{note.id}").status_code == 404


def test_summary_review_toggle_persists(db: Session, client: TestClient) -> None:
    data = dashboard(client)
    url = f"/api/sessions/{data['session']['id']}/summary/review"
    assert data["summary"]["reviewed"] is False
    assert client.put(url, json={"reviewed": True}).status_code == 200
    assert dashboard(client)["summary"]["reviewed"] is True
    client.put(url, json={"reviewed": False})
    assert dashboard(client)["summary"]["reviewed"] is False


def test_review_without_summary_is_404(db: Session, client: TestClient) -> None:
    data = dashboard(client)
    db.execute(text("DELETE FROM session_summaries"))
    db.commit()
    assert dashboard(client)["summary"] is None
    url = f"/api/sessions/{data['session']['id']}/summary/review"
    assert client.put(url, json={"reviewed": True}).status_code == 404
    assert db.scalar(select(SessionSummary)) is None


def test_signup_is_saved_and_deduplicated_by_email(db: Session, client: TestClient) -> None:
    assert client.post("/api/signups", json=VALID_SIGNUP).status_code == 201
    again = {**VALID_SIGNUP, "school": "Law School", "email": "ANA@University.edu"}
    assert client.post("/api/signups", json=again).status_code == 201
    rows = db.scalars(select(PilotSignup)).all()
    assert len(rows) == 1
    assert rows[0].school == "Law School"


@pytest.mark.parametrize("field,value", [("email", "not-an-email"), ("name", "  "), ("school", "")])
def test_invalid_signup_is_rejected(client: TestClient, field: str, value: str) -> None:
    assert client.post("/api/signups", json={**VALID_SIGNUP, field: value}).status_code == 422


def test_signup_works_without_sign_in(db: Session) -> None:
    assert make_client(email=None).post("/api/signups", json=VALID_SIGNUP).status_code == 201


def test_changes_are_audited_without_content(db: Session, client: TestClient) -> None:
    data = dashboard(client)
    client.put(grade_url(data, "Aiko Tanaka"), json={"value": 6.5})
    client.post("/api/signups", json=VALID_SIGNUP)
    events = db.scalars(select(AuditEvent).order_by(AuditEvent.id)).all()
    assert [e.action for e in events] == ["dashboard.view", "grade.set", "signup.save"]
    dumped = " ".join(f"{e.details} {e.entity_id}" for e in events)
    assert "6.5" not in dumped and "ana@" not in dumped


def test_audit_log_is_append_only(db: Session, client: TestClient) -> None:
    dashboard(client)
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text("DELETE FROM audit_events"))
    db.rollback()


def test_demo_user_is_refused_in_production() -> None:
    with pytest.raises(ValueError, match="DEMO_USER"):
        Settings(
            _env_file=None,
            env="production",
            secret_key="a-real-secret-key-123",
            oidc_client_secret="real",
            base_url="https://parti.example.edu",
            demo_user_email="professor@demo.example.edu",
        )
