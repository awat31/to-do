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
from flask_login import login_required
from sqlalchemy import func

from .forms import DeleteForm, ProjectForm, QuickTaskForm
from .models import Project, Task, db
from .palette import PROJECT_COLORS
from .queries import filter_task_tree, prepare_tasks
from .utils import csrf_valid, flash_form_errors, notes_payload

bp = Blueprint("projects", __name__, url_prefix="/projects")


@bp.route("/")
@login_required
def index():
    projects = Project.query.order_by(Project.name).all()
    counts = dict(
        db.session.query(Task.project_id, func.count(Task.id))
        .filter(Task.project_id.isnot(None))
        .group_by(Task.project_id)
        .all()
    )
    return render_template(
        "projects/index.html", projects=projects, counts=counts, delete_form=DeleteForm()
    )


def _color_choices():
    return [(name, name.capitalize()) for name in PROJECT_COLORS]


@bp.route("/new", methods=("GET", "POST"))
@login_required
def create():
    form = ProjectForm()
    form.color.choices = _color_choices()
    if form.validate_on_submit():
        project = Project(name=form.name.data.strip(), color=form.color.data)
        db.session.add(project)
        db.session.commit()
        flash(f"Project {project.name!r} created.", "ok")
        return redirect(url_for("projects.detail", project_id=project.id))
    return render_template("projects/form.html", form=form, project=None)


@bp.route("/<int:project_id>")
@login_required
def detail(project_id: int):
    project = Project.query.get_or_404(project_id)
    prepare_tasks()
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
    project = Project.query.get_or_404(project_id)
    form = ProjectForm(obj=project)
    form.color.choices = _color_choices()
    if form.validate_on_submit():
        project.name = form.name.data.strip()
        project.color = form.color.data
        db.session.commit()
        flash("Project updated.", "ok")
        return redirect(url_for("projects.detail", project_id=project.id))
    return render_template("projects/form.html", form=form, project=project)


@bp.route("/<int:project_id>/delete", methods=("POST",))
@login_required
def delete(project_id: int):
    project = Project.query.get_or_404(project_id)
    db.session.delete(project)  # tasks keep existing, project_id becomes NULL
    db.session.commit()
    flash(f"Project {project.name!r} deleted; its tasks were kept.", "ok")
    return redirect(url_for("projects.index"))


@bp.route("/<int:project_id>/notes", methods=("POST",))
@login_required
def notes(project_id: int):
    """Autosave endpoint for the project notebook."""
    project = Project.query.get_or_404(project_id)
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
    project = Project.query.get_or_404(project_id)

    # Typeahead pick: attach an existing task to this project instead of
    # creating a duplicate.
    existing_id = request.form.get("existing_task_id", type=int)
    if existing_id:
        task = Task.query.get_or_404(existing_id)
        if task.project_id == project.id and task.parent_id is None:
            flash(f"{task.title!r} is already in this project.", "error")
        else:
            moved = task.descendant_objects()
            for item in moved:
                item.project_id = project.id
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
        task = Task(title=form.title.data.strip(), project_id=project.id)
        task.position = task.next_sibling_position()
        db.session.add(task)
        db.session.commit()
        flash("Task added to project.", "ok")
    else:
        flash_form_errors(form)
    return redirect(url_for("projects.detail", project_id=project.id))
