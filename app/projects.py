"""Projects: notebook pages that tasks can be linked to."""
from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import func
from wtforms.fields.choices import SelectChoice

from .access import project_or_404, task_or_404, user_projects
from .tasks import move_subtree
from .forms import DeleteForm, ProjectForm, QuickTaskForm
from .models import Project, Task, db
from .palette import PROJECT_COLORS
from .queries import filter_task_tree, prepare_tasks
from .utils import csrf_valid, flash_form_errors, notes_payload, redirect_back

bp = Blueprint("projects", __name__, url_prefix="/projects")


@bp.route("/")
@login_required
def index():
    show_completed = session.get("show_completed_projects", False)
    query = user_projects().order_by(Project.position, Project.name, Project.id)
    all_projects = query.all()
    projects = [p for p in all_projects if not p.completed]
    completed = [p for p in all_projects if p.completed] if show_completed else []
    counts = dict(
        db.session.query(Task.project_id, func.count(Task.id))
        .filter(
            Task.user_id == current_user.id,
            Task.project_id.isnot(None),
        )
        .group_by(Task.project_id)
        .all()
    )
    return render_template(
        "projects/index.html",
        projects=projects,
        completed=completed,
        counts=counts,
        delete_form=DeleteForm(),
        show_completed=show_completed,
    )


@bp.route("/preferences/show-completed", methods=("POST",))
@login_required
def set_show_completed():
    """Toggle the 'show completed projects' preference (stored in session)."""
    session["show_completed_projects"] = request.form.get("show") == "1"
    return redirect_back(request.form.get("next"), url_for(".index"))


def _color_choices():
    return [SelectChoice(name, name.capitalize()) for name in PROJECT_COLORS]


@bp.route("/new", methods=("GET", "POST"))
@login_required
def create():
    form = ProjectForm()
    form.color.choices = _color_choices()
    if form.validate_on_submit():
        project = Project(
            user_id=current_user.id,
            name=form.name.data.strip(),
            color=form.color.data,
            position=(
                db.session.query(func.max(Project.position))
                .filter_by(user_id=current_user.id)
                .scalar()
                or 0
            ) + 1,
        )
        db.session.add(project)
        db.session.commit()
        flash(f"Project {project.name!r} created.", "ok")
        return redirect(url_for("projects.detail", project_id=project.id))
    return render_template("projects/form.html", form=form, project=None)


@bp.route("/reorder", methods=("POST",))
@login_required
def reorder():
    payload = request.get_json()
    project_ids = payload.get("project_ids") if isinstance(payload, dict) else None
    if not isinstance(project_ids, list) or not all(
        isinstance(project_id, int) and not isinstance(project_id, bool)
        for project_id in project_ids
    ):
        return {"ok": False, "error": "Invalid project order."}, 400

    projects = user_projects().all()
    by_id = {project.id: project for project in projects}
    if len(project_ids) != len(set(project_ids)) or set(project_ids) != set(by_id):
        return {"ok": False, "error": "Invalid project order."}, 400

    for position, project_id in enumerate(project_ids):
        by_id[project_id].position = position
    db.session.commit()
    return {"ok": True}


@bp.route("/<int:project_id>")
@login_required
def detail(project_id: int):
    project = project_or_404(project_id)
    prepare_tasks(user_id=current_user.id)
    roots = project.root_tasks.all()
    if session.get("hide_completed", False):
        roots = filter_task_tree(roots, hide_done=True)
    return render_template(
        "projects/detail.html",
        project=project,
        roots=roots,
        task_form=QuickTaskForm(),
        delete_form=DeleteForm(),
    )


@bp.route("/<int:project_id>/edit", methods=("GET", "POST"))
@login_required
def edit(project_id: int):
    project = project_or_404(project_id)
    form = ProjectForm(obj=project)
    form.color.choices = _color_choices()
    if form.validate_on_submit():
        project.name = form.name.data.strip()
        project.color = form.color.data
        db.session.commit()
        flash("Project updated.", "ok")
        return redirect(url_for("projects.detail", project_id=project.id))
    return render_template("projects/form.html", form=form, project=project)


@bp.route("/<int:project_id>/toggle-completed", methods=("POST",))
@login_required
def toggle_completed(project_id: int):
    """Mark a project as completed, or reopen it."""
    project = project_or_404(project_id)
    project.completed = not project.completed
    db.session.commit()
    if project.completed:
        flash(f"Project {project.name!r} marked as completed.", "ok")
    else:
        flash(f"Project {project.name!r} reopened.", "ok")
    return redirect_back(
        request.form.get("next"), url_for("projects.detail", project_id=project.id)
    )


@bp.route("/<int:project_id>/delete", methods=("POST",))
@login_required
def delete(project_id: int):
    project = project_or_404(project_id)
    db.session.delete(project)  # tasks keep existing, project_id becomes NULL
    db.session.commit()
    flash(f"Project {project.name!r} deleted; its tasks were kept.", "ok")
    return redirect(url_for("projects.index"))


@bp.route("/<int:project_id>/notes", methods=("POST",))
@login_required
def notes(project_id: int):
    """Autosave endpoint for the project notebook."""
    project = project_or_404(project_id)
    data, token = notes_payload()
    if not csrf_valid(token):
        return {"ok": False, "error": "bad csrf"}, 400
    if not isinstance(data.get("notes"), str):
        return {"ok": False, "error": "notes must be a string"}, 400
    project.notes = data["notes"][:current_app.config["PROJECT_NOTES_LIMIT"]]
    db.session.commit()
    return {"ok": True}


@bp.route("/<int:project_id>/add-task", methods=("POST",))
@login_required
def add_task(project_id: int):
    project = project_or_404(project_id)

    # Typeahead pick: attach an existing task to this project instead of
    # creating a duplicate.
    existing_id = request.form.get("existing_task_id", type=int)
    if existing_id:
        task = task_or_404(existing_id)
        if task.project_id == project.id and task.parent_id is None:
            flash(f"{task.title!r} is already in this project.", "error")
        else:
            moved = move_subtree(task, project.id)
            # A picked sub-task is promoted to a project root, otherwise it
            # would remain hidden under a parent in another project.
            if task.parent_id is not None:
                task.parent_id = None
                task.position = task.next_sibling_position()
            db.session.commit()
            suffix = f" and {len(moved) - 1} sub-task(s)" if len(moved) > 1 else ""
            flash(f"Added {task.title!r}{suffix} to this project.", "ok")
        return redirect(url_for("projects.detail", project_id=project.id))

    form = QuickTaskForm()
    if form.validate_on_submit():
        task = Task(
            user_id=current_user.id,
            title=form.title.data.strip(),
            project_id=project.id,
        )
        task.position = task.next_sibling_position()
        db.session.add(task)
        db.session.commit()
        flash("Task added to project.", "ok")
    else:
        flash_form_errors(form)
    return redirect(url_for("projects.detail", project_id=project.id))
