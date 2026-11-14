"""Dashboard: what's due and what to work on today, plus the week ahead."""
import datetime as dt

from flask import Blueprint, render_template
from flask_login import login_required

from . import queries
from .models import Task, TaskDate, db

bp = Blueprint("main", __name__)


@bp.route("/")
@login_required
def dashboard():
    t = dt.date.today()
    week_end = t + dt.timedelta(days=6)
    month_end = t.replace(day=28) + dt.timedelta(days=4)
    month_end = month_end.replace(day=1) - dt.timedelta(days=1)

    due_today = queries.due_groups(t, t).get(t, [])
    work_today = queries.work_groups(t, t).get(t, [])
    week_due = sorted(queries.due_groups(t + dt.timedelta(days=1), week_end).items())
    month_due = sorted(queries.due_groups(week_end + dt.timedelta(days=1), month_end).items())

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
