"""Parti prototype server.

Serves the single-page prototype (landing page + professor dashboard with
sample data) and stores pilot sign-ups in SQLite.

Run locally:
    flask --app app run --debug

Environment:
    PARTI_DATABASE        path to the SQLite file (default: instance/parti.db)
    PARTI_ADMIN_PASSWORD  password for /admin; the admin pages are off when unset
"""

import csv
import hmac
import io
import os
import re
import sqlite3
from datetime import datetime, timezone
from functools import wraps

from flask import Blueprint, Flask, Response, current_app, g, jsonify, render_template, request

from sample_data import DATA

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")
MAX_LENGTHS = {"name": 200, "school": 200, "email": 254}
REQUIRED_MESSAGES = {
    "name": "Please enter your name.",
    "school": "Please enter your school.",
    "email": "Please enter your email.",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS signups (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL,
    school     TEXT NOT NULL,
    email      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
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
    db.execute(SCHEMA)
    db.commit()


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
    return render_template("index.html", data=DATA)


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
        if request.path.startswith("/admin"):
            response.headers["Cache-Control"] = "no-store"
        return response

    return app


if __name__ == "__main__":
    create_app().run(debug=os.environ.get("FLASK_DEBUG") == "1")
