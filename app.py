"""Parti prototype server.

Serves the single-page prototype (landing page + professor dashboard with
sample data), stores pilot sign-ups in SQLite and, once the professor signs
in, saves dashboard grades, notes and the summary review in SQLite too.

Run locally:
    flask --app app run --debug

Environment:
    PARTI_DATABASE            path to the SQLite file (default: instance/parti.db)
    PARTI_ADMIN_PASSWORD      password for /admin; the admin pages are off when unset
    PARTI_PROFESSOR_PASSWORD  password for the professor sign-in; without it the
                              dashboard is a demo that resets on reload
    PARTI_SECRET_KEY          signs the session cookie (default: random per start,
                              which signs everyone out on restart)
"""

import csv
import hmac
import io
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Blueprint, Flask, Response, abort, current_app, g, jsonify, redirect, render_template, request, session, url_for,
)

from sample_data import DATA

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
MAX_LENGTHS = {"name": 200, "school": 200, "email": 254}
REQUIRED_MESSAGES = {
    "name": "Please enter your name.",
    "school": "Please enter your school.",
    "email": "Please enter your email.",
}

NOTE_MAX_LENGTH = 2000
STUDENT_IDS = {s["id"] for s in DATA["students"]}

SCHEMA = """
CREATE TABLE IF NOT EXISTS signups (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    school     TEXT NOT NULL,
    email      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS grades (
    student_id TEXT PRIMARY KEY,
    grade      REAL NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    student_id TEXT NOT NULL,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

bp = Blueprint("parti", __name__)


# ---------------------------------------------------------------- database

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    # Start from the sample data's pre-saved grades, once. After that the
    # professor's changes (including cleared grades) are what counts.
    seeded = db.execute("SELECT 1 FROM settings WHERE key = 'grades_seeded'").fetchone()
    if not seeded:
        stamp = now_iso()
        db.executemany(
            "INSERT OR IGNORE INTO grades (student_id, grade, updated_at) VALUES (?, ?, ?)",
            [(s["id"], s["grade"], stamp) for s in DATA["students"] if s["grade"] is not None],
        )
        db.execute("INSERT INTO settings (key, value) VALUES ('grades_seeded', '1')")
    db.commit()


def saved_dashboard():
    """Everything the professor has saved, in the shape the page's script uses."""
    db = get_db()
    notes = {}
    for row in db.execute("SELECT id, student_id, text, created_at FROM notes ORDER BY created_at DESC, id DESC"):
        notes.setdefault(row["student_id"], []).append(note_json(row))
    reviewed = db.execute("SELECT value FROM settings WHERE key = 'summary_reviewed'").fetchone()
    return {
        "grades": {row["student_id"]: row["grade"] for row in db.execute("SELECT student_id, grade FROM grades")},
        "notes": notes,
        "reviewed": bool(reviewed and reviewed["value"] == "1"),
    }


def note_json(row):
    return {"id": row["id"], "text": row["text"], "created": row["created_at"]}


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- validation

def validate_signup(payload):
    """Return (clean, errors). Mirrors the rules in the page's form script."""
    clean, errors = {}, {}
    for field, required_msg in REQUIRED_MESSAGES.items():
        value = payload.get(field)
        value = value.strip() if isinstance(value, str) else ""
        if not value:
            errors[field] = required_msg
        elif len(value) > MAX_LENGTHS[field]:
            errors[field] = f"Keep this under {MAX_LENGTHS[field]} characters."
        elif field == "email" and not EMAIL_RE.match(value):
            errors[field] = "Enter a valid email address, like name@university.edu."
        clean[field] = value
    return clean, errors


def validate_grade(value):
    """Return (grade, error). Mirrors the rules in the page's grade script."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, "Enter a grade between 0 and 10."
    if not 0 <= value <= 10:
        return None, "The grade must be between 0 and 10."
    if value * 2 != int(value * 2):
        return None, "Use steps of 0.5, for example 7 or 7.5."
    return float(value), None


def json_body():
    # Requiring a JSON body also blocks cross-site form posts: browsers only
    # send application/json cross-origin after a CORS preflight we never allow.
    payload = request.get_json(silent=True) if request.is_json else None
    if not isinstance(payload, dict):
        abort(Response('{"error": "Send the request as a JSON object."}', 400, mimetype="application/json"))
    return payload


def known_student(student_id):
    if student_id not in STUDENT_IDS:
        abort(404)


# ---------------------------------------------------------------- professor auth

def professor_enabled():
    return bool(current_app.config["PROFESSOR_PASSWORD"])


def is_professor():
    return professor_enabled() and session.get("professor") is True


def require_professor(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_professor():
            return jsonify(error="Sign in as the professor to save changes."), 401
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------- admin auth

def require_admin(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        password = current_app.config["ADMIN_PASSWORD"]
        if not password:
            return Response("Admin is turned off. Set PARTI_ADMIN_PASSWORD to turn it on.", 503)
        auth = request.authorization
        given = (auth.password or "") if auth else ""
        if not auth or auth.username != "admin" or not hmac.compare_digest(given.encode(), password.encode()):
            return Response(
                "Sign in with the username admin and the admin password.",
                401,
                {"WWW-Authenticate": 'Basic realm="Parti admin", charset="UTF-8"'},
            )
        return view(*args, **kwargs)
    return wrapped


def all_signups():
    return get_db().execute(
        "SELECT name, school, email, created_at, updated_at FROM signups ORDER BY created_at DESC, id DESC"
    ).fetchall()


def csv_safe(value):
    # Stop spreadsheet apps from running a cell as a formula
    value = "" if value is None else str(value)
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


# ---------------------------------------------------------------- routes

@bp.get("/")
def index():
    professor = is_professor()
    response = current_app.make_response(render_template(
        "index.html",
        data=DATA,
        saved=saved_dashboard() if professor else None,
        professor_enabled=professor_enabled(),
    ))
    if professor:
        # The page carries saved grades and notes
        response.headers["Cache-Control"] = "no-store"
    return response


@bp.route("/login", methods=["GET", "POST"])
def login():
    if not professor_enabled():
        return Response("Professor sign-in is turned off. Set PARTI_PROFESSOR_PASSWORD to turn it on.", 503)
    error = None
    if request.method == "POST":
        given = request.form.get("password", "")
        if hmac.compare_digest(given.encode(), current_app.config["PROFESSOR_PASSWORD"].encode()):
            session.clear()
            session["professor"] = True
            session.permanent = True
            return redirect(url_for("parti.index", _anchor="dashboard"))
        error = "That password isn't right. Try again."
    return render_template("login.html", error=error), (401 if error else 200)


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("parti.index"))


@bp.put("/api/grades/<student_id>")
@require_professor
def save_grade(student_id):
    known_student(student_id)
    grade, error = validate_grade(json_body().get("grade"))
    if error:
        return jsonify(errors={"grade": error}), 400
    db = get_db()
    db.execute(
        """
        INSERT INTO grades (student_id, grade, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(student_id) DO UPDATE SET grade = excluded.grade, updated_at = excluded.updated_at
        """,
        (student_id, grade, now_iso()),
    )
    db.commit()
    return jsonify(grade=grade)


@bp.delete("/api/grades/<student_id>")
@require_professor
def clear_grade(student_id):
    known_student(student_id)
    db = get_db()
    db.execute("DELETE FROM grades WHERE student_id = ?", (student_id,))
    db.commit()
    return "", 204


@bp.post("/api/students/<student_id>/notes")
@require_professor
def add_note(student_id):
    known_student(student_id)
    text = json_body().get("text")
    text = text.strip() if isinstance(text, str) else ""
    if not text:
        return jsonify(errors={"text": "Write a note or pick a quick chip first."}), 400
    if len(text) > NOTE_MAX_LENGTH:
        return jsonify(errors={"text": f"Keep notes under {NOTE_MAX_LENGTH} characters."}), 400
    db = get_db()
    cur = db.execute(
        "INSERT INTO notes (student_id, text, created_at) VALUES (?, ?, ?)", (student_id, text, now_iso())
    )
    db.commit()
    row = db.execute("SELECT id, text, created_at FROM notes WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(note_json(row)), 201


@bp.delete("/api/notes/<int:note_id>")
@require_professor
def delete_note(note_id):
    db = get_db()
    if db.execute("DELETE FROM notes WHERE id = ?", (note_id,)).rowcount == 0:
        abort(404)
    db.commit()
    return "", 204


@bp.put("/api/summary/reviewed")
@require_professor
def set_reviewed():
    reviewed = json_body().get("reviewed")
    if not isinstance(reviewed, bool):
        return jsonify(errors={"reviewed": "Send true or false."}), 400
    db = get_db()
    db.execute(
        "INSERT INTO settings (key, value) VALUES ('summary_reviewed', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        ("1" if reviewed else "0",),
    )
    db.commit()
    return jsonify(reviewed=reviewed)


@bp.post("/api/signups")
def create_signup():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="Send the sign-up as a JSON object."), 400
    clean, errors = validate_signup(payload)
    if errors:
        return jsonify(errors=errors), 400
    stamp = now_iso()
    db = get_db()
    # One row per email: signing up again updates the name and school
    db.execute(
        """
        INSERT INTO signups (name, school, email, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(email) DO UPDATE SET
            name = excluded.name, school = excluded.school, updated_at = excluded.updated_at
        """,
        (clean["name"], clean["school"], clean["email"], stamp, stamp),
    )
    db.commit()
    return jsonify(ok=True), 201


@bp.get("/admin")
@require_admin
def admin():
    return render_template("admin.html", signups=all_signups())


@bp.get("/admin/signups.csv")
@require_admin
def export_signups():
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Name", "School", "Email", "Signed up", "Last updated"])
    for row in all_signups():
        writer.writerow([csv_safe(row[k]) for k in ("name", "school", "email", "created_at", "updated_at")])
    return Response(
        out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=parti-pilot-signups.csv"},
    )


# ---------------------------------------------------------------- app factory

def create_app(config=None):
    app = Flask(__name__)
    app.config.update(
        DATABASE=os.environ.get("PARTI_DATABASE", os.path.join(app.instance_path, "parti.db")),
        ADMIN_PASSWORD=os.environ.get("PARTI_ADMIN_PASSWORD", ""),
        PROFESSOR_PASSWORD=os.environ.get("PARTI_PROFESSOR_PASSWORD", ""),
        SECRET_KEY=os.environ.get("PARTI_SECRET_KEY") or secrets.token_hex(32),
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=8 * 60 * 60,
        MAX_CONTENT_LENGTH=16 * 1024,
    )
    if config:
        app.config.update(config)

    os.makedirs(os.path.dirname(os.path.abspath(app.config["DATABASE"])), exist_ok=True)
    app.teardown_appcontext(close_db)
    app.register_blueprint(bp)
    with app.app_context():
        init_db()

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path.startswith(("/admin", "/api/", "/login")):
            response.headers["Cache-Control"] = "no-store"
        return response

    return app


if __name__ == "__main__":
    create_app().run(debug=os.environ.get("FLASK_DEBUG") == "1")
