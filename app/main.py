"""Dashboard: what's due and what to work on today, plus the week ahead."""
import datetime as dt

from flask import Blueprint, render_template, request, session, url_for
from flask_login import current_user, login_required

from . import periods, queries
from .models import Task
from .utils import redirect_back

bp = Blueprint("main", __name__)


def _prune_empty(pairs):
    """[(date, tasks)] -> drop groups left empty (hide-completed filtering)."""
    return [(d, ts) for d, ts in pairs if ts]


@bp.route("/")
@login_required
def dashboard():
    t = periods.today()
    hide = session.get("hide_completed", False)
    week_end = t + dt.timedelta(days=6)
    month_end = t.replace(day=28) + dt.timedelta(days=4)
    month_end = month_end.replace(day=1) - dt.timedelta(days=1)

    due_today = queries.due_groups(
        t, t, user_id=current_user.id
    ).get(t, [])
    if hide:
        due_today = [task for task in due_today if not task.done]
    due_ids = {task.id for task in due_today}

    work_all = queries.work_groups(
        t, t, user_id=current_user.id
    ).get(t, [])
    # Hide tasks already shown under 'Due today' so the list and its count
    # always match; respect the hide-completed preference.
    work_today = [
        task for task in work_all
        if task.id not in due_ids and not (hide and task.done)
    ]

    week_due = sorted(
        queries.due_groups(
            t + dt.timedelta(days=1), week_end, user_id=current_user.id
        ).items()
    )
    month_due = sorted(
        queries.due_groups(
            week_end + dt.timedelta(days=1), month_end, user_id=current_user.id
        ).items()
    )
    if hide:
        week_due = _prune_empty([(d, [x for x in ts if not x.done]) for d, ts in week_due])
        month_due = _prune_empty([(d, [x for x in ts if not x.done]) for d, ts in month_due])

    queries.prepare_tasks(user_id=current_user.id)

    overdue = Task.query.filter(
        Task.user_id == current_user.id,
        Task.done.is_(False),
        Task.due_date.is_not(None),
        Task.due_date < t,
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
        overdue=overdue,
    )


@bp.route("/preferences/hide-completed", methods=("POST",))
@login_required
def set_hide_completed():
    """Toggle the 'hide completed tasks' preference (stored in the session)."""
    session["hide_completed"] = request.form.get("hide") == "1"
    return redirect_back(request.form.get("next"), url_for(".dashboard"))
