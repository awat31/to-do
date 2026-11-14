"""Dashboard: what's due and what to work on today, plus the week ahead."""
import datetime as dt

from flask import Blueprint, redirect, render_template, request, session, url_for
from flask_login import login_required

from . import queries
from .models import Task, TaskDate, db

bp = Blueprint("main", __name__)


def _prune_empty(pairs):
    """[(date, tasks)] -> drop groups left empty (hide-completed filtering)."""
    return [(d, ts) for d, ts in pairs if ts]


@bp.route("/")
@login_required
def dashboard():
    t = dt.date.today()
    hide = session.get("hide_completed", False)
    week_end = t + dt.timedelta(days=6)
    month_end = t.replace(day=28) + dt.timedelta(days=4)
    month_end = month_end.replace(day=1) - dt.timedelta(days=1)

    due_today = queries.due_groups(t, t).get(t, [])
    if hide:
        due_today = [task for task in due_today if not task.done]
    due_ids = {task.id for task in due_today}

    work_all = queries.work_groups(t, t).get(t, [])
    # Hide tasks already shown under 'Due today' so the list and its count
    # always match; respect the hide-completed preference.
    work_today = [
        task for task in work_all
        if task.id not in due_ids and not (hide and task.done)
    ]

    week_due = sorted(queries.due_groups(t + dt.timedelta(days=1), week_end).items())
    month_due = sorted(queries.due_groups(week_end + dt.timedelta(days=1), month_end).items())
    if hide:
        week_due = _prune_empty([(d, [x for x in ts if not x.done]) for d, ts in week_due])
        month_due = _prune_empty([(d, [x for x in ts if not x.done]) for d, ts in month_due])

    open_tasks = Task.query.filter_by(done=False).count()
    overdue = Task.query.filter(
        Task.done == False,  # noqa: E712
        Task.due_date != None,  # noqa: E711
        Task.due_date < t,
    ).count()
    unscheduled = Task.query.filter(
        Task.done == False,  # noqa: E712
        Task.due_date == None,  # noqa: E711
        ~Task.id.in_(db.session.query(TaskDate.task_id)),
    ).count()

    return render_template(
        "dashboard.html",
        today=t,
        due_today=due_today,
        work_today=work_today,
        week_due=week_due,
        month_due=month_due,
        week_end=week_end,
        month_end=month_end,
        open_tasks=open_tasks,
        overdue=overdue,
        unscheduled=unscheduled,
    )


@bp.route("/preferences/hide-completed", methods=("POST",))
@login_required
def set_hide_completed():
    """Toggle the 'hide completed tasks' preference (stored in the session)."""
    session["hide_completed"] = request.form.get("hide") == "1"
    nxt = request.form.get("next")
    return redirect(nxt or url_for(".dashboard"))
