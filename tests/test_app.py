import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import create_app, get_db  # noqa: E402
from sample_data import DATA  # noqa: E402

PASSWORD = "test-password"
PROFESSOR_PASSWORD = "prof-password"
VALID = {"name": "Ana López", "school": "Business School", "email": "ana@university.edu"}


@pytest.fixture()
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "DATABASE": str(tmp_path / "test.db"),
        "ADMIN_PASSWORD": PASSWORD,
        "PROFESSOR_PASSWORD": PROFESSOR_PASSWORD,
    })


@pytest.fixture()
def client(app):
    return app.test_client()


def auth(user="admin", password=PASSWORD):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def count_signups(app):
    with app.app_context():
        return get_db().execute("SELECT COUNT(*) FROM signups").fetchone()[0]


# ---------------------------------------------------------------- page

def test_index_renders_sample_data(client):
    res = client.get("/")
    assert res.status_code == 200
    html = res.get_data(as_text=True)
    assert "const DATA = {" in html
    assert "Amara Okafor" in html
    assert "{{" not in html
    assert res.headers["X-Frame-Options"] == "DENY"


# ---------------------------------------------------------------- sign-ups

def test_valid_signup_is_saved(app, client):
    res = client.post("/api/signups", json=VALID)
    assert res.status_code == 201
    assert res.get_json() == {"ok": True}
    assert count_signups(app) == 1


def test_signing_up_again_updates_instead_of_duplicating(app, client):
    client.post("/api/signups", json=VALID)
    res = client.post("/api/signups", json={**VALID, "school": "Law School", "email": "ANA@University.edu"})
    assert res.status_code == 201
    assert count_signups(app) == 1
    with app.app_context():
        assert get_db().execute("SELECT school FROM signups").fetchone()[0] == "Law School"


def test_missing_fields_return_field_errors(app, client):
    res = client.post("/api/signups", json={"name": "  ", "email": ""})
    assert res.status_code == 400
    assert set(res.get_json()["errors"]) == {"name", "school", "email"}
    assert count_signups(app) == 0


@pytest.mark.parametrize("email", ["ana", "ana@", "ana@uni", "a na@uni.edu"])
def test_invalid_email_is_rejected(client, email):
    res = client.post("/api/signups", json={**VALID, "email": email})
    assert res.status_code == 400
    assert "valid email" in res.get_json()["errors"]["email"]


def test_overlong_values_are_rejected(client):
    res = client.post("/api/signups", json={**VALID, "name": "x" * 201})
    assert res.status_code == 400
    assert "name" in res.get_json()["errors"]


def test_non_json_body_is_rejected(client):
    res = client.post("/api/signups", data="name=Ana", content_type="application/x-www-form-urlencoded")
    assert res.status_code == 400


# ---------------------------------------------------------------- admin

def test_admin_requires_password(client):
    assert client.get("/admin").status_code == 401
    assert client.get("/admin", headers=auth(password="wrong")).status_code == 401
    assert client.get("/admin", headers=auth(user="someone")).status_code == 401


def test_admin_lists_signups(client):
    client.post("/api/signups", json=VALID)
    res = client.get("/admin", headers=auth())
    assert res.status_code == 200
    assert "ana@university.edu" in res.get_data(as_text=True)
    assert res.headers["Cache-Control"] == "no-store"


def test_admin_escapes_html(client):
    client.post("/api/signups", json={**VALID, "name": "<script>alert(1)</script>"})
    html = client.get("/admin", headers=auth()).get_data(as_text=True)
    assert "<script>alert(1)</script>" not in html


def test_admin_is_off_without_password(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.db"), "ADMIN_PASSWORD": ""})
    assert app.test_client().get("/admin", headers=auth()).status_code == 503


def test_csv_export_neutralises_formulas(client):
    client.post("/api/signups", json={**VALID, "name": "=HYPERLINK(\"x\")"})
    res = client.get("/admin/signups.csv", headers=auth())
    assert res.status_code == 200
    assert res.mimetype == "text/csv"
    body = res.get_data(as_text=True)
    assert body.splitlines()[0] == "Name,School,Email,Signed up,Last updated"
    assert "'=HYPERLINK" in body


# ---------------------------------------------------------------- professor sign-in

@pytest.fixture()
def professor(client):
    res = client.post("/login", data={"password": PROFESSOR_PASSWORD})
    assert res.status_code == 302
    return client


def saved_grades(app):
    with app.app_context():
        return {r[0]: r[1] for r in get_db().execute("SELECT student_id, grade FROM grades")}


def test_wrong_password_does_not_sign_in(client):
    res = client.post("/login", data={"password": "wrong"})
    assert res.status_code == 401
    assert "Try again" in res.get_data(as_text=True)
    assert client.put("/api/grades/s9", json={"grade": 7}).status_code == 401


def test_login_is_off_without_password(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.db"), "PROFESSOR_PASSWORD": ""})
    client = app.test_client()
    assert client.get("/login").status_code == 503
    assert "Sign in to save" not in client.get("/").get_data(as_text=True)


def test_demo_page_has_no_saved_state(client):
    html = client.get("/").get_data(as_text=True)
    assert "const SAVED = null;" in html
    assert "Sign in to save" in html
    assert client.get("/").headers.get("Cache-Control") != "no-store"


def test_saving_requires_sign_in(client):
    assert client.put("/api/grades/s9", json={"grade": 7}).status_code == 401
    assert client.delete("/api/grades/s1").status_code == 401
    assert client.post("/api/students/s1/notes", json={"text": "Hi"}).status_code == 401
    assert client.put("/api/summary/reviewed", json={"reviewed": True}).status_code == 401


def test_sample_grades_are_seeded_once(app, professor):
    assert len(saved_grades(app)) == 8
    professor.delete("/api/grades/s1")
    with app.app_context():
        from app import init_db
        init_db()
    assert "s1" not in saved_grades(app)


def test_grade_is_saved_updated_and_cleared(app, professor):
    assert professor.put("/api/grades/s9", json={"grade": 7.5}).get_json() == {"grade": 7.5}
    professor.put("/api/grades/s9", json={"grade": 8})
    assert saved_grades(app)["s9"] == 8
    assert professor.delete("/api/grades/s9").status_code == 204
    assert "s9" not in saved_grades(app)


@pytest.mark.parametrize("grade", [-1, 10.5, 7.25, "7", None, True])
def test_invalid_grades_are_rejected(app, professor, grade):
    res = professor.put("/api/grades/s9", json={"grade": grade})
    assert res.status_code == 400
    assert "grade" in res.get_json()["errors"]
    assert "s9" not in saved_grades(app)


def test_unknown_student_is_404(professor):
    assert professor.put("/api/grades/nobody", json={"grade": 7}).status_code == 404
    assert professor.post("/api/students/nobody/notes", json={"text": "Hi"}).status_code == 404


def test_writes_must_be_json(professor):
    res = professor.put("/api/grades/s9", data="grade=7", content_type="application/x-www-form-urlencoded")
    assert res.status_code == 400


def test_notes_are_added_and_deleted(app, professor):
    res = professor.post("/api/students/s3/notes", json={"text": "  Strong written follow-up  "})
    assert res.status_code == 201
    note = res.get_json()
    assert note["text"] == "Strong written follow-up"
    assert note["created"]
    assert professor.delete(f"/api/notes/{note['id']}").status_code == 204
    assert professor.delete(f"/api/notes/{note['id']}").status_code == 404


def test_empty_or_long_notes_are_rejected(professor):
    assert professor.post("/api/students/s3/notes", json={"text": "   "}).status_code == 400
    assert professor.post("/api/students/s3/notes", json={"text": "x" * 2001}).status_code == 400


def test_signed_in_page_carries_saved_state(professor):
    professor.put("/api/grades/s9", json={"grade": 6.5})
    professor.post("/api/students/s9/notes", json={"text": "<b>Helped a classmate</b>"})
    professor.put("/api/summary/reviewed", json={"reviewed": True})
    res = professor.get("/")
    html = res.get_data(as_text=True)
    assert res.headers["Cache-Control"] == "no-store"
    assert '"s9": 6.5' in html
    assert '"reviewed": true' in html
    assert "<b>Helped a classmate</b>" not in html  # escaped inside the JSON
    assert "Sign out" in html


def test_reviewed_must_be_boolean(professor):
    assert professor.put("/api/summary/reviewed", json={"reviewed": "yes"}).status_code == 400


def test_logout_ends_the_session(professor):
    professor.post("/logout")
    assert professor.put("/api/grades/s9", json={"grade": 7}).status_code == 401
    assert "const SAVED = null;" in professor.get("/").get_data(as_text=True)


# ---------------------------------------------------------------- sample data

def contributions_by_student():
    counts = {s["id"]: 0 for s in DATA["students"]}
    for c in DATA["contributions"]:
        counts[c["student"]] += 1
    return counts


def test_sample_data_matches_spec():
    counts = contributions_by_student()
    total = len(DATA["contributions"])
    top3 = sorted(counts.values(), reverse=True)[:3]
    assert total == 41
    assert top3 == [8, 8, 7]
    assert sum(top3) / total > 0.5  # balance warning triggers
    assert sum(1 for c in counts.values() if c == 0) == 3
    assert sum(1 for s in DATA["students"] if s["grade"] is not None) == 8

    borderline = [s for s in DATA["students"] if s["borderline"]]
    assert len(borderline) == 3
    assert all(1 <= counts[s["id"]] <= 2 and s["grade"] is None for s in borderline)
    assert all("10:00" <= c["time"] <= "11:30" for c in DATA["contributions"])


def test_class_summary_has_no_student_names():
    text = " ".join(DATA["classSummary"]["points"] + DATA["classSummary"]["questions"] + [DATA["classSummary"]["reading"]])
    for student in DATA["students"]:
        for part in student["name"].split():
            assert part not in text, part
