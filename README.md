# Parti

A clickable prototype of Parti, a tool that helps professors grade class
participation from evidence rather than memory. It has two views, a landing
page and a professor dashboard, served by a small Flask app.

- **Landing page:** the pilot sign-up form saves to a SQLite database.
- **Professor dashboard:** uses fictional sample data from `sample_data.py`.
  Once the professor signs in, grades, notes and the class-summary review are
  saved in the same SQLite database. Without signing in, the dashboard is a demo
  and changes reset on reload.

## Run it

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

export PARTI_ADMIN_PASSWORD='choose-a-password'      # turns on /admin
export PARTI_PROFESSOR_PASSWORD='another-password'  # turns on saving in the dashboard
export PARTI_SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
flask --app app run --debug
```

Open http://127.0.0.1:5000.

## Pages and endpoints

| Path | What it does |
| --- | --- |
| `/` | The prototype (landing page and dashboard) |
| `POST /api/signups` | Saves a pilot sign-up. JSON body: `name`, `school`, `email`. Returns `201`, or `400` with `errors` per field. Signing up again with the same email updates the entry. |
| `/admin` | Lists sign-ups. Username `admin`, password from `PARTI_ADMIN_PASSWORD`. Off when the variable is unset. |
| `/admin/signups.csv` | Downloads the sign-ups as CSV |
| `/login` | Professor sign-in. Password from `PARTI_PROFESSOR_PASSWORD`. Off when the variable is unset. |
| `POST /logout` | Signs the professor out |
| `PUT /api/grades/<student>` | Saves a grade. JSON body: `grade` (0–10, steps of 0.5). |
| `DELETE /api/grades/<student>` | Clears a grade |
| `POST /api/students/<student>/notes` | Adds a note. JSON body: `text` (up to 2000 characters). Returns the note with its `id`. |
| `DELETE /api/notes/<id>` | Deletes a note |
| `PUT /api/summary/reviewed` | Marks the class summary as reviewed. JSON body: `reviewed` (`true` or `false`). |

The `/api/grades`, `/api/students`, `/api/notes` and `/api/summary` endpoints
need the professor to be signed in (`401` otherwise), only take JSON bodies,
and return `400` with `errors` per field when a value is invalid. The sample
data's pre-saved grades are copied into the database the first time it is
created; after that the database is what counts.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `PARTI_DATABASE` | `instance/parti.db` | SQLite file for sign-ups and dashboard data |
| `PARTI_ADMIN_PASSWORD` | unset | Password for the admin pages |
| `PARTI_PROFESSOR_PASSWORD` | unset | Password for the professor sign-in |
| `PARTI_SECRET_KEY` | random on each start | Signs the session cookie. Set it, or everyone is signed out when the server restarts. |

## Tests

```sh
pytest
```

The tests cover the sign-up API, validation, admin access, CSV export,
professor sign-in and the grade, note and summary-review endpoints, and
check that the sample data matches the prototype spec (41 contributions,
balance warning, 3 quiet students, 8 pre-graded, no names in the class summary).

## Files

- `app.py`: Flask app, SQLite storage and admin pages
- `sample_data.py`: fictional dashboard data (edit freely)
- `templates/index.html`: the prototype page
- `templates/admin.html`: sign-up list
- `templates/login.html`: professor sign-in
- `tests/test_app.py`: tests

## Before using with real people

Sign-ups are personal data (GDPR). Before deploying:

- Run it behind HTTPS with a production WSGI server such as gunicorn, not `flask run`.
- Use strong admin and professor passwords, set `PARTI_SECRET_KEY`, and add
  rate limiting to `POST /api/signups` and `POST /login`.
- Grades and professor notes are confidential student records. Real student
  data needs one account per professor and per course, not one shared password.
- Agree where the database lives and when it's deleted. For IE, check the
  hosting and data handling with IE Cloud Services first.
- Have the Communications team review the landing page before it's public.
