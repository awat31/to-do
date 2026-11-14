import os

# Force test determinism regardless of .env / shell environment
os.environ["FLASK_CONFIG"] = "testing"
os.environ["TODO_ADMIN_PASSWORD"] = "test-password"

import pytest  # noqa: E402

from app import create_app, db  # noqa: E402
from app.periods import BadPeriod, period, period_value  # noqa: E402


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


def _csrf(client):
    resp = client.get("/tasks/new")
    if resp.status_code == 302:
        resp = client.get(resp.headers["Location"])
    for line in resp.data.decode().splitlines():
        if 'name="csrf_token"' in line and 'value="' in line:
            return line.split('value="')[1].split('"')[0]
    return ""


def _create_task(logged_in, *, title, due=None, project=None, parent=None):
    data = {
        "title": title,
        "notes": "",
        "due_date": due or "",
        "project_id": project or 0,
        "parent_id": parent or 0,
    }
    resp = logged_in.post("/tasks/new", data=data, follow_redirects=True)
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
    year_work = logged_in.get("/calendar/year/2026?mode=work").data.decode()
    year_due = logged_in.get("/calendar/year/2026?mode=due").data.decode()

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

    parent_id, child_id, grandchild_id = parent.id, child.id, grandchild.id
    logged_in.post(f"/tasks/{parent_id}/delete")
    db.session.close()  # drop cached instances deleted by the bulk delete
    assert Task.query.get(child_id) is None
    assert Task.query.get(grandchild_id) is None


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
    csrf = _csrf(logged_in)
    import datetime as dt

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
    from app.models import Task, TaskDate

    done = _create_task(logged_in, title="Finished thing")
    done.due_date = dt.date.today()
    done.done = True
    open_task = _create_task(logged_in, title="Open thing")
    # work-on today but not due today -> shows only in 'Working on today'
    db.session.add(TaskDate(task_id=open_task.id, date=dt.date.today()))
    db.session.commit()
    csrf = _csrf(logged_in)
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
    page2 = logged_in.get("/tasks/").data.decode()
    from app.queries import filter_task_tree
    roots = Task.query.filter_by(parent_id=None).all()
    filtered = filter_task_tree(roots, True)
    assert [t.title for t in filtered] == ["Done container"]


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
