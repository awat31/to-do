import os

from flask import g

# Force test determinism regardless of .env / shell environment
os.environ["FLASK_CONFIG"] = "testing"
os.environ["TODO_ADMIN_PASSWORD"] = "test-password"

import pytest

from app import create_app, db
from app.periods import BadPeriod, period, period_value


@pytest.fixture()
def app():
    application = create_app()
    @application.teardown_request
    def clear_cached_login_user(_error=None):
        # This fixture keeps an app context alive across multiple test requests.
        g.pop("_login_user", None)

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


def test_logout_clears_remember_cookie(client):
    """Logging out must not let the remember-me cookie sign the user back in."""
    resp = client.post(
        "/login",
        data={"username": "admin", "password": "test-password", "remember": "y"},
    )
    assert resp.status_code == 302
    assert client.get_cookie("remember_token") is not None

    resp = client.post("/logout")
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/login"
    assert client.get_cookie("remember_token") is None

    # The login page itself must not bounce an authenticated-looking user
    # straight back to the dashboard.
    login = client.get("/login")
    assert login.status_code == 200
    assert "Log in" in login.data.decode()
    assert client.get("/").status_code == 302


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

    feb = logged_in.get("/calendar/month/2026-02?layout=list").data.decode()
    mar = logged_in.get("/calendar/month/2026-03?layout=list").data.decode()
    year_work = logged_in.get("/calendar/year/2026?work=1&layout=list").data.decode()
    year_due = logged_in.get("/calendar/year/2026?due=1&layout=list").data.decode()

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


def test_work_date_range_uses_configured_limit(app, logged_in):
    task = _create_task(logged_in, title="Limited range")
    app.config["MAX_WORK_RANGE_DAYS"] = 2

    accepted = logged_in.post(
        f"/tasks/{task.id}/work-dates",
        data={"start": "2026-03-10", "end": "2026-03-11"},
        follow_redirects=True,
    )
    assert accepted.status_code == 200
    assert "Added 2 work dates." in accepted.data.decode()

    rejected = logged_in.post(
        f"/tasks/{task.id}/work-dates",
        data={"start": "2026-03-10", "end": "2026-03-12"},
        follow_redirects=True,
    )
    assert rejected.status_code == 200
    assert "Ranges are limited to 2 days." in rejected.data.decode()


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
        page = logged_in.get(f"/calendar/{kind}/{value}?layout=list").data.decode()
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


def test_project_edit_page_renders_and_saves(logged_in):
    from app.models import Project

    logged_in.post("/projects/new", data={"name": "Renovation", "color": "pink"})
    project = Project.query.filter_by(name="Renovation").one()

    page = logged_in.get(f"/projects/{project.id}/edit")
    assert page.status_code == 200
    assert "Edit project" in page.data.decode()

    response = logged_in.post(
        f"/projects/{project.id}/edit",
        data={"name": "Kitchen", "color": "blue"},
    )
    assert response.status_code == 302
    db.session.refresh(project)
    assert project.name == "Kitchen"
    assert project.color == "blue"


def test_task_detail_shows_project_before_due_date_and_labels_subtree_action(logged_in):
    from app.models import Project

    logged_in.post("/projects/new", data={"name": "Project before due", "color": "pink"})
    project = Project.query.filter_by(name="Project before due").one()
    task = _create_task(logged_in, title="Task with project", project=project.id)

    page = logged_in.get(f"/tasks/{task.id}").data.decode()

    assert page.index('<span class="detail-key">Project</span>') < page.index(
        '<span class="detail-key">Due date</span>'
    )
    assert "Project before due" in page
    assert "Change project" in page
    assert "Set due date" in page
    assert "Mark whole subtree complete" in page
    assert "Move subtree" not in page


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


def test_dashboard_does_not_show_unscheduled_alert(logged_in):
    _create_task(logged_in, title="Unscheduled task")

    page = logged_in.get("/").data.decode()

    assert "open, unscheduled" not in page


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


def test_admin_password_change_survives_restart(app):
    """A password changed in the app must not be reset from .env on restart."""
    from app import _ensure_admin
    from app.models import User

    user = User.query.filter_by(username="admin").first()
    user.set_password("changed-in-app")
    db.session.commit()

    _ensure_admin(app)
    db.session.expire_all()
    user = User.query.filter_by(username="admin").first()
    assert user.check_password("changed-in-app")
    assert not user.check_password("test-password")

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


def test_task_index_hides_done_parents_with_open_children(logged_in):
    parent = _create_task(logged_in, title="Done container")
    parent.done = True
    _create_task(logged_in, title="Still open child", parent=parent.id)
    from app.models import Task

    parent.done = True
    db.session.commit()

    logged_in.post("/preferences/hide-completed", data={"hide": "1", "next": "/"})
    page = logged_in.get("/tasks/").data.decode()
    assert "Still open child" not in page
    assert "Done container" not in page
    from app.queries import filter_task_tree
    roots = Task.query.filter_by(parent_id=None).all()
    filtered = filter_task_tree(roots, True)
    assert [t.title for t in filtered] == ["Done container"]


def test_hide_completed_done_leaf_child_does_not_crash(logged_in):
    """Pruning a completed leaf must not break its open parent's tree."""
    parent = _create_task(logged_in, title="Open parent")
    child = _create_task(logged_in, title="Done leaf", parent=parent.id)
    child.done = True
    db.session.commit()

    logged_in.post("/preferences/hide-completed", data={"hide": "1", "next": "/"})
    resp = logged_in.get("/tasks/")
    assert resp.status_code == 200
    page = resp.data.decode()
    assert "Open parent" in page
    assert "Done leaf" not in page


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


def test_task_search_reports_when_results_are_capped(logged_in):
    import json

    from app.models import Task, User

    user_id = User.query.filter_by(username="admin").one().id
    db.session.add_all(
        [Task(user_id=user_id, title=f"Search cap {i:02}") for i in range(51)]
    )
    db.session.commit()

    response = json.loads(logged_in.get("/tasks/search").data)
    assert len(response["results"]) == 50
    assert response["capped"] is True


def test_reassign_rejects_malformed_project_id(logged_in):
    from app.models import Project

    logged_in.post("/projects/new", data={"name": "Assigned", "color": "blue"})
    project = Project.query.filter_by(name="Assigned").one()
    task = _create_task(logged_in, title="Keep assigned", project=project.id)

    response = logged_in.post(
        f"/tasks/{task.id}/reassign",
        data={"project_id": "not-an-id"},
    )
    assert response.status_code == 400
    db.session.refresh(task)
    assert task.project_id == project.id


def test_clearing_due_date_disables_recurrence(logged_in):
    task = _create_task(logged_in, title="Repeating task", due="2026-03-10")
    task.recurrence = "weekly"
    db.session.commit()

    response = logged_in.post(f"/tasks/{task.id}/due", data={"due_date": ""})
    assert response.status_code == 302
    db.session.refresh(task)
    assert task.due_date is None
    assert task.recurrence == "none"


def test_clearing_subtree_due_dates_disables_recurrence(logged_in):
    parent = _create_task(logged_in, title="Repeating parent", due="2026-03-10")
    child = _create_task(
        logged_in, title="Repeating child", due="2026-03-11", parent=parent.id
    )
    parent.recurrence = "weekly"
    child.recurrence = "monthly"
    db.session.commit()

    response = logged_in.post(
        f"/tasks/{parent.id}/reschedule", data={"due_date": ""}
    )
    assert response.status_code == 302
    db.session.refresh(parent)
    db.session.refresh(child)
    assert parent.due_date is None and parent.recurrence == "none"
    assert child.due_date is None and child.recurrence == "none"


def test_descendant_query_terminates_on_corrupt_cycle(logged_in):
    from app.models import Task

    first = _create_task(logged_in, title="Cycle first")
    second = _create_task(logged_in, title="Cycle second", parent=first.id)
    first.parent_id = second.id
    db.session.commit()

    assert first.descendant_ids() == [second.id]
    assert second.descendant_ids() == [first.id]
    assert logged_in.post(f"/tasks/{first.id}/delete").status_code == 302


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
    # Default section selection, explicitly using the list layout.
    both = logged_in.get(f"{base}?layout=list").data.decode()
    assert "Due in February" in both and "Working on in February" in both
    assert ">Both<" not in both

    # due only
    due_only = logged_in.get(f"{base}?due=1&layout=list").data.decode()
    assert "Due in February" in due_only
    assert "Working on in February" not in due_only
    assert "Selector due feb" in due_only and "Selector test" not in due_only

    # work only
    work_only = logged_in.get(f"{base}?work=1&layout=list").data.decode()
    assert "Working on in February" in work_only
    assert "Due in February" not in work_only
    assert "Selector test" in work_only  # the 28 Feb work-on date shows

    # neither selected -> explicit hint, no sections
    none = logged_in.get(f"{base}?due=0&work=0&layout=list").data.decode()
    assert "Due in February" not in none and "Working on in February" not in none
    assert 'turn on "Due" and/or "Working on"' in none

    # legacy ?mode= URLs keep working
    legacy = logged_in.get(f"{base}?mode=work&layout=list").data.decode()
    assert "Working on in February" in legacy and "Due in February" not in legacy


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200


def test_production_allows_plain_http_sessions(monkeypatch, tmp_path):
    """Regression: secure cookies would break CSRF over plain http://."""
    monkeypatch.delenv("TODO_SECURE_COOKIES", raising=False)
    from app.config import ProductionConfig, _env_bool

    class IsolatedProductionConfig(ProductionConfig):
        SECRET_KEY = "test-production-config-key"
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'production-config.db'}"
        SESSION_COOKIE_SECURE = _env_bool("TODO_SECURE_COOKIES")
        REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE

    app = create_app(IsolatedProductionConfig)
    assert app.config["SESSION_COOKIE_SECURE"] is False


def test_configuration_rejects_invalid_values(monkeypatch):
    from app.config import _env_bool, _env_int, get_config_class

    monkeypatch.setenv("TEST_POSITIVE_INT", "-2")
    with pytest.raises(RuntimeError, match="positive integer"):
        _env_int("TEST_POSITIVE_INT", 3)

    monkeypatch.setenv("TEST_BOOLEAN", "maybe")
    with pytest.raises(RuntimeError, match="boolean"):
        _env_bool("TEST_BOOLEAN")

    monkeypatch.setenv("FLASK_CONFIG", "typo")
    with pytest.raises(RuntimeError, match="FLASK_CONFIG"):
        get_config_class()


def test_session_cookie_not_secure_over_http(client):
    client.get("/login")
    # cookie jar must retain the session for the subsequent POST
    resp = client.post(
        "/login", data={"username": "admin", "password": "test-password"},
        follow_redirects=True,
    )
    assert "Invalid" not in resp.data.decode()


def test_gunicorn_optional_tls_certificate_pair(monkeypatch, tmp_path):
    from pathlib import Path
    from runpy import run_path

    config_path = Path(__file__).resolve().parents[1] / "gunicorn.conf.py"
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TODO_SSL_CERTFILE", raising=False)
    monkeypatch.delenv("TODO_SSL_KEYFILE", raising=False)
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)

    http_config = run_path(str(config_path))
    assert http_config["certfile"] is None
    assert http_config["keyfile"] is None
    assert http_config["forwarded_allow_ips"] == "127.0.0.1,::1"

    monkeypatch.setenv("TODO_SSL_CERTFILE", "/certs/fullchain.pem")
    monkeypatch.setenv("TODO_SSL_KEYFILE", "/certs/private-key.pem")
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.1,10.0.0.4")
    https_config = run_path(str(config_path))
    assert https_config["certfile"] == "/certs/fullchain.pem"
    assert https_config["keyfile"] == "/certs/private-key.pem"
    assert https_config["forwarded_allow_ips"] == "127.0.0.1,10.0.0.4"

    monkeypatch.delenv("TODO_SSL_KEYFILE")
    with pytest.raises(RuntimeError, match="Set both TODO_SSL_CERTFILE"):
        run_path(str(config_path))


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


def test_login_ip_rate_limit(app, client):
    """Excess logins from one IP are throttled with 429 before any DB work."""
    from app import auth as auth_module

    app.config["LOGIN_IP_LIMIT"] = 3
    app.config["LOGIN_IP_WINDOW_MINUTES"] = 15
    auth_module._login_limiter.reset("login:127.0.0.1")
    try:
        for _ in range(3):
            resp = client.post("/login", data={"username": "admin", "password": "wrong"})
            assert resp.status_code == 200
        resp = client.post(
            "/login", data={"username": "admin", "password": "test-password"}
        )
        assert resp.status_code == 429
    finally:
        auth_module._login_limiter.reset("login:127.0.0.1")


def test_signup_ip_rate_limit(app, client):
    from app import auth as auth_module

    app.config["SIGNUP_IP_LIMIT"] = 2
    app.config["SIGNUP_IP_WINDOW_HOURS"] = 1
    auth_module._signup_limiter.reset("signup:127.0.0.1")
    try:
        for i in range(2):
            # Each signup uses a fresh client: after signup the client is
            # authenticated, and an authenticated visitor is redirected
            # before the rate limiter is consulted.
            resp = app.test_client().post(
                "/signup",
                data={"username": f"ratelimit{i}", "password": "longenough1"},
                follow_redirects=True,
            )
            assert resp.status_code == 200
        resp = app.test_client().post(
            "/signup", data={"username": "ratelimit3", "password": "longenough1"}
        )
        assert resp.status_code == 429
    finally:
        auth_module._signup_limiter.reset("signup:127.0.0.1")


def test_password_change_invalidates_other_sessions(app):
    """A session minted before a password change is rejected afterwards."""
    from app.models import User, db

    user = User(username="pwc", display_name="pwc")
    user.set_password("old-password-1")
    db.session.add(user)
    db.session.commit()

    browser_a = app.test_client()
    browser_b = app.test_client()
    for browser in (browser_a, browser_b):
        resp = browser.post(
            "/login", data={"username": "pwc", "password": "old-password-1"}
        )
        assert resp.status_code == 302

    # Browser A changes the password.
    resp = browser_a.post(
        "/account",
        data={
            "current_password": "old-password-1",
            "new_password": "new-password-1",
            "confirm_password": "new-password-1",
        },
        follow_redirects=True,
    )
    assert "Password changed." in resp.data.decode()

    # Browser B's older session is torn down and redirected to login.
    resp = browser_b.get("/")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]

    # Browser A remains signed in (it was re-stamped).
    assert browser_a.get("/").status_code == 200

    # The old password no longer works; the new one does.
    fresh = app.test_client()
    resp = fresh.post(
        "/login", data={"username": "pwc", "password": "old-password-1"}
    )
    assert "Invalid username or password" in resp.data.decode()
    resp = fresh.post(
        "/login", data={"username": "pwc", "password": "new-password-1"},
        follow_redirects=True,
    )
    assert resp.status_code == 200


def test_rate_limiter_unit():
    from app.ratelimit import SlidingWindowLimiter

    limiter = SlidingWindowLimiter(limit=2, window_seconds=60)
    assert limiter.check("k") is True
    assert limiter.check("k") is True
    assert limiter.check("k") is False  # over the limit
    limiter.reset("k")
    assert limiter.check("k") is True  # reset clears history
    assert limiter.check("other") is True  # keys are independent


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
    assert '<div class="undo-bar">' in body and "Undo" in body
    assert "You can undo this from Trash." not in body
    assert '<div class="flashes">' not in body
    refreshed = logged_in.get("/tasks/").data.decode()
    assert '<div class="undo-bar">' not in refreshed
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


def test_purging_trash_shows_task_deleted_message(logged_in):
    from app.models import DeletedBatch

    task = _create_task(logged_in, title="Permanent delete")
    logged_in.post(f"/tasks/{task.id}/delete")
    batch = DeletedBatch.query.one()

    response = logged_in.post(
        f"/tasks/trash/{batch.id}/purge", follow_redirects=True
    )
    body = response.data.decode()

    assert "Task deleted permanently" in body
    assert "Deleted batch removed permanently" not in body
    assert DeletedBatch.query.count() == 0


def test_task_index_shows_top_level_tasks_and_sorts_by_due_date(logged_in):
    _create_task(logged_in, title="Due later", due="2026-03-20")
    _create_task(logged_in, title="Due earlier", due="2026-03-01")
    _create_task(logged_in, title="No due date")
    parent = _create_task(logged_in, title="Unscheduled parent")
    _create_task(logged_in, title="Child due later", due="2026-03-10", parent=parent.id)
    _create_task(logged_in, title="Child due earlier", due="2026-03-05", parent=parent.id)

    page = logged_in.get("/tasks/?sort=due").data.decode()
    assert page.index("Due earlier") < page.index("Due later")
    assert page.index("Due later") < page.index("No due date")
    assert "Child due earlier" not in page
    assert "Child due later" not in page
    assert 'name="sort"' in page
    assert '<option value="due" selected>' in page

    filtered = logged_in.get(
        "/tasks/?q=Due%20later&status=open&sort=due"
    ).data.decode()
    assert "Due later" in filtered and "Due earlier" not in filtered
    assert "No due date" not in filtered
    assert 'name="q" value="Due later"' in filtered
    assert '<option value="open" selected>' in filtered
    assert '<option value="due" selected>' in filtered


def test_task_index_hides_subtasks_and_places_completed_roots_last(logged_in):
    parent = _create_task(logged_in, title="Open root")
    _create_task(logged_in, title="Hidden child", parent=parent.id)
    completed = _create_task(logged_in, title="Done root")
    completed.done = True
    db.session.commit()

    page = logged_in.get("/tasks/").data.decode()
    assert "Hidden child" not in page
    assert page.index("Open root") < page.index("Done root")


def test_task_index_can_move_task_to_trash(logged_in):
    from app.models import DeletedBatch, Task

    task = _create_task(logged_in, title="Task from list")
    child = _create_task(logged_in, title="Child from list", parent=task.id)
    task_id, child_id = task.id, child.id
    page = logged_in.get("/tasks/?sort=project").data.decode()

    assert f'action="/tasks/{task_id}/delete"' in page
    assert 'aria-label="Move task to Trash"' in page
    assert "Move this task and all of its sub-tasks to Trash?" in page

    response = logged_in.post(
        f"/tasks/{task_id}/delete",
        data={"next": "/tasks/?sort=project"},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/tasks/?sort=project"
    assert db.session.get(Task, task_id) is None
    assert db.session.get(Task, child_id) is None
    assert DeletedBatch.query.one().summary == "Task from list + 1 sub-task(s)"


def test_completing_task_from_tasks_page_returns_to_tasks_page(logged_in):
    task = _create_task(logged_in, title="Complete from task list")
    page = logged_in.get("/tasks/?sort=project").data.decode()

    assert 'name="next" value="/tasks/?sort=project"' in page
    response = logged_in.post(
        f"/tasks/{task.id}/toggle",
        data={"next": "/tasks/?sort=project"},
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/tasks/?sort=project"
    db.session.refresh(task)
    assert task.done


@pytest.mark.parametrize("hide_completed", [False, True])
def test_toggling_task_from_project_returns_to_project(logged_in, hide_completed):
    from app.models import Project

    logged_in.post("/projects/new", data={"name": "Task project", "color": "blue"})
    project = Project.query.filter_by(name="Task project").one()
    task = _create_task(logged_in, title="Project task", project=project.id)
    project_url = f"/projects/{project.id}"
    if hide_completed:
        logged_in.post("/preferences/hide-completed", data={"hide": "1", "next": project_url})

    page = logged_in.get(project_url).data.decode()
    toggle_form = page.split(f'action="/tasks/{task.id}/toggle"', 1)[1].split("</form>", 1)[0]
    assert f'name="next" value="{project_url}"' in toggle_form

    response = logged_in.post(f"/tasks/{task.id}/toggle", data={"next": project_url})
    assert response.status_code == 302
    assert response.headers["Location"] == project_url
    db.session.refresh(task)
    assert task.done

    page = logged_in.get(project_url).data.decode()
    if hide_completed:
        assert "Project task" not in page
    else:
        assert 'class="task-card is-done"' in page
        assert f'action="/tasks/{task.id}/toggle"' in page
        response = logged_in.post(f"/tasks/{task.id}/toggle", data={"next": project_url})
        assert response.headers["Location"] == project_url
        db.session.refresh(task)
        assert not task.done


def test_task_index_can_sort_by_project(logged_in):
    from app.models import Project

    logged_in.post("/projects/new", data={"name": "Zulu project", "color": "blue"})
    zulu = Project.query.filter_by(name="Zulu project").one()
    logged_in.post("/projects/new", data={"name": "Alpha project", "color": "blue"})
    alpha = Project.query.filter_by(name="Alpha project").one()
    _create_task(logged_in, title="Zulu task", project=zulu.id)
    _create_task(logged_in, title="Alpha task", project=alpha.id)
    _create_task(logged_in, title="Unassigned task")

    page = logged_in.get("/tasks/?sort=project").data.decode()
    assert page.index("Alpha task") < page.index("Zulu task")
    assert page.index("Zulu task") < page.index("Unassigned task")
    assert '<option value="project" selected>' in page


def test_project_order_can_be_saved(logged_in):
    from app.models import Project

    for name in ("First", "Second", "Third"):
        logged_in.post("/projects/new", data={"name": name, "color": "blue"})
    projects = {
        project.name: project.id for project in Project.query.all()
    }

    response = logged_in.post(
        "/projects/reorder",
        json={"project_ids": [projects["Third"], projects["First"], projects["Second"]]},
    )
    assert response.status_code == 200
    assert [project.name for project in Project.query.order_by(Project.position)] == [
        "Third", "First", "Second"
    ]
    page = logged_in.get("/projects/").data.decode()
    assert [
        page.index(f'href="/projects/{projects[name]}"')
        for name in ("Third", "First", "Second")
    ] == sorted(
        page.index(f'href="/projects/{projects[name]}"')
        for name in ("Third", "First", "Second")
    )


def test_subtask_add_returns_focus_to_add_field(logged_in):
    parent = _create_task(logged_in, title="Parent for quick add")
    response = logged_in.post(
        f"/tasks/{parent.id}/subtasks",
        data={"title": "Quick child"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("#add-subtask")


def test_completing_subtask_returns_to_parent(logged_in):
    from app.models import Task

    parent = _create_task(logged_in, title="Parent staying open")
    child = _create_task(logged_in, title="Child to complete", parent=parent.id)
    response = logged_in.post(f"/tasks/{child.id}/toggle")
    assert response.status_code == 302
    assert response.headers["Location"] == f"/tasks/{parent.id}"
    db.session.refresh(child)
    assert child.done


def test_task_index_keeps_default_order_when_due_sort_is_not_selected(logged_in):
    task = _create_task(logged_in, title="Later task", due="2026-03-20")
    _create_task(logged_in, title="Earlier task", due="2026-03-01")

    page = logged_in.get("/tasks/").data.decode()
    assert page.index("Earlier task") < page.index("Later task")
    assert '<option value="default" selected>' in page
    assert "drag-handle" not in page and 'draggable="true"' not in page
    assert logged_in.post(f"/tasks/{task.id}/reorder").status_code == 404


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

    from app.models import Task, TaskDate, User

    admin_id = User.query.filter_by(username="admin").one().id
    for i in range(6):
        root = Task(user_id=admin_id, title=f"Root {i}", position=i)
        db.session.add(root)
        db.session.flush()
        for j in range(3):
            child = Task(
                user_id=admin_id,
                title=f"Child {i}-{j}",
                parent_id=root.id,
                position=j,
            )
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
    page = logged_in.get(
        "/calendar/month/2026-03?due=1&work=0&layout=calendar"
    ).data.decode()
    assert 'class="month-grid"' in page
    assert "Grid task" in page
    assert "/calendar/day/2026-03-03?due=1&amp;work=0" in page
    assert "month-cell" in page
    # the grid does not leak into non-month views
    day = logged_in.get("/calendar/day/2026-03-03?due=1&work=0").data.decode()
    assert 'class="month-grid"' not in day


def test_week_month_year_calendar_layouts_toggle_and_link(logged_in):
    _create_task(logged_in, title="Calendar task", due="2026-03-03")

    month = logged_in.get(
        "/calendar/month/2026-03?due=1&work=0&layout=calendar"
    ).data.decode()
    assert 'class="month-grid"' in month
    assert "class=\"section\"" not in month

    month_list = logged_in.get(
        "/calendar/month/2026-03?due=1&work=0&layout=list"
    ).data.decode()
    assert 'class="month-grid"' not in month_list
    assert "Calendar task" in month_list
    assert "layout=calendar" in month_list

    week = logged_in.get(
        "/calendar/week/2026-W10?due=1&work=0&layout=calendar"
    ).data.decode()
    assert 'class="week-grid"' in week
    assert "Calendar task" in week
    assert "/calendar/day/2026-03-03?due=1&amp;work=0" in week

    week_list = logged_in.get(
        "/calendar/week/2026-W10?due=1&work=0&layout=list"
    ).data.decode()
    assert 'class="week-grid"' not in week_list
    assert "Calendar task" in week_list

    year = logged_in.get(
        "/calendar/year/2026?due=1&work=0&layout=calendar"
    ).data.decode()
    assert year.count('class="year-month"') == 12
    assert "/calendar/month/2026-03?due=1&amp;work=0" in year
    assert "year-mark-due" in year

    year_list = logged_in.get(
        "/calendar/year/2026?due=1&work=0&layout=list"
    ).data.decode()
    assert 'class="year-calendar"' not in year_list
    assert "Calendar task" in year_list


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


def test_create_app_rejects_invalid_timezone():
    class InvalidTimezoneConfig:
        TIMEZONE = "Not/A_Real_Zone"

    with pytest.raises(RuntimeError, match="TIMEZONE"):
        create_app(InvalidTimezoneConfig)


def test_instance_secret_key_is_persistent_and_private(tmp_path):
    import stat

    from flask import Flask
    from app import _instance_secret_key

    first_app = Flask("first", instance_path=str(tmp_path))
    first_key = _instance_secret_key(first_app)
    second_app = Flask("second", instance_path=str(tmp_path))

    assert _instance_secret_key(second_app) == first_key
    assert stat.S_IMODE((tmp_path / "secret_key").stat().st_mode) == 0o600


def test_instance_directory_is_private(tmp_path):
    import stat

    from flask import Flask
    from app import _secure_instance_dir

    instance_dir = tmp_path / "instance"
    instance_dir.mkdir(mode=0o755)
    os.chmod(instance_dir, 0o755)
    app = Flask("instance", instance_path=str(instance_dir))

    assert _secure_instance_dir(app) == instance_dir
    assert stat.S_IMODE(instance_dir.stat().st_mode) == 0o700


def test_asset_version_tracks_content_changes(tmp_path):
    from flask import Flask
    from app import _asset_version

    app = Flask("assets", static_folder=str(tmp_path))
    asset = tmp_path / "app.js"
    asset.write_text("first version")
    first_version = _asset_version(app)
    asset.write_text("second version")

    assert _asset_version(app) != first_version


# --- multi-user accounts and isolation ----------------------------------

def _signup(app, username="alice", password="alice-password"):
    """Register a new user in a fresh client; signup logs them in."""
    client = app.test_client()
    resp = client.post(
        "/signup",
        data={"username": username, "password": password},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    return client


def test_signup_creates_hashed_password_and_logs_in(app):
    from app.models import User

    client = _signup(app, username="Alice", password="alice-password")

    user = User.query.filter_by(username="Alice").one()
    assert not user.is_admin
    assert user.display_name == "Alice"
    assert user.password_hash != "alice-password"
    assert "alice-password" not in user.password_hash
    assert user.check_password("alice-password")

    # Signup signs the new account in.
    assert client.get("/").status_code == 200
    dashboard = client.get("/").data.decode()
    assert 'href="/account"' in dashboard
    assert "Your to-do list, Alice" in dashboard


def test_user_can_update_display_name_and_dashboard_greeting(app):
    from app.models import User

    client = _signup(app, username="steve01")
    user = User.query.filter_by(username="steve01").one()
    assert user.display_name == "steve01"

    response = client.post(
        "/account/display-name",
        data={"display_name": "  Steve  "},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Display name updated." in response.data.decode()
    db.session.expire_all()
    assert db.session.get(User, user.id).display_name == "Steve"
    dashboard = client.get("/").data.decode()
    assert "Your to-do list, Steve" in dashboard
    assert "Your to-do list, steve01" not in dashboard


def test_display_name_cannot_be_empty(app):
    from app.models import User

    client = _signup(app, username="steve01")
    user = User.query.filter_by(username="steve01").one()

    response = client.post(
        "/account/display-name",
        data={"display_name": "   "},
    )
    assert response.status_code == 400
    assert "Display Name" in response.data.decode()
    db.session.expire_all()
    assert db.session.get(User, user.id).display_name == "steve01"


def test_appearance_preferences_default_to_orange_and_light_mode(logged_in):
    from app.models import User

    user = User.query.filter_by(username="admin").one()
    assert user.highlight_color == "#f97316"
    assert not user.night_mode
    page = logged_in.get("/").data.decode()
    assert '<body class=""' in page
    assert "--accent: #f97316; --accent-text: #000000;" in page
    assert '<meta name="color-scheme" content="light">' in page


def test_user_can_update_highlight_colour_and_night_mode(logged_in):
    from app.models import User

    response = logged_in.post(
        "/account/appearance",
        data={"highlight_color": "#123ABC", "night_mode": "y"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "Appearance settings updated." in response.data.decode()
    page = logged_in.get("/").data.decode()
    assert '<body class="night-mode"' in page
    assert "--accent: #123abc; --accent-text: #ffffff;" in page
    assert '<meta name="theme-color" content="#141414">' in page

    user = User.query.filter_by(username="admin").one()
    assert user.highlight_color == "#123abc"
    assert user.night_mode

    response = logged_in.post(
        "/account/appearance",
        data={"highlight_color": "#ffee00"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    page = logged_in.get("/").data.decode()
    assert '<body class=""' in page
    assert "--accent: #ffee00; --accent-text: #000000;" in page
    db.session.expire_all()
    user = db.session.get(User, user.id)
    assert user.highlight_color == "#ffee00"
    assert not user.night_mode


def test_invalid_highlight_colour_does_not_change_appearance(logged_in):
    from app.models import User

    response = logged_in.post(
        "/account/appearance",
        data={"highlight_color": "red", "night_mode": "y"},
    )
    assert response.status_code == 400
    assert "Choose a valid colour." in response.data.decode()
    user = User.query.filter_by(username="admin").one()
    assert user.highlight_color == "#f97316"
    assert not user.night_mode


def test_signup_rejects_duplicate_username_case_insensitively(app):
    from app.models import User

    _signup(app, username="Alice", password="alice-password")
    second = app.test_client()
    resp = second.post(
        "/signup",
        data={"username": "alice", "password": "another-password"},
        follow_redirects=True,
    )
    assert "That username is already taken." in resp.data.decode()
    assert User.query.filter_by(username="alice").first() is None
    assert User.query.count() == 2  # seeded admin + Alice


def test_signup_and_login_casefold_unicode_usernames(app):
    from app.models import User

    _signup(app, username="Straße", password="alice-password")
    second = app.test_client()
    resp = second.post(
        "/signup",
        data={"username": "STRASSE", "password": "another-password"},
        follow_redirects=True,
    )
    assert "That username is already taken." in resp.data.decode()
    assert User.query.filter_by(username="STRASSE").first() is None

    fresh = app.test_client()
    resp = fresh.post(
        "/login", data={"username": "STRASSE", "password": "alice-password"}
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"


def test_username_key_is_unique_in_database(app):
    from sqlalchemy.exc import IntegrityError

    from app.models import User, db

    db.session.add(User(username="CaseSensitive", password_hash="unused"))
    db.session.commit()
    db.session.add(User(username="casesensitive", password_hash="unused"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_signup_rejects_short_password(app):
    from app.models import User

    client = app.test_client()
    resp = client.post(
        "/signup",
        data={"username": "shorty", "password": "1234567"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "at least 8 characters" in resp.data.decode().lower()
    assert User.query.filter_by(username="shorty").first() is None


def test_login_username_is_case_insensitive(app):
    _signup(app, username="Alice", password="alice-password")

    fresh = app.test_client()
    resp = fresh.post(
        "/login", data={"username": "aLiCe", "password": "alice-password"}
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/"


def test_created_tasks_projects_and_trash_are_owned_by_creator(app):
    from app.models import DeletedBatch, Project, User

    alice = _signup(app)
    alice_user = User.query.filter_by(username="alice").one()

    task = _create_task(alice, title="Owned by alice")
    task_user_id = task.user_id
    alice.post("/projects/new", data={"name": "Alice owned", "color": "pink"})
    project = Project.query.filter_by(name="Alice owned").one()
    alice.post(f"/tasks/{task.id}/delete")
    batch = DeletedBatch.query.one()

    assert task_user_id == alice_user.id
    assert project.user_id == alice_user.id
    assert batch.user_id == alice_user.id
    assert task_user_id != User.query.filter_by(username="admin").one().id


def test_users_cannot_see_each_others_tasks_or_projects(app, logged_in):
    import json

    from app.models import Project

    admin_task = _create_task(logged_in, title="Admin secret", due="2026-04-01")
    logged_in.post("/projects/new", data={"name": "Admin project", "color": "blue"})
    admin_project = Project.query.filter_by(name="Admin project").one()

    alice = _signup(app)
    alice_task = _create_task(alice, title="Alice secret", due="2026-04-02")
    alice.post("/projects/new", data={"name": "Alice project", "color": "pink"})

    admin_pages = (
        "/",
        "/tasks/",
        "/projects/",
        "/calendar/day/2026-04-01?due=1&work=0",
        "/calendar/month/2026-04?due=1&work=0",
    )
    for url in admin_pages:
        page = logged_in.get(url).data.decode()
        assert "Alice secret" not in page, url
        assert "Alice project" not in page, url

    alice_pages = (
        "/",
        "/tasks/",
        "/projects/",
        "/calendar/day/2026-04-02?due=1&work=0",
        "/calendar/month/2026-04?due=1&work=0",
    )
    for url in alice_pages:
        page = alice.get(url).data.decode()
        assert "Admin secret" not in page, url
        assert "Admin project" not in page, url

    admin_results = json.loads(logged_in.get("/tasks/search").data)["results"]
    alice_results = json.loads(alice.get("/tasks/search").data)["results"]
    assert "Alice secret" not in [r["title"] for r in admin_results]
    assert "Admin secret" not in [r["title"] for r in alice_results]

    # The admin's pre-existing data did not disappear when a user signed up.
    assert "Admin secret" in logged_in.get("/tasks/").data.decode()
    assert "Alice secret" in alice.get("/tasks/").data.decode()
    assert admin_project.id != Project.query.filter_by(name="Alice project").one().id
    assert admin_task.user_id != alice_task.user_id


def test_cross_user_records_return_404(app, logged_in):
    from app.models import Project, Task

    admin_task = _create_task(logged_in, title="Admin owned")
    logged_in.post("/projects/new", data={"name": "Admin owned project", "color": "blue"})
    admin_project = Project.query.filter_by(name="Admin owned project").one()

    alice = _signup(app)
    attempts = (
        ("get", f"/tasks/{admin_task.id}"),
        ("get", f"/tasks/{admin_task.id}/edit"),
        ("post", f"/tasks/{admin_task.id}/toggle"),
        ("post", f"/tasks/{admin_task.id}/delete"),
        ("post", f"/tasks/{admin_task.id}/notes"),
        ("post", f"/tasks/{admin_task.id}/subtasks"),
        ("post", f"/tasks/{admin_task.id}/reassign"),
        ("post", f"/tasks/{admin_task.id}/reschedule"),
        ("post", f"/tasks/{admin_task.id}/complete-subtree"),
        ("post", f"/calendar/quick-add-date/{admin_task.id}"),
        ("get", f"/projects/{admin_project.id}"),
        ("get", f"/projects/{admin_project.id}/edit"),
        ("post", f"/projects/{admin_project.id}/delete"),
        ("post", f"/projects/{admin_project.id}/notes"),
        ("post", f"/projects/{admin_project.id}/add-task"),
    )
    for method, url in attempts:
        resp = getattr(alice, method)(url)
        assert resp.status_code == 404, (method, url, resp.status_code)

    db.session.expire_all()
    assert db.session.get(Task, admin_task.id) is not None
    assert db.session.get(Task, admin_task.id).done is False
    assert db.session.get(Project, admin_project.id) is not None


def test_cannot_attach_task_to_another_users_project_or_parent(app, logged_in):
    from app.models import Project, Task

    admin_project = Project.query.filter_by(name="Admin attach project").first()
    logged_in.post("/projects/new", data={"name": "Admin attach project", "color": "blue"})
    admin_project = Project.query.filter_by(name="Admin attach project").one()
    admin_parent = _create_task(logged_in, title="Admin parent")

    alice = _signup(app)
    base = {"notes": "", "due_date": "", "recurrence": "none"}

    resp = alice.post(
        "/tasks/new",
        data={**base, "title": "Sneaky project task", "project_id": admin_project.id,
              "parent_id": 0},
    )
    assert resp.status_code == 200
    assert Task.query.filter_by(title="Sneaky project task").first() is None

    resp = alice.post(
        "/tasks/new",
        data={**base, "title": "Sneaky parent task", "project_id": 0,
              "parent_id": admin_parent.id},
    )
    assert resp.status_code == 200
    assert Task.query.filter_by(title="Sneaky parent task").first() is None

    alice_task = _create_task(alice, title="Alice movable")
    resp = alice.post(
        f"/tasks/{alice_task.id}/reassign",
        data={"project_id": admin_project.id},
    )
    assert resp.status_code == 400
    db.session.expire_all()
    assert db.session.get(Task, alice_task.id).project_id is None


def test_database_rejects_cross_user_task_relations(app):
    from sqlalchemy.exc import IntegrityError

    from app.models import Project, Task, User, db

    owner = User(username="owner", password_hash="unused")
    other = User(username="other", password_hash="unused")
    db.session.add_all((owner, other))
    db.session.flush()
    foreign_project = Project(user_id=other.id, name="Other project")
    foreign_parent = Task(user_id=other.id, title="Other parent")
    foreign_source = Task(user_id=other.id, title="Other recurrence source")
    db.session.add_all((foreign_project, foreign_parent, foreign_source))
    db.session.commit()

    invalid_tasks = (
        Task(user_id=owner.id, title="Foreign project", project_id=foreign_project.id),
        Task(user_id=owner.id, title="Foreign parent", parent_id=foreign_parent.id),
        Task(
            user_id=owner.id,
            title="Foreign recurrence source",
            recurrence_source_id=foreign_source.id,
        ),
    )
    for task in invalid_tasks:
        db.session.add(task)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_trash_is_per_user(app, logged_in):
    from app.models import DeletedBatch

    task = _create_task(logged_in, title="Admin trash item")
    logged_in.post(f"/tasks/{task.id}/delete")
    batch = DeletedBatch.query.one()

    alice = _signup(app)
    page = alice.get("/tasks/trash").data.decode()
    assert "Admin trash item" not in page
    assert alice.post(f"/tasks/trash/{batch.id}/restore").status_code == 404
    assert alice.post(f"/tasks/trash/{batch.id}/purge").status_code == 404

    # Emptying Alice's trash must not remove the admin's batch.
    assert alice.post("/tasks/trash/empty").status_code == 302
    db.session.expire_all()
    assert db.session.get(DeletedBatch, batch.id) is not None
    assert "Admin trash item" in logged_in.get("/tasks/trash").data.decode()


def test_account_page_requires_login(client):
    resp = client.get("/account")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_account_changes_password(app):
    from app.models import User

    alice = _signup(app, username="alice", password="old-password")

    resp = alice.post(
        "/account",
        data={
            "current_password": "wrong-password",
            "new_password": "new-password-1",
            "confirm_password": "new-password-1",
        },
    )
    assert "Current password is incorrect." in resp.data.decode()
    db.session.expire_all()
    assert User.query.filter_by(username="alice").one().check_password("old-password")

    resp = alice.post(
        "/account",
        data={
            "current_password": "old-password",
            "new_password": "new-password-1",
            "confirm_password": "new-password-1",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Password changed." in resp.data.decode()

    db.session.expire_all()
    user = User.query.filter_by(username="alice").one()
    assert user.check_password("new-password-1")
    assert not user.check_password("old-password")

    alice.post("/logout")
    fresh = app.test_client()
    old_login = fresh.post(
        "/login", data={"username": "alice", "password": "old-password"}
    )
    assert "Invalid username or password" in old_login.data.decode()
    new_login = fresh.post(
        "/login", data={"username": "alice", "password": "new-password-1"}
    )
    assert new_login.status_code == 302
    assert new_login.headers["Location"] == "/"


def test_admin_user_management_is_admin_only(app, logged_in):
    from app.models import User

    alice = _signup(app)
    alice_user = User.query.filter_by(username="alice").one()
    admin = User.query.filter_by(username="admin").one()
    assert admin.is_admin
    assert not alice_user.is_admin

    admin_page = logged_in.get("/admin/users")
    assert admin_page.status_code == 200
    assert "alice" in admin_page.data.decode()
    assert 'href="/admin/users"' in logged_in.get("/").data.decode()

    assert 'href="/admin/users"' not in alice.get("/").data.decode()
    assert alice.get("/admin/users").status_code == 403
    assert alice.post(f"/admin/users/{admin.id}/delete").status_code == 403
    assert alice.post(
        f"/admin/users/{alice_user.id}/admin",
        data={"is_admin": "1"},
    ).status_code == 403
    self_delete = logged_in.post(f"/admin/users/{admin.id}/delete")
    assert self_delete.status_code == 302
    assert self_delete.headers["Location"].endswith("/admin/users")
    assert db.session.get(User, admin.id) is not None


def test_admin_can_grant_revoke_and_self_revoke_admin_rights(app, logged_in):
    from app.models import User

    alice = _signup(app)
    alice_user = User.query.filter_by(username="alice").one()
    user_id = alice_user.id

    admin_page = logged_in.get("/admin/users").data.decode()
    assert "Make admin" in admin_page
    assert logged_in.post(
        f"/admin/users/{user_id}/admin",
        data={"is_admin": "1"},
    ).status_code == 302
    assert db.session.get(User, user_id).is_admin
    assert alice.get("/admin/users").status_code == 200

    assert "Revoke admin" in alice.get("/admin/users").data.decode()
    assert logged_in.post(
        f"/admin/users/{user_id}/admin",
        data={"is_admin": "0"},
    ).status_code == 302
    db.session.expire_all()
    assert not db.session.get(User, user_id).is_admin
    assert alice.get("/admin/users").status_code == 403

    logged_in.post(f"/admin/users/{user_id}/admin", data={"is_admin": "1"})
    assert db.session.get(User, user_id).is_admin
    response = alice.post(
        f"/admin/users/{user_id}/admin",
        data={"is_admin": "0"},
    )
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/account")
    db.session.expire_all()
    assert not db.session.get(User, user_id).is_admin
    assert alice.get("/admin/users").status_code == 403


def test_admin_user_deletion_requires_csrf(csrf_client, app):
    from app.models import User, db

    alice = User(username="csrf-user", password_hash="unused")
    db.session.add(alice)
    db.session.commit()
    user_id = alice.id

    response = csrf_client.post(f"/admin/users/{user_id}/delete")
    assert response.status_code == 400
    assert db.session.get(User, user_id) is not None
    response = csrf_client.post(
        f"/admin/users/{user_id}/admin",
        data={"is_admin": "1"},
    )
    assert response.status_code == 400
    assert not db.session.get(User, user_id).is_admin


def test_builtin_admin_cannot_delete_own_account(app, logged_in):
    from app.models import User

    admin = User.query.filter_by(username="admin").one()
    admin_id = admin.id
    page = logged_in.get("/account").data.decode()
    assert "The built-in admin account cannot be deleted." in page
    assert "Delete my account" not in page

    response = logged_in.post(
        "/account/delete",
        data={"current_password": "test-password"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "The built-in admin account cannot be deleted." in response.data.decode()
    db.session.expire_all()
    assert db.session.get(User, admin_id).is_admin


def test_builtin_admin_cannot_be_deleted_by_another_admin(app):
    from app.models import User

    original_admin = User.query.filter_by(username="admin").one()
    original_admin_id = original_admin.id
    moderator = _signup(app, username="moderator")
    moderator_user = User.query.filter_by(username="moderator").one()
    moderator_user.is_admin = True
    db.session.commit()

    page = moderator.get("/admin/users").data.decode()
    assert "This account can't be deleted" in page
    assert f'action="/admin/users/{original_admin_id}/delete"' not in page

    response = moderator.post(
        f"/admin/users/{original_admin_id}/delete",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "The built-in admin account cannot be deleted." in response.data.decode()
    db.session.expire_all()
    assert db.session.get(User, original_admin_id).is_protected_admin


def test_builtin_admin_cannot_delete_itself_after_admin_rights_are_revoked(
    app, logged_in,
):
    from app.models import User

    admin = User.query.filter_by(username="admin").one()
    admin_id = admin.id
    admin.is_admin = False
    db.session.commit()

    page = logged_in.get("/account").data.decode()
    assert "The built-in admin account cannot be deleted." in page
    assert "Delete my account" not in page

    response = logged_in.post(
        "/account/delete",
        data={"current_password": "test-password"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "The built-in admin account cannot be deleted." in response.data.decode()
    db.session.expire_all()
    assert db.session.get(User, admin_id) is not None


def test_admin_delete_user_cascades_through_user_data(app, logged_in):
    import datetime as dt

    from app.models import DeletedBatch, Project, Task, TaskDate, User

    alice = _signup(app)
    alice_user = User.query.filter_by(username="alice").one()
    admin_task = _create_task(logged_in, title="Keep admin task")

    alice.post("/projects/new", data={"name": "Alice project", "color": "pink"})
    project = Project.query.filter_by(name="Alice project").one()
    alice_task = _create_task(
        alice, title="Alice task", project=project.id,
    )
    db.session.add(TaskDate(task_id=alice_task.id, date=dt.date(2026, 4, 1)))
    batch = DeletedBatch(
        user_id=alice_user.id,
        summary="Alice deleted task",
        payload='{"tasks":[]}',
    )
    db.session.add(batch)
    db.session.commit()
    alice_id = alice_user.id
    project_id = project.id
    task_id = alice_task.id
    batch_id = batch.id
    admin_task_id = admin_task.id

    resp = logged_in.post(
        f"/admin/users/{alice_id}/delete",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "all of their data" in resp.data.decode()

    db.session.expire_all()
    assert db.session.get(User, alice_id) is None
    assert db.session.get(Project, project_id) is None
    assert db.session.get(Task, task_id) is None
    assert TaskDate.query.filter_by(task_id=task_id).count() == 0
    assert db.session.get(DeletedBatch, batch_id) is None
    assert db.session.get(Task, admin_task_id) is not None


@pytest.mark.parametrize("is_admin", [False, True])
def test_user_can_delete_only_their_own_account_and_data(app, is_admin):
    import datetime as dt

    from app.models import DeletedBatch, Project, Task, TaskDate, User

    alice = _signup(app)
    alice_user = User.query.filter_by(username="alice").one()
    alice_user.is_admin = is_admin
    db.session.commit()
    admin = User.query.filter_by(username="admin").one()
    admin_task = Task(user_id=admin.id, title="Keep admin task")
    db.session.add(admin_task)

    alice.post("/projects/new", data={"name": "Alice private", "color": "pink"})
    project = Project.query.filter_by(name="Alice private").one()
    alice_task = _create_task(alice, title="Alice private task", project=project.id)
    db.session.add(TaskDate(task_id=alice_task.id, date=dt.date(2026, 4, 2)))
    batch = DeletedBatch(
        user_id=alice_user.id,
        summary="Alice deleted task",
        payload='{"tasks":[]}',
    )
    db.session.add(batch)
    db.session.commit()
    alice_id = alice_user.id
    project_id = project.id
    task_id = alice_task.id
    batch_id = batch.id
    admin_task_id = admin_task.id

    wrong_password = alice.post(
        "/account/delete",
        data={"current_password": "wrong-password"},
    )
    assert wrong_password.status_code == 400
    assert "Current password is incorrect." in wrong_password.data.decode()
    assert db.session.get(User, alice_id) is not None

    resp = alice.post(
        "/account/delete",
        data={"current_password": "alice-password"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "all of its data were deleted" in resp.data.decode()
    assert alice.get("/").status_code == 302
    assert alice.get("/login").status_code == 200

    db.session.expire_all()
    assert db.session.get(User, alice_id) is None
    assert db.session.get(Project, project_id) is None
    assert db.session.get(Task, task_id) is None
    assert TaskDate.query.filter_by(task_id=task_id).count() == 0
    assert db.session.get(DeletedBatch, batch_id) is None
    assert db.session.get(Task, admin_task_id) is not None


def test_admin_can_delete_self_from_user_list_and_is_logged_out(app, logged_in):
    from app.models import User

    admin = User(username="moderator", is_admin=True)
    admin.set_password("moderator-password")
    other_admin = User(username="other-admin", is_admin=True)
    other_admin.set_password("other-password")
    db.session.add(admin)
    db.session.add(other_admin)
    db.session.commit()
    admin_id = admin.id
    other_admin_id = other_admin.id

    client = app.test_client()
    client.post(
        "/login",
        data={"username": "moderator", "password": "moderator-password"},
    )
    page = client.get("/admin/users").data.decode()
    assert "Delete my account" in page

    response = logged_in.post(
        f"/admin/users/{other_admin_id}/delete",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert db.session.get(User, other_admin_id) is None

    response = client.post(
        f"/admin/users/{admin_id}/delete",
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert "all of its data were deleted" in response.data.decode()
    assert client.get("/login").status_code == 200
    assert db.session.get(User, admin_id) is None


# --- CSV import -------------------------------------------------------------

def _import_csv(logged_in, csv_text, filename="tasks.csv"):
    from io import BytesIO
    token = _token(logged_in.get("/tasks/import").data.decode())
    return logged_in.post(
        "/tasks/import",
        data={
            "file": (BytesIO(csv_text.encode("utf-8")), filename),
            "csrf_token": token,
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )


def test_import_page_renders(logged_in):
    resp = logged_in.get("/tasks/import")
    assert resp.status_code == 200
    page = resp.data.decode()
    assert "Import tasks" in page
    assert "title" in page


def test_import_minimal_csv(logged_in):
    resp = _import_csv(logged_in, "title\nBuy milk\nWalk dog\n")
    assert resp.status_code == 200
    from app.models import Task
    titles = {t.title for t in Task.query.all()}
    assert titles == {"Buy milk", "Walk dog"}


def test_import_full_columns(logged_in):
    from app.models import Project, Task, TaskDate
    logged_in.post("/projects/new", data={"name": "Home", "color": "blue"})
    csv_text = (
        "title,notes,due_date,work_dates,project,done\n"
        '"Renovate kitchen","Sand the walls",2026-05-01,2026-04-28,Home,false\n'
        '"Old chore",,,,Home,true\n'
    )
    resp = _import_csv(logged_in, csv_text)
    assert resp.status_code == 200

    task = Task.query.filter_by(title="Renovate kitchen").one()
    assert task.notes == "Sand the walls"
    assert task.due_date.isoformat() == "2026-05-01"
    assert not task.done
    assert TaskDate.query.filter_by(task_id=task.id).count() == 1
    project = Project.query.filter_by(name="Home").one()
    assert task.project_id == project.id

    done_task = Task.query.filter_by(title="Old chore").one()
    assert done_task.done
    assert done_task.due_date is None


def test_import_work_date_ranges(logged_in):
    from app.models import Task, TaskDate
    csv_text = (
        "title,work_dates\n"
        '"Trip","2026-06-01-2026-06-03, 2026-06-07"\n'
    )
    resp = _import_csv(logged_in, csv_text)
    assert resp.status_code == 200
    task = Task.query.filter_by(title="Trip").one()
    dates = sorted(td.date.isoformat() for td in TaskDate.query.filter_by(task_id=task.id))
    assert dates == ["2026-06-01", "2026-06-02", "2026-06-03", "2026-06-07"]


def test_import_parent_links_by_title(logged_in):
    from app.models import Task
    csv_text = (
        "title,parent\n"
        '"Plan holiday",\n'
        '"Book flights",Plan holiday\n'
    )
    resp = _import_csv(logged_in, csv_text)
    assert resp.status_code == 200
    parent = Task.query.filter_by(title="Plan holiday").one()
    child = Task.query.filter_by(title="Book flights").one()
    assert child.parent_id == parent.id
    assert parent.parent_id is None


def test_import_parent_from_existing_task(logged_in):
    from app.models import Task
    _create_task(logged_in, title="Existing parent")
    csv_text = "title,parent\nChild,Existing parent\n"
    resp = _import_csv(logged_in, csv_text)
    assert resp.status_code == 200
    parent = Task.query.filter_by(title="Existing parent").one()
    child = Task.query.filter_by(title="Child").one()
    assert child.parent_id == parent.id


def test_import_reports_bad_rows_and_continues(logged_in):
    from app.models import Task
    csv_text = (
        "title,due_date\n"
        '"Good task",2026-05-01\n'
        '"Bad date",not-a-date\n'
        '\n'
        '"Also good",\n'
    )
    resp = _import_csv(logged_in, csv_text)
    assert resp.status_code == 200
    page = resp.data.decode()
    assert "Line 3" in page  # bad row reported by line number
    titles = {t.title for t in Task.query.all()}
    assert titles == {"Good task", "Also good"}


def test_import_missing_title_row_skipped(logged_in):
    from app.models import Task
    csv_text = (
        "title\n"
        '"Has title"\n'
        '"   "\n'
    )
    resp = _import_csv(logged_in, csv_text)
    assert resp.status_code == 200
    titles = {t.title for t in Task.query.all()}
    assert titles == {"Has title"}


def test_import_unknown_project_warns_but_imports(logged_in):
    from app.models import Task
    csv_text = "title,project\nOrphan,Missing project\n"
    resp = _import_csv(logged_in, csv_text)
    assert resp.status_code == 200
    task = Task.query.filter_by(title="Orphan").one()
    assert task.project_id is None
    assert "Missing project" in resp.data.decode()


def test_import_unknown_parent_warns_but_imports(logged_in):
    from app.models import Task
    csv_text = "title,parent\nLonely,Nonexistent\n"
    resp = _import_csv(logged_in, csv_text)
    assert resp.status_code == 200
    task = Task.query.filter_by(title="Lonely").one()
    assert task.parent_id is None


def test_import_requires_title_column(logged_in):
    resp = _import_csv(logged_in, "name\nJust a name\n")
    assert resp.status_code == 400
    from app.models import Task
    assert Task.query.count() == 0


def test_import_requires_file(logged_in):
    token = _token(logged_in.get("/tasks/import").data.decode())
    resp = logged_in.post(
        "/tasks/import",
        data={"csrf_token": token},
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 400
    page = resp.data.decode()
    assert "Choose a CSV file" in page


def test_import_rejects_non_utf8(logged_in):
    from io import BytesIO
    token = _token(logged_in.get("/tasks/import").data.decode())
    resp = logged_in.post(
        "/tasks/import",
        data={
            "file": (BytesIO(b"\xff\xfe\x00b\x00a\x00d"), "bad.csv"),
            "csrf_token": token,
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 400
    assert "UTF-8" in resp.data.decode()


def test_import_header_case_insensitive(logged_in):
    from app.models import Task
    csv_text = "TITLE,Due_Date\nCapitalised,2026-05-05\n"
    resp = _import_csv(logged_in, csv_text)
    assert resp.status_code == 200
    task = Task.query.filter_by(title="Capitalised").one()
    assert task.due_date.isoformat() == "2026-05-05"


def test_import_cycle_falls_back_to_top_level(logged_in):
    from app.models import Task
    csv_text = (
        "title,parent\n"
        '"A",B\n'
        '"B",A\n'
    )
    resp = _import_csv(logged_in, csv_text)
    assert resp.status_code == 200
    a = Task.query.filter_by(title="A").one()
    b = Task.query.filter_by(title="B").one()
    # One of them keeps the parent link, the other is pushed to top level so
    # no cycle exists.
    if a.parent_id == b.id:
        assert b.parent_id is None
    else:
        assert b.parent_id == a.id
        assert a.parent_id is None


def test_import_self_parent_rejected(logged_in):
    from app.models import Task
    csv_text = "title,parent\nSolo,Solo\n"
    resp = _import_csv(logged_in, csv_text)
    assert resp.status_code == 200
    task = Task.query.filter_by(title="Solo").one()
    assert task.parent_id is None


def test_import_done_accepts_word_variants(logged_in):
    from app.models import Task
    csv_text = (
        "title,done\n"
        '"Yes task",YES\n'
        '"No task",0\n'
    )
    resp = _import_csv(logged_in, csv_text)
    assert resp.status_code == 200
    assert Task.query.filter_by(title="Yes task").one().done
    assert not Task.query.filter_by(title="No task").one().done


# --- CSV export -------------------------------------------------------------

def test_export_csv_contains_tasks(logged_in):
    _create_task(logged_in, title="Exported task", due="2026-05-01")
    logged_in.post("/projects/new", data={"name": "Work", "color": "blue"})
    from app.models import Project
    project = Project.query.filter_by(name="Work").one()
    _create_task(logged_in, title="Project task", project=project.id)

    resp = logged_in.get("/tasks/export")
    assert resp.status_code == 200
    assert resp.mimetype == "text/csv"
    assert "attachment" in resp.headers["Content-Disposition"]
    body = resp.data.decode("utf-8")
    assert "Exported task" in body
    assert "2026-05-01" in body
    assert "Work" in body


def test_export_requires_login(client):
    resp = client.get("/tasks/export")
    assert resp.status_code == 302


def test_export_is_formula_safe(logged_in):
    _create_task(logged_in, title='=HYPERLINK("http://evil")')
    resp = logged_in.get("/tasks/export")
    body = resp.data.decode("utf-8")
    # The dangerous cell is exported with a protective leading quote.
    assert "'=HYPERLINK" in body


def test_export_import_round_trip(logged_in):
    from app.models import DeletedBatch, Task, TaskDate
    logged_in.post("/projects/new", data={"name": "Round", "color": "blue"})
    from app.models import Project
    project = Project.query.filter_by(name="Round").one()
    parent = _create_task(logged_in, title="Round parent", due="2026-06-01")
    child = _create_task(
        logged_in, title="Round child", project=project.id, parent=parent.id
    )
    logged_in.post(
        f"/tasks/{child.id}/work-dates",
        data={"start": "2026-06-02", "end": "2026-06-03"},
    )
    _create_task(logged_in, title="=dangerous")

    resp = logged_in.get("/tasks/export")
    csv_text = resp.data.decode("utf-8")

    # Wipe and re-import.
    TaskDate.query.delete()
    Task.query.delete()
    DeletedBatch.query.delete()
    db.session.commit()
    assert Task.query.count() == 0

    resp = _import_csv(logged_in, csv_text)
    assert resp.status_code == 200
    page = resp.data.decode()
    assert "imported" in page

    new_parent = Task.query.filter_by(title="Round parent").one()
    new_child = Task.query.filter_by(title="Round child").one()
    assert new_child.parent_id == new_parent.id
    assert new_child.project.name == "Round"
    assert new_parent.due_date.isoformat() == "2026-06-01"
    dates = sorted(
        td.date.isoformat() for td in TaskDate.query.filter_by(task_id=new_child.id)
    )
    assert dates == ["2026-06-02", "2026-06-03"]
    assert Task.query.filter_by(title="=dangerous").count() == 1


def test_export_only_own_tasks(logged_in, app):
    from app.models import User
    bob = User(username="bob")
    bob.set_password("bob-password-123")
    db.session.add(bob)
    db.session.commit()
    _create_task(logged_in, title="Mine only")

    bob_client = app.test_client()
    bob_client.post("/login", data={"username": "bob", "password": "bob-password-123"})
    resp = bob_client.get("/tasks/export")
    body = resp.data.decode("utf-8")
    assert "Mine only" not in body


# --- project completion -----------------------------------------------------

def _token_from(client, url="/projects/"):
    return _token(client.get(url).data.decode())


def test_project_complete_and_reopen(logged_in):
    from app.models import Project
    logged_in.post("/projects/new", data={"name": "Finisher", "color": "blue"})
    project = Project.query.filter_by(name="Finisher").one()
    token = _token_from(logged_in, "/projects")

    resp = logged_in.post(
        f"/projects/{project.id}/toggle-completed",
        data={"csrf_token": token, "next": "/projects/"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    db.session.refresh(project)
    assert project.completed

    resp = logged_in.post(
        f"/projects/{project.id}/toggle-completed",
        data={"csrf_token": token, "next": "/projects/"},
        follow_redirects=True,
    )
    db.session.refresh(project)
    assert not project.completed


def test_completed_projects_hidden_until_toggle(logged_in):
    from app.models import Project
    logged_in.post("/projects/new", data={"name": "Open one", "color": "blue"})
    logged_in.post("/projects/new", data={"name": "Done one", "color": "pink"})
    done = Project.query.filter_by(name="Done one").one()
    token = _token_from(logged_in, "/projects")
    logged_in.post(
        f"/projects/{done.id}/toggle-completed",
        data={"csrf_token": token, "next": "/projects/"},
        follow_redirects=True,
    )

    page = logged_in.get("/projects/").data.decode()
    assert "Open one" in page
    assert "Done one" not in page
    assert "Completed projects" not in page

    # Toggle the slider on.
    logged_in.post(
        "/projects/preferences/show-completed",
        data={"show": "1", "next": "/projects/"},
    )
    page = logged_in.get("/projects/").data.decode()
    assert "Completed projects" in page
    assert "Done one" in page
    assert "Open one" in page

    # Toggle it back off.
    logged_in.post(
        "/projects/preferences/show-completed",
        data={"next": "/projects/"},
    )
    page = logged_in.get("/projects/").data.decode()
    assert "Done one" not in page


def test_completed_project_ownership_isolated(logged_in, app):
    from app.models import Project, User
    bob = User(username="bob2")
    bob.set_password("bob-password-123")
    db.session.add(bob)
    db.session.commit()
    logged_in.post("/projects/new", data={"name": "Admin secret project", "color": "blue"})
    admin_project = Project.query.filter_by(name="Admin secret project").one()

    bob_client = app.test_client()
    bob_client.post("/login", data={"username": "bob2", "password": "bob-password-123"})

    # Bob cannot toggle someone else's project.
    token = _token_from(bob_client, "/projects")
    resp = bob_client.post(
        f"/projects/{admin_project.id}/toggle-completed",
        data={"csrf_token": token, "next": "/projects/"},
    )
    assert resp.status_code == 404
    db.session.refresh(admin_project)
    assert not admin_project.completed

    # Admin completes their own project; bob still cannot see it.
    token = _token_from(logged_in, "/projects")
    logged_in.post(
        f"/projects/{admin_project.id}/toggle-completed",
        data={"csrf_token": token, "next": "/projects/"},
    )
    bob_client.post(
        "/projects/preferences/show-completed",
        data={"show": "1", "next": "/projects/"},
    )
    page = bob_client.get("/projects/").data.decode()
    assert "Admin secret project" not in page


def test_project_detail_shows_completed_state(logged_in):
    from app.models import Project
    logged_in.post("/projects/new", data={"name": "Detailed", "color": "blue"})
    project = Project.query.filter_by(name="Detailed").one()
    token = _token_from(logged_in, "/projects")
    logged_in.post(
        f"/projects/{project.id}/toggle-completed",
        data={"csrf_token": token, "next": f"/projects/{project.id}"},
    )
    page = logged_in.get(f"/projects/{project.id}").data.decode()
    assert "Detailed" in page  # completed projects remain viewable


def test_project_detail_has_complete_button(logged_in):
    from app.models import Project
    logged_in.post("/projects/new", data={"name": "Buttoned", "color": "blue"})
    project = Project.query.filter_by(name="Buttoned").one()

    page = logged_in.get(f"/projects/{project.id}").data.decode()
    assert "Complete project" in page
    assert "Reopen project" not in page

    token = _token_from(logged_in, "/projects/")
    logged_in.post(
        f"/projects/{project.id}/toggle-completed",
        data={"csrf_token": token, "next": f"/projects/{project.id}"},
        follow_redirects=True,
    )
    page = logged_in.get(f"/projects/{project.id}").data.decode()
    assert "Reopen project" in page
    assert "Complete project" not in page


def test_task_detail_has_complete_button(logged_in):
    from app.models import Task
    task = _create_task(logged_in, title="Buttoned task")

    page = logged_in.get(f"/tasks/{task.id}").data.decode()
    assert "Complete task" in page
    assert "Reopen task" not in page

    token = _token(logged_in.get(f"/tasks/{task.id}").data.decode())
    logged_in.post(
        f"/tasks/{task.id}/toggle",
        data={"csrf_token": token, "next": f"/tasks/{task.id}"},
        follow_redirects=True,
    )
    page = logged_in.get(f"/tasks/{task.id}").data.decode()
    assert "Reopen task" in page
    assert "Complete task" not in page
    assert Task.query.get(task.id).done
