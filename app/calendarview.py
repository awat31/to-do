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
from flask_login import login_required
from flask_wtf.csrf import validate_csrf
from werkzeug.exceptions import BadRequest

from . import periods, queries
from .models import Task, TaskDate, db
from .utils import redirect_back

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
    return {"due": 1 if due_sel else 0, "work": 1 if work_sel else 0}


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
    keyfn = _keyfn(kind)
    hide = session.get("hide_completed", False)

    due_break = _due_breakdown(kind, p["start"], p["end"], keyfn) if due_sel else []
    work_break = _work_breakdown(kind, p["start"], p["end"], keyfn) if work_sel else []
    if hide:
        due_break = _strip_done(due_break)
        work_break = _strip_done(work_break)
    due_count = sum(len(tasks) for _, days in due_break for _, tasks in days)
    work_count = sum(len(tasks) for _, days in work_break for _, tasks in days)
    queries.prepare_tasks()

    return render_template(
        "calendar.html",
        p=p, kind=kind, due_sel=due_sel, work_sel=work_sel,
        due_break=due_break, work_break=work_break,
        due_count=due_count, work_count=work_count,
    )


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
