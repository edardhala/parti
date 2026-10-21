# Parti

A clickable prototype of Parti, a tool that helps professors grade class
participation from evidence rather than memory. It has two views, a landing
page and a professor dashboard, served by a small Flask app.

- **Landing page:** the pilot sign-up form saves to a SQLite database.
- **Professor dashboard:** uses fictional sample data from `sample_data.py`.
  Grades and notes entered in the dashboard stay in the browser and reset on reload.

## Run it

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

export PARTI_ADMIN_PASSWORD='choose-a-password'   # turns on /admin
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

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `PARTI_DATABASE` | `instance/parti.db` | SQLite file for sign-ups |
| `PARTI_ADMIN_PASSWORD` | unset | Password for the admin pages |

## Tests

```sh
pytest
```

The tests cover the sign-up API, validation, admin access and CSV export, and
check that the sample data matches the prototype spec (41 contributions,
balance warning, 3 quiet students, 8 pre-graded, no names in the class summary).

## Files

- `app.py`: Flask app, SQLite storage and admin pages
- `sample_data.py`: fictional dashboard data (edit freely)
- `templates/index.html`: the prototype page
- `templates/admin.html`: sign-up list
- `tests/test_app.py`: tests

## Before using with real people

Sign-ups are personal data (GDPR). Before deploying:

- Run it behind HTTPS with a production WSGI server such as gunicorn, not `flask run`.
- Use a strong admin password, and add rate limiting to `POST /api/signups`.
- Agree where the database lives and when it's deleted. For IE, check the
  hosting and data handling with IE Cloud Services first.
- Have the Communications team review the landing page before it's public.
