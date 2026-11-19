# To-Do

An open-source Flask task manager with projects, nested tasks, flexible work schedules,
recurring tasks, trash and undo, and day / week / month / year calendars.
Accounts have private tasks, projects, schedules, and trash; usernames are
case-insensitive, and ownership is enforced in both route queries and database
constraints.

Run locally on your infrastructure. Designed to be run on Ubuntu. Run in WSL or
an LXC container.

Tasks are based on their due date and grouped by project. All tasks for a given
day are available in that day's respective week, month, and year, giving a
time-oriented task management approach.

Yes, it's vibe coded. But that doesn't mean it's not useful.

## Features

- **Tasks and sub-tasks** — nest tasks, track progress across leaf tasks, add
  notes, set due dates, and schedule one or more work-on dates. A date range can
  cover up to 90 days.
- **Recurring tasks** — daily, weekly, or monthly. Completing a recurring task
  creates its next occurrence; monthly dates that do not exist in the next
  month clamp to that month's last day.
- **Projects** — group tasks on colour-coded project pages with a plain-text
  notebook that autosaves. Deleting a project keeps its tasks.
- **Calendars** — view tasks by due date and/or planned work date. Week, month,
  and year views have list and calendar layouts; year view links its twelve
  mini-calendars to their months. Month and week calendar days link to the day
  view.
- **Task list tools** — filter by title, project, and status; order top-level
  tasks by due date or project; and show or hide completed tasks. Task details
  include controls to complete, move, or reschedule a whole subtree.
- **Trash and undo** — deleting a task snapshots its subtree. Restore from the
  undo bar or Trash, or permanently delete it. On deletion, batches beyond the
  newest 20 and batches older than 30 days are pruned.
- **Accounts** — open registration, private user data, password changes,
  customizable display names and appearance settings (highlight colour and
  Night mode), and self-service deletion for every account
  except the built-in `admin`. Admins can view and delete users, grant or revoke
  admin privileges, and revoke their own privileges or delete their own
  account. There is no password-reset flow.
- **Mobile and installable shell** — responsive layout and a PWA manifest.
  Static assets are cached and an offline page is shown when navigation cannot
  reach the server; task pages and task data are not available offline.
- **Security controls** — CSRF protection for state-changing requests, login
  lockout, safe redirects, security headers, session-cookie options, and
  SQLite foreign-key enforcement.

## User Guide

### Create an account and sign in

Open the application and choose **Sign up**. Usernames must be 3–80
characters; username matching is case-insensitive. Passwords must be at least
8 characters. Each account sees only its own tasks, projects, schedules, and
trash. Open registration means anyone who can reach the app can create an
account.

On a new, migrated database, the app also creates an initial account named
`admin`. Its generated password is written to `instance/admin_password.txt`,
unless `TODO_ADMIN_PASSWORD` was set before startup. This account can manage
users. Sign in and change its password from the account link in the top
navigation; keep the password file private and delete it after noting the
generated password. The app restricts its `instance/` directory to the
service owner so the database, session key, and generated password remain
private.

### Dashboard and tasks

The dashboard greets you with **Welcome Back** and your display name, then
separates tasks **Due today** and **Working on today**. It also shows upcoming
due tasks, overdue tasks, and open unscheduled tasks. The **Hide completed**
control is a per-session preference.

Choose **New task** to create a task. Add a title, optional notes and due date,
and optionally choose a project or parent task. To make it recurring, choose
daily, weekly, or monthly; a due date is required. Marking a recurring task
done creates its next occurrence.

Open a task to manage its notes, due date, work-on dates, and sub-tasks. Add a
single work-on date or an inclusive date range of up to 90 days. Add separate
dates individually for a non-consecutive schedule. A task card's date control
can also add a work-on date directly from a dashboard, list, or calendar.
Sub-tasks can be nested; on a parent task, the progress indicator summarizes
completed leaf tasks.

On a task's detail page, **Sub-tree actions** can mark the task and all its
sub-tasks done, move them to a project, or set their due date. The Tasks page
shows top-level tasks only, puts completed tasks last, and filters by title,
project, and open/done status as you type or change a filter. Drag project
cards on the Projects page to reorder them.

### Projects

Choose **New project** to create a colour-coded project. Drag its card on the
Projects page to change the project order. Its page has a
notebook for plain-text notes and a task list. Use the add-task field to create
a task or search for an existing task to attach it to the project. Deleting a
project does not delete its tasks.

### Calendar

Choose **Calendar** and select **Day**, **Week**, **Month**, or **Year**.
Navigate with the previous/next arrows, **Today**, or **Jump to…**. Use **Due**
and **Working on** to show either or both date categories.

Week, month, and year pages have **List** and **Calendar** layouts. The month
calendar shows a month grid; each date opens its day view. The week calendar
shows seven linked days. The year calendar shows twelve mini-calendars; select
a month to open its month view. Day view is a task list.

### Trash and account

Deleting a task also deletes its sub-tasks, but keeps a snapshot in **Trash**.
Use **Undo** immediately after a deletion, or restore a batch from Trash. When
a task is deleted, batches older than 30 days and batches beyond the newest 20
are pruned. Deleting a batch or emptying Trash is permanent.

Select your username in the navigation to set your display name, choose an
application highlight colour, turn Night mode on or off, or change your
password. The highlight colour defaults to the standard orange and applies in
both light and Night mode. Night mode uses a midnight-blue background with
light text. Your display name defaults to your username and can be changed at
any time. To log out, use **Log out**.

Administrators can select **Users** in the navigation to see registered
accounts, delete any user (including themselves, except for the protected
built-in `admin` account), or use **Make admin** and **Revoke admin** to change
administrator privileges. When an administrator deletes their own account,
they are logged out. An administrator may also revoke their own privileges;
they are then returned to their account page and lose access to admin tools. If
the last administrator deletes themselves or revokes their rights, no one can
manage users until an administrator is restored directly in the database.

Use the account page's **Delete my account** form to delete any account except
the protected built-in `admin`; enter the current password to confirm. Admins
can also delete themselves from the Users list. Self-deletion logs you out.
Deletion permanently removes the account's tasks, projects, schedules, and
trash.

### Install and offline behavior

On browsers that support installation, use the browser's install option to add
the app to your device. Service workers require HTTPS, except on localhost.
While offline, cached static files remain available and navigation shows an
offline page; authenticated task pages and task data require a connection to
the server.

## Technical Guide

### Requirements and installation

Use Python 3.11 or newer. From the repository directory:

```bash
python3 --version
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` as needed. In particular, set `TODO_ADMIN_PASSWORD` before the
first application startup if you want to choose the initial `admin` password.
Keep `.env`, the database, and generated secret files private.

Apply the database migrations:

```bash
.venv/bin/flask --app wsgi db upgrade
```

The user-management migration marks the existing case-insensitive `admin`
account as an administrator; other existing accounts remain regular users.

Run the app with Gunicorn:

```bash
.venv/bin/gunicorn -c gunicorn.conf.py wsgi:app
```

Gunicorn reads `.env` from the working directory. By default it binds to
`0.0.0.0:8000` and serves HTTP. The app creates its `instance/` directory,
persists a secret key there if `SECRET_KEY` is unset, and seeds the initial
`admin` account when it starts against a migrated database with no users.
Check `instance/admin_password.txt` for the generated password.

To run the Flask development server locally instead:

```bash
FLASK_CONFIG=development .venv/bin/flask --app wsgi run
```

The development server defaults to localhost on port 5000 and is for
development, not deployment. Check the health endpoint with
`curl http://127.0.0.1:8000/healthz` when using the default Gunicorn bind.

### HTTP, HTTPS, and reverse proxies

Plain HTTP is the default. To serve HTTPS directly from Gunicorn, configure
both the certificate chain and private-key paths in `.env` or the service
environment:

```dotenv
BIND=0.0.0.0:8443
TODO_SSL_CERTFILE=/path/to/fullchain.pem
TODO_SSL_KEYFILE=/path/to/private-key.pem
TODO_SECURE_COOKIES=1
```

The certificate file should contain the certificate chain. Both TLS paths
must be set together. Set `TODO_SECURE_COOKIES=1` whenever the browser-facing
connection uses HTTPS, whether TLS is terminated by Gunicorn or a reverse
proxy. Keep the private key readable only by the service account.

For a reverse proxy that handles TLS, omit both Gunicorn certificate settings
and bind Gunicorn to loopback, for example `BIND=127.0.0.1:8000`. Configure the
proxy to forward requests to Gunicorn and enable secure cookies as above. If
the proxy connects from a non-loopback address, set `FORWARDED_ALLOW_IPS` to
that proxy's trusted IP address or addresses; do not use `*` on a public
interface.

### Configuration

Set options in `.env` or the service environment. `.env.example` lists the
commonly used settings.

| Variable                   | Default                            | Purpose                                                                  |
|----------------------------|------------------------------------|--------------------------------------------------------------------------|
| `FLASK_CONFIG`             | `production`                       | Select `production`, `development`, or `testing` app settings.           |
| `BIND`                     | `0.0.0.0:8000`                     | Gunicorn listen address.                                                 |
| `WEB_CONCURRENCY`          | Calculated from CPU count          | Gunicorn worker count.                                                   |
| `THREADS`                  | `2`                                | Threads per Gunicorn worker.                                             |
| `GUNICORN_TIMEOUT`         | `60`                               | Worker timeout in seconds.                                               |
| `FORWARDED_ALLOW_IPS`      | `127.0.0.1,::1`                    | Trusted proxy addresses allowed to set forwarded headers.                |
| `TODO_ADMIN_PASSWORD`      | Generated                          | Initial `admin` password, used only when the database has no users.      |
| `TODO_DATABASE`            | `instance/todo.sqlite3`            | SQLAlchemy database URL.                                                 |
| `SECRET_KEY`               | Generated in `instance/secret_key` | Session and CSRF signing key.                                            |
| `TODO_TIMEZONE`            | `UTC`                              | IANA timezone used for "today" and date defaults.                        |
| `TODO_SSL_CERTFILE`        | Unset                              | Gunicorn TLS certificate/full-chain PEM; set with `TODO_SSL_KEYFILE`.    |
| `TODO_SSL_KEYFILE`         | Unset                              | Gunicorn TLS private-key PEM; set with `TODO_SSL_CERTFILE`.              |
| `TODO_SECURE_COOKIES`      | Off                                | Set to `1` when the browser-facing connection uses HTTPS.                |
| `TODO_TRUSTED_HOSTS`       | Unset                              | Optional comma-separated host allow-list.                                |
| `TODO_LOGIN_MAX_ATTEMPTS`  | `5`                                | Failed logins before account lockout.                                    |
| `TODO_LOGIN_LOCK_MINUTES`  | `15`                               | Duration of login lockout.                                               |
| `TODO_TASK_NOTES_LIMIT`    | `100000`                           | Maximum task-note characters.                                            |
| `TODO_PROJECT_NOTES_LIMIT` | `200000`                           | Maximum project-note characters.                                         |

Notes are stored as plain text and are not rendered as Markdown.

### Database migrations and development

After changing models, generate and review a migration, then apply it:

```bash
.venv/bin/flask --app wsgi db migrate -m "Describe change"
.venv/bin/flask --app wsgi db upgrade
```

The optional `deploy/todo.service` is a systemd example with paths specific to
this checkout. Adapt its working directory, executable paths, service user,
and writable instance directory before using it elsewhere.

To run tests, install the test tools separately (they are not runtime
requirements), then run:

```bash
.venv/bin/pip install pytest ruff
.venv/bin/python -m pytest
.venv/bin/ruff check app tests
```

### Data, secrets, and repository layout

- SQLite database: `instance/todo.sqlite3` by default; override with
  `TODO_DATABASE`.
- Secret key: `instance/secret_key` by default; generated with restrictive
  file permissions when first needed. Keep it stable across restarts.
- Back up the database and secret key together. Stop the app before copying
  the `instance/` directory, or use SQLite's online backup facility while it
  is running. SQLite uses foreign keys, WAL journaling, and a 5-second busy
  timeout.

```text
app/
  __init__.py       app factory, headers, error handlers, template helpers
  config.py         production / development / testing configuration
  models.py         users, projects, tasks, work dates, deleted batches
  palette.py        project colours
  periods.py        day/week/month/year calculations and timezone-aware today
  queries.py        task loading, tree/progress preparation, date grouping
  utils.py          safe redirects, CSRF helpers, date and notes helpers
  access.py         account-scoped object lookup
  auth.py           registration, login, lockout, password changes
  main.py           dashboard and preferences
  tasks.py          task CRUD, recurrence, trash/undo, subtree actions
  projects.py       project CRUD, notebook autosave, task assignment
  calendarview.py   day/week/month/year views and calendar grids
  templates/        Jinja templates
  static/           CSS, JavaScript, manifest, service worker, offline page
migrations/         Alembic database migrations
tests/              pytest test suite
deploy/todo.service systemd example
wsgi.py             Gunicorn entry point
gunicorn.conf.py    Gunicorn server configuration
```
