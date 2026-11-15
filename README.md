# To-Do

A Flask-based task app: recursive sub-tasks, projects with notebook pages,
flexible work-on scheduling, recurring tasks, trash/undo, and calendar views
by day / week / month / year.

## Features

- **Recursive sub-tasks** — any task can have sub-tasks to unlimited depth.
  Parents show a progress bar rolled up from their leaf tasks.
- **Projects** — each project is a notebook page with a large auto-saving
  notes area plus its assigned tasks. Tasks link to a project; projects are
  colour-coded (bright palette on a cream background).
- **Due date & work-on dates** — a task has one *due* date plus any number of
  *work on* dates: a single date, a range (up to 90 days), or several
  non-consecutive dates.
- **Recurring tasks** — daily / weekly / monthly. Completing a task creates
  the next occurrence (monthly repeats clamp to month end).
- **Calendar views** — Day / Week / Month / Year, navigable with ‹ / › and a
  "Today" button. Month view includes a compact at-a-glance grid. Tasks can be
  viewed **by due date** or **by planned work date**, independently, and the
  selection survives navigation and the jump-to popup.
- **Task list tools** — filters (text / project / status), drag-and-drop
  sibling reordering, bulk complete / move / reschedule for a subtree, and an
  optional hide-completed switch.
- **Trash & undo** — deleting a subtree snapshots it; restore from Trash or
  the undo bar. Batches auto-expire after 30 days (newest 20 kept).
- **Autosave** — task and project notes save as you type, with retry,
  keepalive, and a form-encoded `sendBeacon` payload for the last edit.
- **PWA / mobile shell** — installable manifest, service-worker static cache,
  offline fallback page, and a responsive touch layout.
- **Security** — CSRF on every state-changing request, login lockout, safe
  redirects, CSP/security headers, session hardening, and SQLite foreign-key
  enforcement.

## Stack

Python 3.14 · Flask 3 (app factory + blueprints) · Flask-SQLAlchemy +
Flask-Migrate (Alembic) · Flask-Login (single admin account) · Flask-WTF
(CSRF everywhere, including JSON fetches) · Gunicorn · systemd · pytest.

## Layout

```
app/
  __init__.py       app factory, headers, error handlers, template helpers
  config.py         production / development / testing configs
  models.py         Project, Task (self-referential), TaskDate, User, DeletedBatch
  palette.py        bright project colours
  periods.py        day/week/month/year maths + timezone-aware today
  queries.py        eager loading, tree/progress preparation, due/work grouping
  utils.py          safe redirects, CSRF helpers, date helpers
  auth.py           single-user login, lockout, remember-me
  main.py           dashboard and preferences
  tasks.py          task CRUD, recurrence, trash/undo, bulk actions, reorder
  projects.py       project CRUD, notebook autosave, typeahead assignment
  calendarview.py   day/week/month/year views, month grid, quick-add-date
  templates/        Jinja templates (macros for task cards/trees)
  static/           stylesheet, JS, manifest, service worker, offline page
migrations/         Alembic migrations
tests/              pytest suite (in-memory SQLite, CSRF-enabled cases)
deploy/todo.service systemd unit
wsgi.py             gunicorn entry point
gunicorn.conf.py    production server config
```

`index.html` in the repo root is a separate, no-dependency localStorage demo
and is not part of the deployed Flask service.

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
.venv/bin/python -m ruff check app tests
```

After changing models:

```bash
.venv/bin/flask --app wsgi db migrate -m "Describe change"
.venv/bin/flask --app wsgi db upgrade
```

## Configuration

Set these in `.env` or the systemd unit:

| Variable | Default | Purpose |
|----------|---------|---------|
| `TODO_ADMIN_PASSWORD` | generated | Admin password; kept in sync on restart |
| `TODO_DATABASE` | `instance/todo.sqlite3` | SQLAlchemy database URL |
| `SECRET_KEY` | `instance/secret_key` | Session/CSRF secret (file chmod 600) |
| `TODO_TIMEZONE` | `UTC` | IANA timezone for "today" and date defaults |
| `TODO_SECURE_COOKIES` | off | Enable only behind HTTPS |
| `TODO_TRUSTED_HOSTS` | any | Comma-separated host allow-list |
| `TODO_LOGIN_MAX_ATTEMPTS` | `5` | Failed logins before lockout |
| `TODO_LOGIN_LOCK_MINUTES` | `15` | Lockout duration |
| `TODO_TASK_NOTES_LIMIT` | `100000` | Max task-note characters |
| `TODO_PROJECT_NOTES_LIMIT` | `200000` | Max project-note characters |

Notes are intentionally plain text (no Markdown rendering), so autosave never
emits unsanitised HTML.

## Data & secrets

- Database: `instance/todo.sqlite3` (override with `TODO_DATABASE`).
- Secret key: `instance/secret_key` (generated, chmod 600).
- Back up by copying the `instance/` directory while the app is stopped
  (or use `sqlite3 instance/todo.sqlite3 '.backup ...'` live).
- SQLite runs with foreign keys on, WAL journaling, and a 5-second busy
  timeout (set per connection in `models.py`).
