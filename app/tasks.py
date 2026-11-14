"""Task management: CRUD, recursive sub-tasks, due dates, work-on dates."""
import datetime as dt

from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from flask_login import login_required
from flask_wtf.csrf import validate_csrf
from werkzeug.exceptions import BadRequest

from .forms import (
    DeleteForm, DueDateForm, NotesForm, QuickTaskForm, TaskForm, WorkDateForm,
)
from .models import Project, Task, TaskDate, db
from .queries import due_in, work_rows

bp = Blueprint("tasks", __name__, url_prefix="/tasks")

MAX_WORK_RANGE_DAYS = 90


def _project_choices(form: TaskForm) -> None:
    form.project_id.choices = [(0, "— No project —")] + [
        (p.id, p.name) for p in Project.query.order_by(Project.name)
    ]


def _parent_choices(form: TaskForm, task: Task | None = None) -> None:
    q = Task.query
    if task is not None:
        excluded = {task.id, *task.descendant_ids()}
        q = q.filter(~Task.id.in_(excluded))
    form.parent_id.choices = [(0, "— Top level —")] + [
        (t.id, t.title) for t in q.order_by(Task.title).all()
    ]


def _parse_ids(form) -> tuple[int, int]:
    try:
        return int(form.project_id.data or 0), int(form.parent_id.data or 0)
    except (TypeError, ValueError):
        return 0, 0


@bp.route("/")
@login_required
def index():
    """All top-level tasks with their full sub-task trees."""
    from .queries import filter_task_tree

    roots = (
        Task.query
        .filter(Task.parent_id.is_(None))
        .order_by(Task.done, Task.due_date.is_(True), Task.due_date, Task.position, Task.id)
        .all()
    )
    hide = session.get("hide_completed", False)
    if hide:
        roots = filter_task_tree(roots, hide_done=True)
    return render_template("tasks/index.html", roots=roots, delete_form=DeleteForm())


def _save_from_form(form: TaskForm, task: Task) -> Task:
    project_id, parent_id = _parse_ids(form)
    task.title = form.title.data.strip()
    task.notes = form.notes.data or ""
    task.due_date = form.due_date.data
    task.project_id = project_id or None
    task.parent_id = parent_id or None
    return task


@bp.route("/new", methods=("GET", "POST"))
@login_required
def create():
    parent = None
    if request.args.get("parent"):
        parent = Task.query.get_or_404(request.args["parent"], description="Parent task")
    preset_project = request.args.get("project")

    form = TaskForm()
    _project_choices(form)
    _parent_choices(form)

    if request.method == "GET":
        if parent is not None:
            form.parent_id.data = parent.id
        if preset_project:
            try:
                form.project_id.data = int(preset_project)
            except ValueError:
                pass

    if form.validate_on_submit():
        task = Task()
        _save_from_form(form, task)
        task.position = task.next_sibling_position()
        db.session.add(task)
        db.session.commit()
        flash(f"Task {task.title!r} created.", "ok")
        return redirect(url_for("tasks.detail", task_id=task.id))
    return render_template("tasks/form.html", form=form, task=None, parent=parent)


@bp.route("/<int:task_id>")
@login_required
def detail(task_id: int):
    task = Task.query.get_or_404(task_id)
    return render_template(
        "tasks/detail.html",
        task=task,
        ancestors=task.ancestors(),
        delete_form=DeleteForm(),
        work_form=WorkDateForm(),
        due_form=DueDateForm(data={"due_date": task.due_date} if task.due_date else {}),
        subtask_form=QuickTaskForm(),
    )


@bp.route("/<int:task_id>/edit", methods=("GET", "POST"))
@login_required
def edit(task_id: int):
    task = Task.query.get_or_404(task_id)
    form = TaskForm(obj=task)
    _project_choices(form)
    _parent_choices(form, task)
    if form.validate_on_submit():
        _save_from_form(form, task)
        db.session.commit()
        flash("Task updated.", "ok")
        return redirect(url_for("tasks.detail", task_id=task.id))
    return render_template("tasks/form.html", form=form, task=task, parent=None)


@bp.route("/<int:task_id>/toggle", methods=("POST",))
@login_required
def toggle(task_id: int):
    task = Task.query.get_or_404(task_id)
    task.done = not task.done
    db.session.commit()
    return redirect(request.form.get("next") or url_for("tasks.detail", task_id=task.id))


@bp.route("/<int:task_id>/notes", methods=("POST",))
@login_required
def notes(task_id: int):
    """Autosave endpoint for the task notes editor."""
    task = Task.query.get_or_404(task_id)
    try:
        validate_csrf(request.headers.get("X-CSRFToken", ""))
    except BadRequest:
        return {"ok": False, "error": "bad csrf"}, 400
    body = request.get_json(silent=True) or {}
    task.notes = (body.get("notes") or "")[:100_000]
    db.session.commit()
    return {"ok": True, "saved_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


@bp.route("/<int:task_id>/delete", methods=("POST",))
@login_required
def delete(task_id: int):
    task = Task.query.get_or_404(task_id)
    ids = [task.id, *task.descendant_ids()]
    TaskDate.query.filter(TaskDate.task_id.in_(ids)).delete(synchronize_session=False)
    Task.query.filter(Task.id.in_(ids)).delete(synchronize_session=False)
    db.session.commit()
    flash(f"Deleted {len(ids)} task(s) including sub-tasks.", "ok")
    nxt = request.form.get("next")
    return redirect(nxt or url_for("tasks.index"))


@bp.route("/<int:task_id>/subtasks", methods=("POST",))
@login_required
def add_subtask(task_id: int):
    task = Task.query.get_or_404(task_id)
    form = QuickTaskForm()
    if form.validate_on_submit():
        child = Task(title=form.title.data.strip(), parent_id=task.id, project_id=task.project_id)
        child.position = child.next_sibling_position()
        db.session.add(child)
        db.session.commit()
        flash("Sub-task added.", "ok")
    return redirect(url_for("tasks.detail", task_id=task.id))


@bp.route("/<int:task_id>/due", methods=("POST",))
@login_required
def set_due(task_id: int):
    task = Task.query.get_or_404(task_id)
    form = DueDateForm()
    if form.validate_on_submit():
        task.due_date = form.due_date.data
        db.session.commit()
        flash("Due date updated.", "ok")
    return redirect(url_for("tasks.detail", task_id=task.id))


@bp.route("/<int:task_id>/work-dates", methods=("POST",))
@login_required
def add_work_dates(task_id: int):
    """Add a single date or an inclusive range of work-on dates."""
    task = Task.query.get_or_404(task_id)
    form = WorkDateForm()
    if form.validate_on_submit():
        end = form.end.data or form.start.data
        d, existing = form.start.data, {
            td.date for td in TaskDate.query.filter_by(task_id=task.id)
        }
        added = []
        while d <= end:
            if d not in existing:
                db.session.add(TaskDate(task_id=task.id, date=d))
                added.append(d)
            d += dt.timedelta(days=1)
        db.session.commit()
        n = len(added)
        flash(f"Added {n} work date{'' if n == 1 else 's'}.", "ok")
    return redirect(url_for("tasks.detail", task_id=task.id))


@bp.route("/<int:task_id>/work-dates/<int:td_id>/remove", methods=("POST",))
@login_required
def remove_work_date(task_id: int, td_id: int):
    task = Task.query.get_or_404(task_id)
    td = TaskDate.query.get_or_404(td_id)
    if td.task_id != task.id:
        abort(400)
    db.session.delete(td)
    db.session.commit()
    flash("Work date removed.", "ok")
    return redirect(url_for("tasks.detail", task_id=task.id))


@bp.route("/search")
@login_required
def search():
    """JSON task-name search for the typeahead on the project page.

    Empty query returns all tasks (capped); otherwise titles containing the
    query, case-insensitive.
    """
    q = (request.args.get("q") or "").strip()
    query = Task.query
    if q:
        query = query.filter(Task.title.ilike(f"%{q}%"))
    tasks = query.order_by(Task.title).limit(50).all()
    return {
        "results": [
            {
                "id": t.id,
                "title": t.title,
                "project": t.project.name if t.project else None,
                "done": t.done,
            }
            for t in tasks
        ],
        "capped": query.count() > 50,
    }


@bp.route("/scheduled")
@login_required
def scheduled():
    """Loose ends: tasks with dates (for reference) — simple two lists."""
    t = dt.date.today()
    overdue = due_in(dt.date(2000, 1, 1), t - dt.timedelta(days=1))
    working_now = [
        (task, d) for task, d in work_rows(t, t)
    ]
    return render_template("tasks/scheduled.html", overdue=overdue, working_now=working_now)
