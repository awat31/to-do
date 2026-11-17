"""Calendar views: day / week / month / year, grouped by due or work dates.

Structure per section (Due / Working on):
    [(group_key, [(date, [tasks...]), ...]), ...]
A group is a day (day/week/month views) or a month (year view); inside a
group, tasks are listed under their exact dates.
"""
import datetime as dt

from flask import (
    Blueprint,
    abort,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required

from . import periods, queries
from .access import task_or_404
from .models import TaskDate, db
from .utils import csrf_valid, redirect_back

bp = Blueprint("calendarview", __name__, url_prefix="/calendar")


def _selection() -> tuple[bool, bool]:
    """Which sections to show, from the Due / Working on selectors.

    A 'mode=due|work|both' legacy param still works. Otherwise: no selector
    in the URL at all means both; once any selector is present, each section
    is exactly what it says (absent = off).
    """
    args = request.args
    legacy = args.get("mode")
    if legacy in ("due", "work", "both"):
        return legacy in ("due", "both"), legacy in ("work", "both")
    if "due" not in args and "work" not in args:
        return True, True
    return args.get("due") == "1", args.get("work") == "1"


def _sel_kwargs() -> dict:
    """due/work query arguments mirroring the current selection."""
    due_sel, work_sel = _selection()
    out = {"due": 1 if due_sel else 0, "work": 1 if work_sel else 0}
    layout = request.args.get("layout")
    if layout in ("list", "calendar"):
        out["layout"] = layout
    return out


def _breakdown(per_date: dict, kind: str) -> list:
    """(group_key, [(date, tasks), ...]) sorted by group then date."""
    grouped: dict = {}
    for d, tasks in per_date.items():
        grouped.setdefault(periods.group_key(kind, d), {}).setdefault(d, []).extend(tasks)
    return [(k, sorted(m.items())) for k, m in sorted(grouped.items())]


def _strip_done(breaks):
    """Drop done tasks (and then-empty dates/groups) from a breakdown."""
    out = []
    for key, days in breaks:
        kept = [(d, [t for t in ts if not t.done]) for d, ts in days]
        kept = [(d, ts) for d, ts in kept if ts]
        if kept:
            out.append((key, kept))
    return out


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

    due_sel, work_sel = _selection()
    layout_mode = request.args.get("layout", "calendar")
    if layout_mode not in ("list", "calendar"):
        layout_mode = "calendar"
    if kind == "day":
        layout_mode = "list"
    hide = session.get("hide_completed", False)

    due_break = (
        _breakdown(
            queries.due_groups(p["start"], p["end"], user_id=current_user.id), kind
        )
        if due_sel else []
    )
    work_break = (
        _breakdown(
            queries.work_groups(p["start"], p["end"], user_id=current_user.id), kind
        )
        if work_sel else []
    )
    if hide:
        due_break = _strip_done(due_break)
        work_break = _strip_done(work_break)
    due_count = sum(len(tasks) for _, days in due_break for _, tasks in days)
    work_count = sum(len(tasks) for _, days in work_break for _, tasks in days)
    month_grid = _month_grid(p, due_break, work_break) if kind == "month" else None
    week_grid = _week_grid(p, due_break, work_break) if kind == "week" else None
    year_months = _year_months(p, due_break, work_break) if kind == "year" else None
    queries.prepare_tasks(user_id=current_user.id)

    return render_template(
        "calendar.html",
        p=p, kind=kind, due_sel=due_sel, work_sel=work_sel,
        due_break=due_break, work_break=work_break,
        due_count=due_count, work_count=work_count,
        month_grid=month_grid, week_grid=week_grid,
        year_months=year_months, layout_mode=layout_mode,
    )


def _date_maps(due_break, work_break):
    due_map = {d: tasks for _, days in due_break for d, tasks in days}
    work_map = {d: tasks for _, days in work_break for d, tasks in days}
    return due_map, work_map


def _calendar_cell(day, due_map, work_map, in_month: bool, today) -> dict:
    due = due_map.get(day, [])
    work = work_map.get(day, [])
    tasks, seen = [], set()
    for task in [*due, *work]:
        if task.id not in seen:
            seen.add(task.id)
            tasks.append(task)
    return {
        "date": day,
        "in_month": in_month,
        "is_today": day == today,
        "due": due,
        "work": work,
        "tasks": tasks,
    }


def _month_grid(p, due_break, work_break) -> dict:
    """Compact Monday-first month grid."""
    due_map, work_map = _date_maps(due_break, work_break)
    first, last = p["start"], p["end"]
    grid_start = first - dt.timedelta(days=first.weekday())
    grid_end = last + dt.timedelta(days=6 - last.weekday())
    today = periods.today()
    cells = []
    day = grid_start
    while day <= grid_end:
        cells.append(
            _calendar_cell(day, due_map, work_map, first <= day <= last, today)
        )
        day += dt.timedelta(days=1)
    return {
        "cells": cells,
        "weekdays": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    }


def _week_grid(p, due_break, work_break) -> dict:
    """Seven-day calendar table for the selected week."""
    due_map, work_map = _date_maps(due_break, work_break)
    today = periods.today()
    cells = [
        _calendar_cell(
            p["start"] + dt.timedelta(days=offset), due_map, work_map, True, today
        )
        for offset in range(7)
    ]
    return {
        "cells": cells,
        "weekdays": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    }


def _year_months(p, due_break, work_break) -> list[dict]:
    """Build one linked mini-calendar for each month of the selected year."""
    months = []
    for month in range(1, 13):
        first = dt.date(p["start"].year, month, 1)
        value = periods.month_value(first)
        month_period = periods.period("month", value)
        months.append(
            {
                "month": month,
                "value": value,
                "grid": _month_grid(month_period, due_break, work_break),
            }
        )
    return months


@bp.route("/jump", methods=("GET",))
@login_required
def jump():
    """Target of the calendar popup's date picker.

    Precedence: a picked date -> day view; otherwise month+year -> month
    view; otherwise just a year -> year view.
    """
    sel = _sel_kwargs()
    raw = (request.args.get("date") or "").strip()
    if raw:
        try:
            d = periods.parse_day(raw)
        except ValueError:
            abort(400)
        return redirect(url_for(".view", kind="day", value=d.isoformat(), **sel))

    year = request.args.get("year", type=int)
    month = request.args.get("month", type=int)
    if year and month:
        try:
            d = dt.date(year, month, 1)
        except ValueError:
            abort(400)
        return redirect(url_for(".view", kind="month", value=periods.month_value(d), **sel))
    if year:
        return redirect(url_for(".view", kind="year", value=f"{year:04d}", **sel))
    return redirect(url_for(".view", kind="day", **sel))


@bp.route("/quick-add-date/<int:task_id>", methods=("POST",))
@login_required
def quick_add_date(task_id: int):
    """Add a work-on date to a task straight from a calendar card.

    An empty date input means 'today' (that is the pre-filled default on the
    dashboard); an unparseable value redirects back with a flash message
    instead of erroring out.
    """
    task = task_or_404(task_id)
    if not csrf_valid(request.form.get("csrf_token", "")):
        abort(400)
    raw = (request.form.get("date") or "").strip()
    if raw:
        try:
            d = periods.parse_day(raw)
        except ValueError:
            flash(f"Could not read date {raw!r}; pick one from the picker.", "error")
            return redirect_back(
                request.form.get("next"), url_for("calendarview.view", kind="day")
            )
    else:
        d = periods.today()
    label = d.strftime("%a %-d %b %Y")
    if TaskDate.query.filter_by(task_id=task.id, date=d).first():
        flash(f"{task.title!r} is already scheduled for {label}.", "ok")
    else:
        db.session.add(TaskDate(task_id=task.id, date=d))
        db.session.commit()
        flash(f"Added {label} as a work-on date for {task.title!r}.", "ok")
    return redirect_back(
        request.form.get("next"), url_for("calendarview.view", kind="day")
    )
