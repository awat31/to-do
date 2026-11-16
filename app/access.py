"""Ownership-scoped lookups.

Every user has their own task/project silo. Looking a record up by id alone
would let another user's id leak data or be modified, so all routes use these
helpers instead. ``first_or_404`` deliberately returns the same 404 for a
missing record and for somebody else's record.
"""
from flask_login import current_user

from .models import DeletedBatch, Project, Task


def user_projects():
    """Base query for the current user's projects (ownership filter applied)."""
    return Project.query.filter_by(user_id=current_user.id)


def user_tasks():
    """Base query for the current user's tasks (ownership filter applied)."""
    return Task.query.filter_by(user_id=current_user.id)


def user_batches():
    """Base query for the current user's trash batches."""
    return DeletedBatch.query.filter_by(user_id=current_user.id)


def task_or_404(task_id: int) -> Task:
    return Task.query.filter_by(
        id=task_id, user_id=current_user.id
    ).first_or_404()


def project_or_404(project_id: int) -> Project:
    return Project.query.filter_by(
        id=project_id, user_id=current_user.id
    ).first_or_404()


def batch_or_404(batch_id: int) -> DeletedBatch:
    return DeletedBatch.query.filter_by(
        id=batch_id, user_id=current_user.id
    ).first_or_404()
