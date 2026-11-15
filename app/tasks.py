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

from . import periods
from .access import batch_or_404, task_or_404
from .forms import (
    DeleteForm,
    DueDateForm,
    QuickTaskForm,
    TaskForm,
    WorkDateForm,
)
from .models import DeletedBatch, Project, Task, TaskDate, db, utcnow
from .queries import (
    build_forest,
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
    form.project_id.choices = [(0, "— No project —")] + [
        (p.id, p.name)
        for p in Project.query.filter_by(user_id=current_user.id)
        .order_by(Project.name)
    ]


def _parent_choices(form: TaskForm, task: Task | None = None) -> None:
    q = Task.query.filter(Task.user_id == current_user.id)
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


def _save_from_form(form: TaskForm, task: Task) -> bool:
    project_id, parent_id = _parse_ids(form)
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
    """All top-level tasks with their full sub-task trees, filterable."""
    from .queries import load_all_tasks

    q = (request.args.get("q") or "").strip()[:200]
    project_id = request.args.get("project", type=int) or None
    status = request.args.get("status", "all")
    if status not in ("all", "open", "done"):
        status = "all"
    # "Done" is an explicit request: don't let the hide-completed preference
    # silently turn the result into an empty page.
    hide = session.get("hide_completed", False) and status != "done"

    all_tasks = prepare_tasks(load_all_tasks(current_user.id))

    def matches(task: Task) -> bool:
        if q and q.casefold() not in task.title.casefold():
            return False
        if project_id and task.project_id != project_id:
            return False
        if status == "open" and task.done:
            return False
        return status != "done" or task.done

    filtered = bool(q or project_id or status != "all")
    roots = build_forest(
        all_tasks,
        hide_done=hide,
        predicate=matches if filtered else None,
    )
    return render_template(
        "tasks/index.html",
        roots=roots,
        delete_form=DeleteForm(),
        projects=Project.query.filter_by(user_id=current_user.id)
        .order_by(Project.name)
        .all(),
        q=q,
        project_filter=project_id,
        status=status,
        filtered=filtered,
    )


@bp.route("/new", methods=("GET", "POST"))
@login_required
def create():
    parent = None
    parent_id = request.args.get("parent", type=int)
    if parent_id:
        parent = task_or_404(parent_id)
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
    from .queries import load_all_tasks

    prepare_tasks(load_all_tasks(current_user.id))
    return render_template(
        "tasks/detail.html",
        task=task,
        ancestors=task.ancestors(),
        delete_form=DeleteForm(),
        work_form=WorkDateForm(),
        due_form=DueDateForm(data={"due_date": task.due_date} if task.due_date else {}),
        subtask_form=QuickTaskForm(),
        projects=Project.query.filter_by(user_id=current_user.id)
        .order_by(Project.name)
        .all(),
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
    return redirect_back(
        request.form.get("next"), url_for("tasks.detail", task_id=task.id)
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
        while node.parent_id in ids:
            depth += 1
            node = db.session.get(Task, node.parent_id)
            if node is None:
                break
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
        DeletedBatch.query
        .filter_by(user_id=current_user.id)
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
    flash(
        f"Deleted {len(ids)} task(s) including sub-tasks. "
        "You can undo this from Trash.",
        "ok",
    )
    return redirect_back(request.form.get("next"), url_for("tasks.index"))


@bp.route("/trash")
@login_required
def trash():
    batches = (
        DeletedBatch.query
        .filter_by(user_id=current_user.id)
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
    flash("Deleted batch removed permanently.", "ok")
    return redirect(url_for("tasks.trash"))


@bp.route("/trash/empty", methods=("POST",))
@login_required
def empty_trash():
    DeletedBatch.query.filter_by(user_id=current_user.id).delete()
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
    return redirect(url_for("tasks.detail", task_id=task.id))


@bp.route("/<int:task_id>/due", methods=("POST",))
@login_required
def set_due(task_id: int):
    task = task_or_404(task_id)
    form = DueDateForm()
    if form.validate_on_submit():
        task.due_date = form.due_date.data
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
    td = TaskDate.query.get_or_404(td_id)
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
    project_id = request.form.get("project_id", type=int) or None
    project = db.session.get(Project, project_id) if project_id else None
    if project_id and (project is None or project.user_id != current_user.id):
        abort(400)
    tasks = task.descendant_objects()
    for item in tasks:
        item.project_id = project_id
    db.session.commit()
    flash(f"Moved {len(tasks)} task(s) to the selected project.", "ok")
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
    db.session.commit()
    flash(f"Updated the due date for {len(tasks)} task(s).", "ok")
    return redirect(url_for("tasks.detail", task_id=task.id))


@bp.route("/<int:task_id>/reorder", methods=("POST",))
@login_required
def reorder(task_id: int):
    """Move a task before/after a sibling (drag-and-drop target)."""
    task = task_or_404(task_id)
    data = request.get_json(silent=True) or {}
    target_id = data.get("target_id")
    try:
        target_id = int(target_id)
    except (TypeError, ValueError):
        return {"ok": False, "error": "target_id required"}, 400
    target = task_or_404(target_id)
    if target.id == task.id or target.id in task.descendant_ids():
        return {"ok": False, "error": "cannot move a task into itself"}, 400

    new_parent = target.parent_id
    task.parent_id = new_parent
    project_id = target.project_id
    for item in task.descendant_objects():
        item.project_id = project_id

    if new_parent is None:
        siblings = Task.query.filter(
            Task.user_id == current_user.id,
            Task.parent_id.is_(None),
            Task.id != task.id,
        )
    else:
        siblings = Task.query.filter(
            Task.user_id == current_user.id,
            Task.parent_id == new_parent,
            Task.id != task.id,
        )
    siblings = siblings.order_by(Task.position, Task.id).all()
    index = next((i for i, item in enumerate(siblings) if item.id == target.id), len(siblings))
    if data.get("position") == "after":
        index += 1
    siblings.insert(index, task)
    for position, item in enumerate(siblings, start=1):
        item.position = position
    db.session.commit()
    return {"ok": True}


@bp.route("/search")
@login_required
def search():
    """JSON task-name search for the typeahead on the project page."""
    q = (request.args.get("q") or "").strip()[:200]
    query = Task.query.filter(
        Task.user_id == current_user.id
    ).options(joinedload(Task.project))
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
