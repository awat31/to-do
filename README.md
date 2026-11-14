# To-Do

A Flask-based task app: recursive sub-tasks, projects with notebook pages,
flexible work-on scheduling, and calendar views by day / week / month / year.

## Features

- **Recursive sub-tasks** — any task can have sub-tasks, to unlimited depth.
  Parents show a progress bar rolled up from their (leaf) sub-tasks.
- **Projects** — each project is a notebook page with a large auto-saving
  notes area plus its assigned tasks. Tasks link to a project; projects are
  colour-coded (bright palette on a cream background).
- **Due date & work-on dates** — a task has a single *due* date plus any
  number of *work on* dates: a single date, a range (up to 90 days), or
  several non-consecutive dates.
- **Add dates from anywhere** — every task card in the calendar has a quick
  date box (`＋`) to add that day as a work-on date.
- **Calendar views** — Day / Week / Month / Year, navigable with ‹ / › and
  a "Today" button. A task visible on a day is automatically visible in its
  containing week, month and year. Within each period you can view tasks
  **by due date** or **by what you plan to work on**, independently:
  a task due 03/03/2026 that you plan to work on 28/02/2026 shows under
  *February* in the "Working on" section and under *March* in the "Due"
  section. The year view groups by month; week/month views group by day.

## Stack

Python 3.14 · Flask 3 (app factory + blueprints) · Flask-SQLAlchemy +
Flask-Migrate (Alembic) · Flask-Login (single admin account) · Flask-WTF
(CSRF everywhere, including JSON fetches) · Gunicorn · systemd · pytest.

## Layout

```
app/
  __init__.py       app factory, error handlers, template helpers
  config.py         production / development / testing configs
  models.py         Project, Task (self-referential), TaskDate, User
  palette.py        bright project colours
  periods.py        day/week/month/year period maths
  queries.py        due/work-on grouping queries
  auth.py           single-user login
  main.py           dashboard
  tasks.py          task CRUD, sub-tasks, due + work-on dates
  projects.py       project CRUD, notebook autosave
  calendarview.py   day/week/month/year views + quick-add-date
  templates/        Jinja templates (macros for task cards/trees)
  static/           stylesheet + small vanilla JS (autosave, confirms)
migrations/         Alembic migrations
tests/              pytest suite (in-memory SQLite)
deploy/todo.service systemd unit
wsgi.py             gunicorn entry point
gunicorn.conf.py    production server config
```

## Running

Already installed and running as a service on this container:

```bash
systemctl status todo
curl http://127.0.0.1:8000/healthz   # -> {"status":"ok"}
```

Log in at `http://<host>:8000/login` with username `admin`. On first run a
random password is generated and stored at `instance/admin_password.txt`
(set `TODO_ADMIN_PASSWORD` instead to choose your own; then delete the file).

To manage the service:

```bash
systemctl restart todo
journalctl -u todo -f        # logs
```

To bind to a different port/address, edit `BIND` in `deploy/todo.service`
(or export the env var) and restart.

### From scratch

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
FLASK_CONFIG=production .venv/bin/flask --app wsgi db upgrade
.venv/bin/gunicorn -c gunicorn.conf.py wsgi:app
```

## Development

```bash
FLASK_CONFIG=development .venv/bin/flask --app wsgi run
.venv/bin/python -m pytest tests/    # test suite
```

After changing models:

```bash
.venv/bin/flask --app wsgi db migrate -m "Describe change"
.venv/bin/flask --app wsgi db upgrade
```

## Data & secrets

- Database: `instance/todo.sqlite3` (override with `TODO_DATABASE`).
- Secret key: `instance/secret_key` (generated, chmod 600).
- Back up by copying the `instance/` directory while the app is stopped
  (or use `sqlite3 instance/todo.sqlite3 '.backup ...'` live).
