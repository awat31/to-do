"""Projects: notebook pages that tasks can be linked to."""
from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import login_required
from flask_wtf.csrf import validate_csrf
from werkzeug.exceptions import BadRequest

from .forms import DeleteForm, ProjectForm, QuickTaskForm
from .models import Project, Task, db
from .palette import PROJECT_COLORS

bp = Blueprint("projects", __name__, url_prefix="/projects")


@bp.route("/")
@login_required
def index():
    projects = Project.query.order_by(Project.name).all()
    return render_template("projects/index.html", projects=projects, delete_form=DeleteForm())


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
    return render_template(
        "projects/detail.html",
        project=project,
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
    try:
        validate_csrf(request.headers.get("X-CSRFToken", ""))
    except BadRequest:
        return {"ok": False, "error": "bad csrf"}, 400
    body = request.get_json(silent=True) or {}
    project.notes = (body.get("notes") or "")[:500_000]
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
        if task.project_id == project.id:
            flash(f"{task.title!r} is already in this project.", "error")
        else:
            task.project_id = project.id
            db.session.commit()
            flash(f"Added {task.title!r} to this project.", "ok")
        return redirect(url_for("projects.detail", project_id=project.id))

    form = QuickTaskForm()
    if form.validate_on_submit():
        task = Task(title=form.title.data.strip(), project_id=project.id)
        task.position = task.next_sibling_position()
        db.session.add(task)
        db.session.commit()
        flash("Task added to project.", "ok")
    return redirect(url_for("projects.detail", project_id=project.id))
