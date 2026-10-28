# Parti backend

FastAPI + PostgreSQL. Serves the Parti page (landing page and professor
dashboard) and stores grades, notes, the class-summary review and pilot
sign-ups in the database. All course data is fictional demo data.

## Run it locally

Needs Python 3.12 and a local PostgreSQL.

```sh
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# One-time database setup
createuser -h 127.0.0.1 parti
createdb -h 127.0.0.1 -O parti parti
createdb -h 127.0.0.1 -O parti parti_test   # for the tests

alembic upgrade head              # create the tables
python -m parti.cli seed          # load the demo course (add --reset to start over)

PARTI_DEMO_USER_EMAIL=professor@demo.example.edu \
  uvicorn parti.app:create_app --factory --port 8000
```

Open http://127.0.0.1:8000. API docs are at http://127.0.0.1:8000/api/docs.

Sign-in isn't built yet. `PARTI_DEMO_USER_EMAIL` makes every request act as
the demo professor; the app refuses to start with it when `PARTI_ENV=production`.

Opening `parti/static/index.html` directly as a file still works. It falls
back to built-in sample data and keeps changes in memory only.

## What's stored

| Table | What |
| --- | --- |
| `contributions` | One-line summary, tag and depth per spoken contribution. No audio or transcripts. |
| `grades` | Set only by the professor, 0–10 in steps of 0.5 (enforced by the database) |
| `notes` | Professor notes per student and session |
| `student_flags` | Professor markers such as "borderline". Not a grade. |
| `session_summaries` | Anonymized class summary and its review status |
| `pilot_signups` | Name, school, email from the landing page (one row per email) |
| `audit_events` | Who did what, by id only. Append-only: a trigger rejects UPDATE and DELETE. |

## API

| Method and path | What it does |
| --- | --- |
| `GET /api/dashboard` | Latest session the user teaches: students, contributions, grades, notes, summary |
| `GET /api/sessions/{id}/dashboard` | The same for one session |
| `PUT /api/sessions/{id}/students/{student}/grade` | Save or update a grade, `{"value": 7.5}` |
| `DELETE /api/sessions/{id}/students/{student}/grade` | Clear a grade |
| `POST /api/sessions/{id}/students/{student}/notes` | Add a note, `{"body": "..."}` |
| `DELETE /api/notes/{id}` | Delete your own note |
| `PUT /api/sessions/{id}/summary/review` | Mark the summary reviewed, `{"reviewed": true}` |
| `POST /api/signups` | Pilot sign-up, `{"name", "school", "email"}` (no sign-in needed) |

Only staff of a course can see or change its data; anything else returns 404.

## Configuration

Environment variables, or a `.env` file in `backend/`:

| Variable | Default |
| --- | --- |
| `PARTI_DATABASE_URL` | `postgresql+psycopg://parti@127.0.0.1:5432/parti` |
| `PARTI_DEMO_USER_EMAIL` | unset (API returns 401) |
| `PARTI_DISPLAY_TIMEZONE` | `Europe/Madrid` |
| `PARTI_NOTIFY_TO`, `PARTI_SMTP_*` | unset (see Sign-up notifications) |
| `PARTI_TEST_DATABASE_URL` | `postgresql+psycopg://parti@127.0.0.1:5432/parti_test` (emptied by the tests) |

## Sign-up notifications

Each pilot sign-up emails your team right away (name, school and email, with
Reply-To set to the person). Nobody else is emailed, including the person who
signed up. If someone signs up again with the same email, the team gets an
"Updated pilot sign-up" email.

```sh
PARTI_NOTIFY_TO="you@example.edu, teammate@example.edu"
PARTI_NOTIFY_FROM="Parti <parti@example.edu>"
PARTI_SMTP_HOST=smtp.example.edu
PARTI_SMTP_PORT=587               # STARTTLS; set PARTI_SMTP_STARTTLS=false to turn it off
PARTI_SMTP_USERNAME=...
PARTI_SMTP_PASSWORD=...           # keep this in .env, never in git
```

Without `PARTI_SMTP_HOST`, nothing is sent: the server logs a line with the
subject and recipients instead. A mail failure never stops a sign-up from
saving.

**Daily summary instead (or as well):** set `PARTI_NOTIFY_EACH_SIGNUP=false` to
stop the per-sign-up emails, and run the summary from cron, e.g. every day at 8:00:

```sh
0 8 * * * cd /path/to/parti/backend && .venv/bin/python -m parti.cli digest
```

It lists sign-ups from the last 24 hours (`--hours` to change) and sends nothing
when there are none.

## Tests

```sh
pytest
```

The tests migrate the test database from scratch and cover the sample-data
spec, grades, notes, the summary review, sign-ups, access checks and the audit log.

## Not done yet

- University sign-in (OIDC) and a student-facing view
- Admin page for sign-ups (the old Flask app had one: `parti/legacy_flask_app.py`)
- Deployment. Before real students' data goes in, agree on hosting, HTTPS,
  retention and deletion with the university, and have the landing page
  reviewed by the Communications team.
