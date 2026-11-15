import os

# Force test determinism regardless of .env / shell environment
os.environ["FLASK_CONFIG"] = "testing"
os.environ["TODO_ADMIN_PASSWORD"] = "test-password"

import pytest

from app import create_app, db
from app.periods import BadPeriod, period, period_value


@pytest.fixture()
def app():
    application = create_app()
    ctx = application.app_context()
    ctx.push()
    db.create_all()  # tests bypass migrations (in-memory SQLite)
    from app import _ensure_admin
    _ensure_admin(application)
    yield application
    db.session.remove()
    db.drop_all()
    ctx.pop()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def logged_in(client):
    client.post("/login", data={"username": "admin", "password": "test-password"})
    return client


def _token(html):
    for line in html.splitlines():
        if 'name="csrf_token"' in line and 'value="' in line:
            return line.split('value="')[1].split('"')[0]
    return ""


def _csrf(client):
    resp = client.get("/tasks/new")
    if resp.status_code == 302:
        resp = client.get(resp.headers["Location"])
    return _token(resp.data.decode())


@pytest.fixture()
def csrf_client(app):
    """A logged-in client with CSRF protection actually switched on."""
    app.config["WTF_CSRF_ENABLED"] = True
    client = app.test_client()
    token = _token(client.get("/login").data.decode())
    client.post(
        "/login",
        data={
            "username": "admin",
            "password": "test-password",
            "csrf_token": token,
        },
    )
    return client


def _create_task(logged_in, *, title, due=None, project=None, parent=None):
    data = {
        "title": title,
        "notes": "",
        "due_date": due or "",
        "project_id": project or 0,
        "parent_id": parent or 0,
    }
    logged_in.post("/tasks/new", data=data, follow_redirects=True)
    from app.models import Task
    task = Task.query.filter_by(title=title).first()
    assert task is not None
    return task


# --- auth ---------------------------------------------------------------

def test_requires_login(client):
    resp = client.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_login_rejects_bad_password(client):
    resp = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert "Invalid username or password" in resp.data.decode()


def test_login_accepts_correct_password(client):
    resp = client.post(
        "/login", data={"username": "admin", "password": "test-password"},
        follow_redirects=True,
    )
    assert resp.status_code == 200


# --- calendar period logic ---------------------------------------------

def test_period_day():
    p = period("day", "2026-03-03")
    assert (p["start"], p["end"]) == (__import__("datetime").date(2026, 3, 3),) * 2


def test_period_week():
    p = period("week", "2026-W09")
    assert p["start"].isoformat() == "2026-02-23"
    assert p["end"].isoformat() == "2026-03-01"


def test_period_year():
    p = period("year", "2026")
    assert p["start"].isoformat() == "2026-01-01"
    assert p["end"].isoformat() == "2026-12-31"


def test_period_invalid():
    with pytest.raises(BadPeriod):
        period("month", "nonsense")


def test_period_value_roundtrip():
    import datetime as dt
    for kind in ("day", "week", "month", "year"):
        d = dt.date(2026, 3, 3)
        assert period_value(kind, d) == period(kind, period_value(kind, d))["value"]


# --- core behaviour -----------------------------------------------------

def test_work_and_due_grouping_spec_example(logged_in):
    """Due 03/03/2026, work-on 28/02/2026 -> February in work view, March in due view."""
    _create_task(logged_in, title="Paint walls", due="2026-03-03")
    from app.models import Task, TaskDate, db

    task = Task.query.filter_by(title="Paint walls").first()
    db.session.add(TaskDate(task_id=task.id, date=__import__("datetime").date(2026, 2, 28)))
    db.session.commit()

    feb = logged_in.get("/calendar/month/2026-02").data.decode()
    mar = logged_in.get("/calendar/month/2026-03").data.decode()
    year_work = logged_in.get("/calendar/year/2026?work=1").data.decode()
    year_due = logged_in.get("/calendar/year/2026?due=1").data.decode()

    assert "Paint walls" in feb
    assert "Working on in February" in feb
    assert "Due in February" not in feb
    assert "Paint walls" in mar and "Due in March" in mar
    assert "February" in year_work and "Paint walls" in year_work
    assert "March" in year_due and "Paint walls" in year_due
    # (the jump popup's month dropdown also contains month names, so check
    # the section headings specifically)
    assert '"subhead">February' not in year_due


def test_date_range_expands_into_daily_work_dates(logged_in):
    task = _create_task(logged_in, title="Range test")
    logged_in.post(
        f"/tasks/{task.id}/work-dates",
        data={"start": "2026-03-10", "end": "2026-03-12"},
    )
    dates = [td.date.isoformat() for td in task.work_dates]
    assert dates == ["2026-03-10", "2026-03-11", "2026-03-12"]


def test_subtask_progress_and_cascade_delete(logged_in):
    from app.models import Task

    parent = _create_task(logged_in, title="Parent")
    child = _create_task(logged_in, title="Child", parent=parent.id)
    grandchild = _create_task(logged_in, title="Grandchild", parent=child.id)
    grandchild.done = True
    db.session.commit()

    assert set(parent.descendant_ids()) == {child.id, grandchild.id}
    assert parent.progress() == (1, 1)
    # a leaf is not a "project" and no longer shows a 0/1 progress bar
    assert grandchild.progress() is None

    parent_id, child_id, grandchild_id = parent.id, child.id, grandchild.id
    logged_in.post(f"/tasks/{parent_id}/delete")
    db.session.close()  # drop cached instances deleted by the bulk delete
    assert db.session.get(Task, child_id) is None
    assert db.session.get(Task, grandchild_id) is None


def test_task_appears_in_day_week_month_year(logged_in):
    _create_task(logged_in, title="Due today")
    from app.models import Task

    task = Task.query.filter_by(title="Due today").first()
    task.due_date = __import__("datetime").date(2026, 2, 25)
    db.session.commit()

    for kind, value in (("day", "2026-02-25"), ("week", "2026-W09"),
                        ("month", "2026-02"), ("year", "2026")):
        page = logged_in.get(f"/calendar/{kind}/{value}").data.decode()
        assert "Due today" in page, f"missing in {kind} view"


def _meta_csrf(client, url="/"):
    page = client.get(url).data.decode()
    marker = 'name="csrf-token" content="'
    return page.split(marker)[1].split('"')[0]


def test_project_notes_autosave(logged_in):
    logged_in.post("/projects/new", data={"name": "Renovation", "color": "pink"})
    from app.models import Project
    project = Project.query.first()
    token = _meta_csrf(logged_in, f"/projects/{project.id}")
    resp = logged_in.post(
        f"/projects/{project.id}/notes",
        json={"notes": "Budget ~500"},
        headers={"X-CSRFToken": token},
    )
    assert resp.status_code == 200
    assert Project.query.first().notes == "Budget ~500"


def test_quick_add_work_date(logged_in):
    import datetime as dt
    task = _create_task(logged_in, title="Quick add")
    csrf = _csrf(logged_in)
    resp = logged_in.post(
        f"/calendar/quick-add-date/{task.id}",
        data={"date": "2026-02-28", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert dt.date(2026, 2, 28) in [td.date for td in task.work_dates]


def test_quick_add_date_defaults_to_today(logged_in):
    """Regression: clicking ＋ with an empty date box gave a 400."""
    import datetime as dt
    task = _create_task(logged_in, title="Quick add empty")
    csrf = _csrf(logged_in)
    resp = logged_in.post(
        f"/calendar/quick-add-date/{task.id}",
        data={"date": "", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert dt.date.today() in [td.date for td in task.work_dates]


def test_quick_add_invalid_date_flashes_not_400(logged_in):
    task = _create_task(logged_in, title="Quick add garbage")
    csrf = _csrf(logged_in)
    resp = logged_in.post(
        f"/calendar/quick-add-date/{task.id}",
        data={"date": "not-a-date", "csrf_token": csrf},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Could not read date" in resp.data.decode()


def test_dashboard_card_prefills_today(logged_in):
    """The ＋ box on dashboard cards should carry today's date pre-filled."""
    import datetime as dt
    task = _create_task(logged_in, title="Due now")
    task.due_date = dt.date.today()
    db.session.commit()
    page = logged_in.get("/").data.decode()
    assert f'value="{dt.date.today().isoformat()}"' in page


def test_dashboard_work_count_matches_list(logged_in):
    """Regression: 'Working on today' counted tasks hidden as due-today dupes."""
    import datetime as dt
    task = _create_task(logged_in, title="Both today")
    task.due_date = dt.date.today()
    from app.models import TaskDate
    db.session.add(TaskDate(task_id=task.id, date=dt.date.today()))
    db.session.commit()

    page = logged_in.get("/").data.decode()
    assert 'Working on today <span class="count">0</span>' in page
    assert "Both today" in page  # visible once, under Due today


def test_dashboard_renders_with_upcoming_tasks(logged_in):
    """Regression: dashboard 500 (dict unpacking) once tasks exist."""
    import datetime as dt
    task = _create_task(logged_in, title="Later")
    task.due_date = dt.date.today() + dt.timedelta(days=3)
    db.session.commit()
    page = logged_in.get("/").data.decode()
    assert "Later" in page and task.due_date.strftime("%a %-d %b %Y") in page


def test_admin_password_syncs_from_env(app):
    """Regression: .env password must update an existing admin account."""
    import os

    from app import _ensure_admin
    from app.models import User
    user = User.query.filter_by(username="admin").first()
    assert user.check_password("test-password")

    os.environ["TODO_ADMIN_PASSWORD"] = "brand-new-secret"
    try:
        _ensure_admin(app)
        db.session.expire_all()
        assert User.query.filter_by(username="admin").first().check_password("brand-new-secret")
        assert not User.query.filter_by(username="admin").first().check_password("test-password")
    finally:
        os.environ["TODO_ADMIN_PASSWORD"] = "test-password"  # restore for other tests
        user = User.query.filter_by(username="admin").first()
        user.set_password("test-password")
        db.session.commit()


def test_calendar_jump_day_week_month_year(logged_in):
    for qs, expected in (
        ({"date": "2026-03-03"}, "/calendar/day/2026-03-03"),
        ({"year": "2026", "month": "3"}, "/calendar/month/2026-03"),
        ({"year": "2026"}, "/calendar/year/2026"),
    ):
        resp = logged_in.get("/calendar/jump", query_string=qs)
        assert resp.status_code == 302
        assert expected in resp.headers["Location"]
    # bad date -> 400, not a crash
    assert logged_in.get("/calendar/jump", query_string={"date": "nope"}).status_code == 400


def test_hide_completed_preference(logged_in):
    import datetime as dt

    from app.models import TaskDate

    done = _create_task(logged_in, title="Finished thing")
    done.due_date = dt.date.today()
    done.done = True
    open_task = _create_task(logged_in, title="Open thing")
    # work-on today but not due today -> shows only in 'Working on today'
    db.session.add(TaskDate(task_id=open_task.id, date=dt.date.today()))
    db.session.commit()
    day_url = f"/calendar/day/{dt.date.today().isoformat()}"

    # default: both visible everywhere
    dash = logged_in.get("/").data.decode()
    assert "Finished thing" in dash and "Open thing" in dash
    cal = logged_in.get(day_url).data.decode()
    assert "Finished thing" in cal
    tasks_page = logged_in.get("/tasks/").data.decode()
    assert "Finished thing" in tasks_page

    # toggle on
    logged_in.post("/preferences/hide-completed", data={"hide": "1", "next": "/"},
                   follow_redirects=True)
    dash = logged_in.get("/").data.decode()
    # regression: the switch must render its ON state (was always unchecked)
    assert 'name="hide" value="1" checked' in dash
    assert "Finished thing" not in dash and "Open thing" in dash
    assert 'Working on today <span class="count">1' in dash

    cal = logged_in.get(day_url).data.decode()
    assert "Finished thing" not in cal and "Open thing" in cal

    tasks_page = logged_in.get("/tasks/").data.decode()
    assert "Finished thing" not in tasks_page

    # toggle back off (unchecked switch posts no 'hide' field)
    logged_in.post("/preferences/hide-completed", data={"next": "/"})
    off = logged_in.get("/").data.decode()
    assert "Finished thing" in off
    assert 'name="hide" value="1" checked' not in off


def test_hide_completed_keeps_done_parents_with_open_children(logged_in):
    parent = _create_task(logged_in, title="Done container")
    parent.done = True
    _create_task(logged_in, title="Still open child", parent=parent.id)
    from app.models import Task

    parent.done = True
    db.session.commit()

    logged_in.post("/preferences/hide-completed", data={"hide": "1", "next": "/"})
    page = logged_in.get("/tasks/").data.decode()
    assert "Still open child" in page          # child survives
    assert "Done container" in page            # kept as its container
    from app.queries import filter_task_tree
    roots = Task.query.filter_by(parent_id=None).all()
    filtered = filter_task_tree(roots, True)
    assert [t.title for t in filtered] == ["Done container"]


def test_task_search(logged_in):
    """JSON typeahead: empty query = all tasks, otherwise filtered by title."""
    _create_task(logged_in, title="Alpha build")
    _create_task(logged_in, title="Beta rollout")
    import json

    all_rows = json.loads(logged_in.get("/tasks/search").data)["results"]
    titles = [r["title"] for r in all_rows]
    assert "Alpha build" in titles and "Beta rollout" in titles

    hit = json.loads(logged_in.get("/tasks/search?q=alpha").data)["results"]
    assert [r["title"] for r in hit] == ["Alpha build"]

    miss = json.loads(logged_in.get("/tasks/search?q=zzz").data)["results"]
    assert miss == []


def test_project_add_task_assigns_existing(logged_in):
    """Picking a task from the typeahead moves it into the project."""
    from app.models import Project, Task

    task = _create_task(logged_in, title="Unassigned thing")
    assert task.project_id is None
    project = Project.query.filter_by(name="Renovation").first()
    if project is None:
        logged_in.post("/projects/new", data={"name": "Renovation", "color": "pink"})
        project = Project.query.filter_by(name="Renovation").first()

    resp = logged_in.post(
        f"/projects/{project.id}/add-task",
        data={"title": task.title, "existing_task_id": task.id},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    db.session.expire_all()
    assert db.session.get(Task, task.id).project_id == project.id

    # assigning it again is a friendly no-op
    resp = logged_in.post(
        f"/projects/{project.id}/add-task",
        data={"title": task.title, "existing_task_id": task.id},
        follow_redirects=True,
    )
    assert "already in this project" in resp.data.decode()


def test_calendar_section_selectors(logged_in):
    """Due / Working on are independent selectable sections; no 'Both' button."""
    import datetime as dt

    from app.models import TaskDate

    task = _create_task(logged_in, title="Selector test")
    task.due_date = dt.date(2026, 3, 3)
    due_feb = _create_task(logged_in, title="Selector due feb")
    due_feb.due_date = dt.date(2026, 2, 20)
    db.session.add(TaskDate(task_id=task.id, date=dt.date(2026, 2, 28)))
    db.session.commit()

    base = "/calendar/month/2026-02"
    # default (no params): both sections
    both = logged_in.get(base).data.decode()
    assert "Due in February" in both and "Working on in February" in both
    assert ">Both<" not in both

    # due only
    due_only = logged_in.get(f"{base}?due=1").data.decode()
    assert "Due in February" in due_only
    assert "Working on in February" not in due_only
    assert "Selector due feb" in due_only and "Selector test" not in due_only

    # work only
    work_only = logged_in.get(f"{base}?work=1").data.decode()
    assert "Working on in February" in work_only
    assert "Due in February" not in work_only
    assert "Selector test" in work_only  # the 28 Feb work-on date shows

    # neither selected -> explicit hint, no sections
    none = logged_in.get(f"{base}?due=0&work=0").data.decode()
    assert "Due in February" not in none and "Working on in February" not in none
    assert 'turn on "Due" and/or "Working on"' in none

    # legacy ?mode= URLs keep working
    legacy = logged_in.get(f"{base}?mode=work").data.decode()
    assert "Working on in February" in legacy and "Due in February" not in legacy


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_production_allows_plain_http_sessions():
    """Regression: secure cookies would break CSRF over plain http://."""
    os.environ.pop("TODO_SECURE_COOKIES", None)
    os.environ["TODO_ADMIN_PASSWORD"] = "test-password"  # keep _ensure_admin deterministic
    from app.config import ProductionConfig
    app = create_app(ProductionConfig)
    assert app.config["SESSION_COOKIE_SECURE"] is False


def test_session_cookie_not_secure_over_http(client):
    client.get("/login")
    # cookie jar must retain the session for the subsequent POST
    resp = client.post(
        "/login", data={"username": "admin", "password": "test-password"},
        follow_redirects=True,
    )
    assert "Invalid" not in resp.data.decode()


# --- security hardening -------------------------------------------------

def test_safe_next_rejects_offsite_targets():
    from app.utils import safe_next

    assert safe_next("/tasks/?status=open", "/") == "/tasks/?status=open"
    assert safe_next(None, "/") == "/"
    for bad in (
        "https://evil.example/phish",
        "//evil.example",
        "/\\evil.example",
        "javascript:alert(1)",
        "/ok\r\nSet-Cookie: x=y",
    ):
        assert safe_next(bad, "/") == "/", bad


def test_login_redirect_uses_local_next(client):
    resp = client.post(
        "/login?next=/tasks/",
        data={"username": "admin", "password": "test-password"},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/tasks/"


def test_login_ignores_offsite_next(client):
    resp = client.post(
        "/login?next=https://evil.example/phish",
        data={"username": "admin", "password": "test-password"},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"


def test_login_lockout_after_repeated_failures(app, client):
    from app.models import User

    app.config["LOGIN_MAX_ATTEMPTS"] = 2
    app.config["LOGIN_LOCK_MINUTES"] = 15
    for _ in range(2):
        client.post("/login", data={"username": "admin", "password": "wrong"})

    user = User.query.filter_by(username="admin").first()
    assert user.is_locked()
    # even the correct password stays locked until the window expires
    resp = client.post(
        "/login", data={"username": "admin", "password": "test-password"}
    )
    assert resp.status_code == 429

    user.clear_failures()
    db.session.commit()
    resp = client.post(
        "/login", data={"username": "admin", "password": "test-password"}
    )
    assert resp.status_code == 302


def test_security_headers_and_html_no_store(client):
    resp = client.get("/login")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
    assert "script-src 'self'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["Cache-Control"] == "no-store"


def test_csrf_required_to_remove_work_date(csrf_client):
    from app.models import Task, TaskDate

    token = _csrf(csrf_client)
    csrf_client.post(
        "/tasks/new",
        data={
            "title": "CSRF task",
            "notes": "",
            "due_date": "",
            "project_id": 0,
            "parent_id": 0,
            "csrf_token": token,
        },
    )
    task = Task.query.filter_by(title="CSRF task").one()
    csrf_client.post(
        f"/tasks/{task.id}/work-dates",
        data={"start": "2026-03-10", "csrf_token": token},
    )
    work_date = TaskDate.query.filter_by(task_id=task.id).one()

    missing = csrf_client.post(
        f"/tasks/{task.id}/work-dates/{work_date.id}/remove"
    )
    assert missing.status_code == 400
    assert db.session.get(TaskDate, work_date.id) is not None

    ok = csrf_client.post(
        f"/tasks/{task.id}/work-dates/{work_date.id}/remove",
        data={"csrf_token": _csrf(csrf_client)},
    )
    assert ok.status_code == 302
    assert db.session.get(TaskDate, work_date.id) is None


def test_notes_api_requires_csrf_and_string(csrf_client, app):
    from app.models import Task

    token = _csrf(csrf_client)
    csrf_client.post(
        "/tasks/new",
        data={
            "title": "Notes task",
            "notes": "",
            "due_date": "",
            "project_id": 0,
            "parent_id": 0,
            "csrf_token": token,
        },
    )
    task = Task.query.filter_by(title="Notes task").one()

    assert csrf_client.post(
        f"/tasks/{task.id}/notes", json={"notes": "hello"}
    ).status_code == 400
    assert csrf_client.post(
        f"/tasks/{task.id}/notes",
        json={"notes": 123},
        headers={"X-CSRFToken": token},
    ).status_code == 400

    app.config["TASK_NOTES_LIMIT"] = 4
    resp = csrf_client.post(
        f"/tasks/{task.id}/notes",
        json={"notes": "abcdef"},
        headers={"X-CSRFToken": token},
    )
    assert resp.status_code == 200
    db.session.expire_all()
    assert db.session.get(Task, task.id).notes == "abcd"


# --- feature behaviour --------------------------------------------------

def test_recurring_task_spawns_next_occurrence_once(logged_in):
    import datetime as dt

    from app.models import Task

    task = _create_task(logged_in, title="Water plants", due="2026-03-03")
    logged_in.post(
        f"/tasks/{task.id}/edit",
        data={
            "title": task.title,
            "notes": "",
            "due_date": "2026-03-03",
            "recurrence": "daily",
            "project_id": 0,
            "parent_id": 0,
        },
    )
    db.session.expire_all()
    task = db.session.get(Task, task.id)
    assert task.recurrence == "daily"

    logged_in.post(f"/tasks/{task.id}/toggle")
    clones = Task.query.filter_by(recurrence_source_id=task.id).all()
    assert len(clones) == 1
    assert clones[0].due_date == dt.date(2026, 3, 4)
    assert clones[0].recurrence == "daily"

    # toggle off and on again: still exactly one future occurrence
    logged_in.post(f"/tasks/{task.id}/toggle")
    logged_in.post(f"/tasks/{task.id}/toggle")
    assert Task.query.filter_by(recurrence_source_id=task.id).count() == 1


def test_recurrence_requires_due_date(logged_in):
    resp = logged_in.post(
        "/tasks/new",
        data={
            "title": "No due repeat",
            "notes": "",
            "due_date": "",
            "recurrence": "weekly",
            "project_id": 0,
            "parent_id": 0,
        },
    )
    assert resp.status_code == 200
    assert "Give the task a due date to repeat it" in resp.data.decode()


def test_delete_moves_subtree_to_trash_and_restores_it(logged_in):
    import datetime as dt

    from app.models import DeletedBatch, Task, TaskDate

    parent = _create_task(logged_in, title="Trash parent")
    child = _create_task(logged_in, title="Trash child", parent=parent.id)
    db.session.add(TaskDate(task_id=child.id, date=dt.date(2026, 4, 1)))
    db.session.commit()
    parent_id, child_id = parent.id, child.id

    resp = logged_in.post(f"/tasks/{parent_id}/delete", follow_redirects=True)
    body = resp.data.decode()
    assert "Deleted" in body and "Undo" in body
    db.session.expire_all()
    assert db.session.get(Task, parent_id) is None
    assert db.session.get(Task, child_id) is None

    batch = DeletedBatch.query.one()
    assert "Trash parent" in logged_in.get("/tasks/trash").data.decode()

    resp = logged_in.post(f"/tasks/trash/{batch.id}/restore", follow_redirects=True)
    assert resp.status_code == 200
    db.session.expire_all()
    restored_parent = Task.query.filter_by(title="Trash parent").one()
    restored_child = Task.query.filter_by(title="Trash child").one()
    assert restored_child.parent_id == restored_parent.id
    assert [td.date for td in restored_child.work_dates] == [dt.date(2026, 4, 1)]
    assert DeletedBatch.query.count() == 0


def test_empty_trash(logged_in):
    from app.models import DeletedBatch, Task

    task = _create_task(logged_in, title="Throwaway")
    logged_in.post(f"/tasks/{task.id}/delete")
    assert DeletedBatch.query.count() == 1
    logged_in.post("/tasks/trash/empty")
    assert DeletedBatch.query.count() == 0
    assert Task.query.filter_by(title="Throwaway").first() is None


def test_drag_reorder_moves_sibling(logged_in):
    from app.models import Task

    first = _create_task(logged_in, title="First")
    _create_task(logged_in, title="Second")
    third = _create_task(logged_in, title="Third")

    resp = logged_in.post(
        f"/tasks/{third.id}/reorder",
        json={"target_id": first.id, "position": "after"},
    )
    assert resp.status_code == 200 and resp.get_json()["ok"] is True
    db.session.expire_all()
    order = [
        t.title
        for t in Task.query.filter_by(parent_id=None).order_by(
            Task.position, Task.id
        )
    ]
    assert order == ["First", "Third", "Second"]

    child = _create_task(logged_in, title="First child", parent=first.id)
    resp = logged_in.post(
        f"/tasks/{first.id}/reorder",
        json={"target_id": child.id, "position": "before"},
    )
    assert resp.status_code == 400


def test_bulk_subtree_actions(logged_in):
    import datetime as dt

    from app.models import Task

    parent = _create_task(logged_in, title="Bulk parent")
    child = _create_task(logged_in, title="Bulk child", parent=parent.id)
    parent_id, child_id = parent.id, child.id

    logged_in.post(f"/tasks/{parent_id}/complete-subtree")
    db.session.expire_all()
    assert db.session.get(Task, parent_id).done
    assert db.session.get(Task, child_id).done

    logged_in.post(
        f"/tasks/{parent_id}/reschedule", data={"due_date": "2026-05-01"}
    )
    db.session.expire_all()
    assert db.session.get(Task, parent_id).due_date == dt.date(2026, 5, 1)
    assert db.session.get(Task, child_id).due_date == dt.date(2026, 5, 1)


def test_calendar_selection_persists_across_navigation(logged_in):
    page = logged_in.get("/calendar/day/2026-03-03?due=0&work=1").data.decode()
    assert "due=0&amp;work=1" in page

    resp = logged_in.get(
        "/calendar/jump",
        query_string={"date": "2026-03-03", "due": "0", "work": "1"},
    )
    assert resp.status_code == 302
    assert "due=0" in resp.headers["Location"]
    assert "work=1" in resp.headers["Location"]


def test_scheduled_page_hides_completed_tasks(logged_in):
    import datetime as dt

    open_task = _create_task(logged_in, title="Overdue open")
    open_task.due_date = dt.date(2020, 1, 1)
    done_task = _create_task(logged_in, title="Overdue done")
    done_task.due_date = dt.date(2020, 1, 2)
    done_task.done = True
    db.session.commit()

    page = logged_in.get("/tasks/scheduled").data.decode()
    assert "Overdue open" in page
    assert "Overdue done" not in page


def test_task_index_query_count_stays_bounded(logged_in):
    """Rendering a full tree must not issue a query per card."""
    from sqlalchemy import event

    from app.models import Task, TaskDate

    for i in range(6):
        root = Task(title=f"Root {i}", position=i)
        db.session.add(root)
        db.session.flush()
        for j in range(3):
            child = Task(title=f"Child {i}-{j}", parent_id=root.id, position=j)
            db.session.add(child)
            db.session.flush()
            db.session.add(TaskDate(task_id=child.id, date=__import__("datetime").date(2026, 3, 1)))
    db.session.commit()

    statements = []

    def count_selects(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)

    event.listen(db.engine, "before_cursor_execute", count_selects)
    try:
        resp = logged_in.get("/tasks/")
        assert resp.status_code == 200
    finally:
        event.remove(db.engine, "before_cursor_execute", count_selects)

    assert len(statements) <= 15, f"{len(statements)} SELECTs: {statements}"


def test_done_filter_overrides_hide_completed(logged_in):
    task = _create_task(logged_in, title="Explicitly done")
    task.done = True
    db.session.commit()
    logged_in.post("/preferences/hide-completed", data={"hide": "1", "next": "/tasks/"})

    page = logged_in.get("/tasks/?status=done").data.decode()
    assert "Explicitly done" in page

    # The preference still applies to the unfiltered list.
    all_page = logged_in.get("/tasks/").data.decode()
    assert "Explicitly done" not in all_page


def test_bulk_complete_spawns_recurring_tasks(logged_in):
    import datetime as dt

    from app.models import Task

    parent = _create_task(logged_in, title="Bulk repeat", due="2026-06-01")
    logged_in.post(
        f"/tasks/{parent.id}/edit",
        data={
            "title": parent.title,
            "notes": "",
            "due_date": "2026-06-01",
            "recurrence": "weekly",
            "project_id": 0,
            "parent_id": 0,
        },
    )
    db.session.expire_all()
    parent = db.session.get(Task, parent.id)

    logged_in.post(f"/tasks/{parent.id}/complete-subtree")
    clone = Task.query.filter_by(recurrence_source_id=parent.id).one()
    assert clone.due_date == dt.date(2026, 6, 8)


def test_project_move_promotes_picked_subtask(logged_in):
    from app.models import Project, Task

    logged_in.post("/projects/new", data={"name": "Promote", "color": "blue"})
    project = Project.query.filter_by(name="Promote").one()
    parent = _create_task(logged_in, title="Parent elsewhere")
    child = _create_task(logged_in, title="Picked child", parent=parent.id)
    grandchild = _create_task(logged_in, title="Picked grandchild", parent=child.id)
    child_id, grandchild_id = child.id, grandchild.id

    logged_in.post(
        f"/projects/{project.id}/add-task",
        data={"title": child.title, "existing_task_id": child.id},
        follow_redirects=True,
    )
    db.session.expire_all()
    moved = db.session.get(Task, child_id)
    assert moved.project_id == project.id
    assert moved.parent_id is None  # promoted to a project root
    assert db.session.get(Task, grandchild_id).project_id == project.id


def test_notes_accepts_form_beacon_payload(csrf_client):
    """The page-hide beacon sends FormData because it cannot set a header."""
    from app.models import Task

    token = _csrf(csrf_client)
    csrf_client.post(
        "/tasks/new",
        data={
            "title": "Beacon task",
            "notes": "",
            "due_date": "",
            "project_id": 0,
            "parent_id": 0,
            "csrf_token": token,
        },
    )
    task = Task.query.filter_by(title="Beacon task").one()
    resp = csrf_client.post(
        f"/tasks/{task.id}/notes",
        data={"notes": "last edit", "csrf_token": token},
    )
    assert resp.status_code == 200
    db.session.expire_all()
    assert db.session.get(Task, task.id).notes == "last edit"


def test_notes_rejects_bad_csrf_without_global_guard(app, logged_in):
    """A bad token must be a 400 even if CSRFProtect is not intercepting."""
    app.config["WTF_CSRF_ENABLED"] = False
    task = _create_task(logged_in, title="Bad token task")
    resp = logged_in.post(
        f"/tasks/{task.id}/notes",
        json={"notes": "nope", "csrf_token": "not-a-token"},
    )
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "bad csrf"


def test_month_view_renders_overview_grid(logged_in):
    _create_task(logged_in, title="Grid task", due="2026-03-03")
    page = logged_in.get("/calendar/month/2026-03?due=1&work=0").data.decode()
    assert 'class="month-grid"' in page
    assert "Grid task" in page
    assert "/calendar/day/2026-03-03?due=1&amp;work=0" in page
    assert "month-cell" in page
    # the grid does not leak into non-month views
    day = logged_in.get("/calendar/day/2026-03-03?due=1&work=0").data.decode()
    assert 'class="month-grid"' not in day


def test_pwa_manifest_and_service_worker(client):
    import json

    worker = client.get("/sw.js")
    assert worker.status_code == 200
    assert "javascript" in worker.headers["Content-Type"]
    assert worker.headers["Service-Worker-Allowed"] == "/"
    assert "todo-shell" in worker.data.decode()

    manifest = client.get("/static/manifest.webmanifest")
    assert manifest.status_code == 200
    assert json.loads(manifest.data)["display"] == "standalone"
    assert 'rel="manifest"' in client.get("/login").data.decode()


def test_timezone_setting_drives_today(app):
    import datetime as dt
    from zoneinfo import ZoneInfo

    from app import periods

    app.config["TIMEZONE"] = "Pacific/Kiritimati"
    expected = dt.datetime.now(ZoneInfo("Pacific/Kiritimati")).date()
    assert periods.today() == expected
    app.config["TIMEZONE"] = "UTC"
