"""Calendar views: day / week / month / year, grouped by due or work dates.

Structure per section (Due / Working on):
    [(group_key, [(date, [tasks...]), ...]), ...]
A group is a day (day/week/month views) or a month (year view); inside a
group, tasks are listed under their exact dates.
"""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import login_required
from flask_wtf.csrf import validate_csrf
from werkzeug.exceptions import BadRequest

from . import periods, queries
from .models import Task, TaskDate, db

bp = Blueprint("calendarview", __name__, url_prefix="/calendar")


def _mode() -> str:
    m = request.args.get("mode", "both")
    return m if m in ("both", "due", "work") else "both"


def _keyfn(kind: str):
    return (lambda d: (d.month,)) if kind == "year" else (lambda d: (d,))


def _due_breakdown(kind: str, start, end, keyfn) -> list:
    """(group_key, [(date, tasks), ...]) sorted by group then date."""
    per_date = queries.due_groups(start, end)  # date -> tasks
    grouped: dict = {}
    for d, tasks in per_date.items():
        grouped.setdefault(keyfn(d), {}).setdefault(d, []).extend(tasks)
    return [(k, sorted(m.items())) for k, m in sorted(grouped.items())]


def _work_breakdown(kind: str, start, end, keyfn) -> list:
    per_date = queries.work_groups(start, end)  # date -> tasks
    grouped: dict = {}
    for d, tasks in per_date.items():
        grouped.setdefault(keyfn(d), {}).setdefault(d, []).extend(tasks)
    return [(k, sorted(m.items())) for k, m in sorted(grouped.items())]


@bp.route("/", defaults={"kind": "day", "value": None})
@bp.route("/<kind>/", defaults={"value": None})
@bp.route("/<kind>/<value>")
@login_required
def view(kind: str, value: str | None):
    if kind not in periods.KINDS:
        abort(404)
    try:
        p = periods.period(kind, value)
    except periods.BadPeriod:
        abort(404)

    mode = _mode()
    keyfn = _keyfn(kind)

    due_break = _due_breakdown(kind, p["start"], p["end"], keyfn)
    work_break = _work_breakdown(kind, p["start"], p["end"], keyfn)
    due_count = sum(len(tasks) for _, days in due_break for _, tasks in days)
    work_count = sum(len(tasks) for _, days in work_break for _, tasks in days)

    return render_template(
        "calendar.html",
        p=p, kind=kind, mode=mode,
        due_break=due_break, work_break=work_break,
        due_count=due_count, work_count=work_count,
    )


@bp.route("/quick-add-date/<int:task_id>", methods=("POST",))
@login_required
def quick_add_date(task_id: int):
    """Add a work-on date to a task straight from a calendar card.

    An empty date input means 'today' (that is the pre-filled default on the
    dashboard); an unparseable value redirects back with a flash message
    instead of erroring out.
    """
    task = Task.query.get_or_404(task_id)
    try:
        validate_csrf(request.form.get("csrf_token", ""))
    except BadRequest:
        abort(400)
    raw = (request.form.get("date") or "").strip()
    if raw:
        try:
            d = periods.parse_day(raw)
        except ValueError:
            flash(f"Could not read date {raw!r}; pick one from the picker.", "error")
            return redirect(request.form.get("next") or url_for("calendarview.view", kind="day"))
    else:
        d = periods.today()
    if not TaskDate.query.filter_by(task_id=task.id, date=d).first():
        db.session.add(TaskDate(task_id=task.id, date=d))
        db.session.commit()
        flash(f"Added {d.strftime('%a %-d %b %Y')} as a work-on date for {task.title!r}.", "ok")
    return redirect(request.form.get("next") or url_for("calendarview.view", kind="day"))
