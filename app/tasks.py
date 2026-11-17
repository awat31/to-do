"""Task management: CRUD, recursive sub-tasks, due dates, work-on dates."""
import datetime as dt
import json

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload
from wtforms.fields.choices import SelectChoice

from . import periods
from .access import batch_or_404, task_or_404, user_batches, user_projects, user_tasks
from .forms import (
    DeleteForm,
    DueDateForm,
    QuickTaskForm,
    TaskForm,
    WorkDateForm,
)
from .models import DeletedBatch, Project, Task, TaskDate, db, utcnow
from .queries import (
    due_in,
    prepare_tasks,
    work_rows,
)
from .utils import (
    csrf_valid,
    flash_form_errors,
    notes_payload,
    redirect_back,
    shift_month,
)

bp = Blueprint("tasks", __name__, url_prefix="/tasks")


def _project_choices(form: TaskForm) -> None:
    form.project_id.choices = [SelectChoice("0", "— No project —")] + [
        SelectChoice(str(p.id), p.name)
        for p in user_projects().order_by(Project.name)
    ]


def _parent_choices(form: TaskForm, task: Task | None = None) -> None:
    q = user_tasks()
    if task is not None:
        excluded = {task.id, *task.descendant_ids()}
        q = q.filter(~Task.id.in_(excluded))
    form.parent_id.choices = [SelectChoice("0", "— Top level —")] + [
        SelectChoice(str(t.id), t.title) for t in q.order_by(Task.title).all()
    ]


def _save_from_form(form: TaskForm, task: Task) -> bool:
    project_id = form.project_id.data or 0
    parent_id = form.parent_id.data or 0
    project = db.session.get(Project, project_id) if project_id else None
    if project_id and (project is None or project.user_id != current_user.id):
        form.project_id.errors.append("Choose one of your own projects.")
        return False
    parent = db.session.get(Task, parent_id) if parent_id else None
    if parent_id and (parent is None or parent.user_id != current_user.id):
        form.parent_id.errors.append("Choose one of your own tasks as the parent.")
        return False
    if (
        task.id is not None
        and parent_id
        and (parent_id == task.id or parent_id in task.descendant_ids())
    ):
        form.parent_id.errors.append(
            "A task cannot be nested under itself or one of its sub-tasks."
        )
        return False
    task.title = form.title.data.strip()
    task.notes = form.notes.data or ""
    task.due_date = form.due_date.data
    task.recurrence = form.recurrence.data or "none"
    task.project_id = project_id or None
    task.parent_id = parent_id or None
    return True


def _recurrence_date(d: dt.date, recurrence: str) -> dt.date | None:
    if recurrence == "daily":
        return d + dt.timedelta(days=1)
    if recurrence == "weekly":
        return d + dt.timedelta(days=7)
    if recurrence == "monthly":
        return shift_month(d, 1)
    return None


def _due_date_sort_key(task: Task) -> tuple:
    return (
        task.done,
        task.due_date is None,
        task.due_date or dt.date.max,
        task.position,
        task.id,
    )


def _project_sort_key(task: Task) -> tuple:
    return (
        task.done,
        # \uffff (the highest Unicode code point) sorts unassigned tasks
        # after every named project.
        task.project.name.casefold() if task.project else "\uffff",
        task.position,
        task.id,
    )


def move_subtree(task: Task, project_id: int | None) -> list[Task]:
    """Point a task and its whole subtree at ``project_id``; returns the tasks."""
    moved = task.descendant_objects()
    for item in moved:
        item.project_id = project_id
    return moved


def _spawn_recurrence(task: Task) -> Task | None:
    """Create the next occurrence when a repeating task is completed."""
    if task.recurrence == "none" or task.due_date is None:
        return None
    if Task.query.filter_by(
        recurrence_source_id=task.id, user_id=task.user_id
    ).first() is not None:
        return None
    next_due = _recurrence_date(task.due_date, task.recurrence)
    if next_due is None:
        return None
    delta = next_due - task.due_date
    clone = Task(
        user_id=task.user_id,
        title=task.title,
        notes=task.notes,
        done=False,
        due_date=next_due,
        position=task.next_sibling_position(),
        recurrence=task.recurrence,
        recurrence_source_id=task.id,
        project_id=task.project_id,
        parent_id=task.parent_id,
    )
    db.session.add(clone)
    db.session.flush()
    for work_date in task.work_dates:
        db.session.add(TaskDate(task_id=clone.id, date=work_date.date + delta))
    return clone


@bp.route("/")
@login_required
def index():
    """Top-level tasks, filterable and sortable."""
    q = (request.args.get("q") or "").strip()[:200]
    project_id = request.args.get("project", type=int) or None
    status = request.args.get("status", "all")
    if status not in ("all", "open", "done"):
        status = "all"
    sort_order = request.args.get("sort", "default")
    if sort_order not in ("default", "due", "project"):
        sort_order = "default"
    # "Done" is an explicit request: don't let the hide-completed preference
    # silently turn the result into an empty page.
    hide = session.get("hide_completed", False) and status != "done"

    all_tasks = prepare_tasks(user_id=current_user.id)

    def matches(task: Task) -> bool:
        if q and q.casefold() not in task.title.casefold():
            return False
        if project_id and task.project_id != project_id:
            return False
        if status == "open" and task.done:
            return False
        return status != "done" or task.done

    filtered = bool(q or project_id or status != "all")
    roots = [
        task for task in all_tasks
        if task.parent_id is None
        and (not hide or not task.done)
        and (not filtered or matches(task))
    ]
    sort_key = {
        "due": _due_date_sort_key,
        "project": _project_sort_key,
    }.get(sort_order, _due_date_sort_key)
    roots.sort(key=sort_key)
    return render_template(
        "tasks/index.html",
        roots=roots,
        delete_form=DeleteForm(),
        projects=user_projects().order_by(Project.name).all(),
        q=q,
        project_filter=project_id,
        status=status,
        sort_order=sort_order,
        filtered=filtered,
    )


@bp.route("/new", methods=("GET", "POST"))
@login_required
def create():
    parent = None
    parent_id = request.args.get("parent", type=int)
    if parent_id:
        parent = task_or_404(parent_id)
    preset_project = request.args.get("project", type=int)

    form = TaskForm()
    _project_choices(form)
    _parent_choices(form)

    if request.method == "GET":
        if parent is not None:
            form.parent_id.data = parent.id
        if preset_project is not None:
            form.project_id.data = preset_project

    if form.validate_on_submit():
        task = Task(user_id=current_user.id)
        if _save_from_form(form, task):
            task.position = task.next_sibling_position()
            db.session.add(task)
            db.session.commit()
            flash(f"Task {task.title!r} created.", "ok")
            return redirect(url_for("tasks.detail", task_id=task.id))
    return render_template("tasks/form.html", form=form, task=None, parent=parent)


@bp.route("/<int:task_id>")
@login_required
def detail(task_id: int):
    task = task_or_404(task_id)
    prepare_tasks(user_id=current_user.id)
    return render_template(
        "tasks/detail.html",
        task=task,
        ancestors=task.ancestors(),
        delete_form=DeleteForm(),
        work_form=WorkDateForm(),
        due_form=DueDateForm(data={"due_date": task.due_date} if task.due_date else {}),
        subtask_form=QuickTaskForm(),
        projects=user_projects().order_by(Project.name).all(),
    )


@bp.route("/<int:task_id>/edit", methods=("GET", "POST"))
@login_required
def edit(task_id: int):
    task = task_or_404(task_id)
    form = TaskForm(obj=task)
    _project_choices(form)
    _parent_choices(form, task)
    if form.validate_on_submit() and _save_from_form(form, task):
        db.session.commit()
        flash("Task updated.", "ok")
        return redirect(url_for("tasks.detail", task_id=task.id))
    return render_template("tasks/form.html", form=form, task=task, parent=None)


@bp.route("/<int:task_id>/toggle", methods=("POST",))
@login_required
def toggle(task_id: int):
    task = task_or_404(task_id)
    becoming_done = not task.done
    task.done = becoming_done
    spawned = _spawn_recurrence(task) if becoming_done else None
    db.session.commit()
    if spawned is not None:
        flash(f"Next {task.recurrence} occurrence created for {spawned.due_date:%a %-d %b %Y}.", "ok")
    fallback = (
        url_for("tasks.detail", task_id=task.parent_id)
        if task.parent_id is not None
        else url_for("tasks.detail", task_id=task.id)
    )
    return redirect_back(
        request.form.get("next"), fallback
    )


@bp.route("/<int:task_id>/notes", methods=("POST",))
@login_required
def notes(task_id: int):
    """Autosave endpoint for the task notes editor."""
    task = task_or_404(task_id)
    data, token = notes_payload()
    if not csrf_valid(token):
        return {"ok": False, "error": "bad csrf"}, 400
    if not isinstance(data.get("notes"), str):
        return {"ok": False, "error": "notes must be a string"}, 400
    task.notes = data["notes"][:current_app.config["TASK_NOTES_LIMIT"]]
    db.session.commit()
    return {"ok": True, "saved_at": utcnow().isoformat(timespec="seconds") + "Z"}


def _snapshot_batch(task: Task) -> DeletedBatch:
    tasks = task.descendant_objects()
    ids = {t.id for t in tasks}
    items = []
    for t in tasks:
        depth = 0
        node = t
        seen = {node.id}
        while node.parent_id in ids:
            parent = db.session.get(Task, node.parent_id)
            if parent is None or parent.id in seen:
                break
            depth += 1
            node = parent
            seen.add(node.id)
        items.append(
            {
                "id": t.id,
                "parent_id": t.parent_id,
                "title": t.title,
                "notes": t.notes,
                "done": t.done,
                "due_date": t.due_date.isoformat() if t.due_date else None,
                "position": t.position,
                "project_id": t.project_id,
                "recurrence": t.recurrence,
                "recurrence_source_id": t.recurrence_source_id,
                "depth": depth,
                "work_dates": [td.date.isoformat() for td in t.work_dates],
            }
        )
    items.sort(key=lambda item: item["depth"])
    summary = task.title if len(items) == 1 else f"{task.title} + {len(items) - 1} sub-task(s)"
    return DeletedBatch(
        user_id=task.user_id,
        summary=summary,
        payload=json.dumps({"tasks": items}),
    )


def _restore_batch(batch: DeletedBatch) -> int:
    payload = json.loads(batch.payload or "{}")
    items = payload.get("tasks") or []
    old_to_new: dict[int, int] = {}
    count = 0
    for item in items:
        old_parent = item.get("parent_id")
        parent = db.session.get(Task, old_parent) if old_parent else None
        if old_parent in old_to_new:
            parent_id = old_to_new[old_parent]
        elif parent is not None and parent.user_id == batch.user_id:
            parent_id = old_parent
        else:
            parent_id = None

        project_id = item.get("project_id")
        project = db.session.get(Project, project_id) if project_id else None
        if project is None or project.user_id != batch.user_id:
            project_id = None
        source_id = item.get("recurrence_source_id")
        source_id = old_to_new.get(source_id, source_id)
        source = db.session.get(Task, source_id) if source_id else None
        if source is None or source.user_id != batch.user_id:
            source_id = None

        due = item.get("due_date")
        task = Task(
            user_id=batch.user_id,
            title=item["title"],
            notes=item.get("notes", ""),
            done=bool(item.get("done")),
            due_date=dt.date.fromisoformat(due) if due else None,
            position=item.get("position", 0),
            recurrence=item.get("recurrence", "none"),
            recurrence_source_id=source_id,
            project_id=project_id,
            parent_id=parent_id,
        )
        db.session.add(task)
        db.session.flush()
        for raw in item.get("work_dates") or []:
            db.session.add(TaskDate(task_id=task.id, date=dt.date.fromisoformat(raw)))
        old_to_new[item["id"]] = task.id
        count += 1
    return count


def _trim_trash(keep: int = 20) -> None:
    batches = (
        user_batches()
        .order_by(DeletedBatch.created_at.desc())
        .all()
    )
    removed = False
    for batch in batches[keep:]:
        db.session.delete(batch)
        removed = True
    cutoff = utcnow() - dt.timedelta(days=30)
    for batch in list(batches[:keep]):
        if batch.created_at < cutoff:
            db.session.delete(batch)
            removed = True
    if removed:
        db.session.commit()


@bp.route("/<int:task_id>/delete", methods=("POST",))
@login_required
def delete(task_id: int):
    task = task_or_404(task_id)
    ids = [task.id, *task.descendant_ids()]
    batch = _snapshot_batch(task)
    db.session.add(batch)
    db.session.flush()
    TaskDate.query.filter(TaskDate.task_id.in_(ids)).delete(synchronize_session=False)
    Task.query.filter(
        Task.id.in_(ids), Task.user_id == current_user.id
    ).delete(synchronize_session=False)
    db.session.commit()
    session["last_deleted_batch"] = batch.id
    _trim_trash()
    return redirect_back(request.form.get("next"), url_for("tasks.index"))


@bp.route("/trash")
@login_required
def trash():
    batches = (
        user_batches()
        .order_by(DeletedBatch.created_at.desc())
        .all()
    )
    return render_template("tasks/trash.html", batches=batches, delete_form=DeleteForm())


@bp.route("/trash/<int:batch_id>/restore", methods=("POST",))
@login_required
def restore(batch_id: int):
    batch = batch_or_404(batch_id)
    count = _restore_batch(batch)
    summary = batch.summary
    db.session.delete(batch)
    db.session.commit()
    session.pop("last_deleted_batch", None)
    flash(f"Restored {count} task(s) from {summary!r}.", "ok")
    return redirect(url_for("tasks.trash"))


@bp.route("/trash/<int:batch_id>/purge", methods=("POST",))
@login_required
def purge(batch_id: int):
    batch = batch_or_404(batch_id)
    db.session.delete(batch)
    db.session.commit()
    flash("Task deleted permanently", "ok")
    return redirect(url_for("tasks.trash"))


@bp.route("/trash/empty", methods=("POST",))
@login_required
def empty_trash():
    user_batches().delete()
    db.session.commit()
    session.pop("last_deleted_batch", None)
    flash("Trash emptied.", "ok")
    return redirect(url_for("tasks.trash"))


@bp.route("/<int:task_id>/subtasks", methods=("POST",))
@login_required
def add_subtask(task_id: int):
    task = task_or_404(task_id)
    form = QuickTaskForm()
    if form.validate_on_submit():
        child = Task(
            user_id=current_user.id,
            title=form.title.data.strip(),
            parent_id=task.id,
            project_id=task.project_id,
        )
        child.position = child.next_sibling_position()
        db.session.add(child)
        db.session.commit()
        flash("Sub-task added.", "ok")
    else:
        flash_form_errors(form)
    return redirect(url_for("tasks.detail", task_id=task.id) + "#add-subtask")


@bp.route("/<int:task_id>/due", methods=("POST",))
@login_required
def set_due(task_id: int):
    task = task_or_404(task_id)
    form = DueDateForm()
    if form.validate_on_submit():
        task.due_date = form.due_date.data
        if task.due_date is None:
            task.recurrence = "none"
        db.session.commit()
        flash("Due date updated.", "ok")
    else:
        flash_form_errors(form)
    return redirect(url_for("tasks.detail", task_id=task.id))


@bp.route("/<int:task_id>/work-dates", methods=("POST",))
@login_required
def add_work_dates(task_id: int):
    """Add a single date or an inclusive range of work-on dates."""
    task = task_or_404(task_id)
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
    else:
        flash_form_errors(form)
    return redirect(url_for("tasks.detail", task_id=task.id))


@bp.route("/<int:task_id>/work-dates/<int:td_id>/remove", methods=("POST",))
@login_required
def remove_work_date(task_id: int, td_id: int):
    task = task_or_404(task_id)
    td = db.session.get(TaskDate, td_id)
    if td is None:
        abort(404)
    if td.task_id != task.id:
        abort(400)
    db.session.delete(td)
    db.session.commit()
    flash("Work date removed.", "ok")
    return redirect(url_for("tasks.detail", task_id=task.id))


@bp.route("/<int:task_id>/complete-subtree", methods=("POST",))
@login_required
def complete_subtree(task_id: int):
    task = task_or_404(task_id)
    tasks = task.descendant_objects()
    for item in tasks:
        item.done = True
    # Keep recurring tasks consistent with the single-task toggle.
    for item in tasks:
        _spawn_recurrence(item)
    db.session.commit()
    flash(f"Marked {len(tasks)} task(s) done.", "ok")
    return redirect(url_for("tasks.detail", task_id=task.id))


@bp.route("/<int:task_id>/reassign", methods=("POST",))
@login_required
def reassign_subtree(task_id: int):
    task = task_or_404(task_id)
    raw_project_id = (request.form.get("project_id") or "").strip()
    try:
        project_id = int(raw_project_id) if raw_project_id else None
    except ValueError:
        abort(400)
    project = db.session.get(Project, project_id) if project_id else None
    if project_id and (project is None or project.user_id != current_user.id):
        abort(400)
    count = len(move_subtree(task, project_id))
    db.session.commit()
    flash(f"Moved {count} task(s) to the selected project.", "ok")
    return redirect(url_for("tasks.detail", task_id=task.id))


@bp.route("/<int:task_id>/reschedule", methods=("POST",))
@login_required
def reschedule_subtree(task_id: int):
    task = task_or_404(task_id)
    raw = (request.form.get("due_date") or "").strip()
    try:
        due = periods.parse_day(raw) if raw else None
    except ValueError:
        flash("That due date was not valid.", "error")
        return redirect(url_for("tasks.detail", task_id=task.id))
    tasks = task.descendant_objects()
    for item in tasks:
        item.due_date = due
        if due is None:
            item.recurrence = "none"
    db.session.commit()
    flash(f"Updated the due date for {len(tasks)} task(s).", "ok")
    return redirect(url_for("tasks.detail", task_id=task.id))


@bp.route("/search")
@login_required
def search():
    """JSON task-name search for the typeahead on the project page."""
    q = (request.args.get("q") or "").strip()[:200]
    query = user_tasks().options(joinedload(Task.project))
    if q:
        # ilike() only case-folds ASCII on SQLite; Python casefold() handles
        # the full Unicode range, so the SQL ilike is a cheap prefilter and
        # the exact comparison happens in Python.
        query = query.filter(Task.title.ilike(f"%{q}%"))
    # 51 = 50 results + one overflow probe to detect the "capped" flag.
    tasks = query.order_by(Task.title, Task.id).limit(51).all()
    if q:
        tasks = [t for t in tasks if q.casefold() in t.title.casefold()]
    capped = len(tasks) > 50
    return {
        "results": [
            {
                "id": t.id,
                "title": t.title,
                "project": t.project.name if t.project else None,
                "done": t.done,
            }
            for t in tasks[:50]
        ],
        "capped": capped,
    }


@bp.route("/scheduled")
@login_required
def scheduled():
    """Overdue and working-on-today lists (open tasks only)."""
    t = periods.today()
    overdue = due_in(
        dt.date(2000, 1, 1),
        t - dt.timedelta(days=1),
        include_done=False,
        user_id=current_user.id,
    )
    working_now = work_rows(
        t, t, include_done=False, user_id=current_user.id
    )
    prepare_tasks(user_id=current_user.id)
    return render_template("tasks/scheduled.html", overdue=overdue, working_now=working_now)
