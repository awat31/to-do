"""Database models: projects, tasks (recursive), work dates, users."""
import datetime as dt
import sqlite3

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import ForeignKeyConstraint, UniqueConstraint, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, validates
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record):
    """Enforce foreign keys and use WAL for the multi-worker SQLite setup."""
    if not isinstance(dbapi_connection, sqlite3.Connection):
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA journal_mode=WAL")
    finally:
        cursor.close()


@event.listens_for(Session, "after_flush")
def _bump_task_version(session, flush_context):
    """Invalidate per-request derived caches after any write.

    ``Task.progress()`` and friends cache their results on the instance so a
    rendered tree costs no extra queries. A flush can change the answer, so
    the cache carries a version that is bumped here.
    """
    session.info["todo_task_version"] = session.info.get("todo_task_version", 0) + 1


def _task_version() -> int:
    return db.session.info.get("todo_task_version", 0)


def utcnow() -> dt.datetime:
    """Naive UTC timestamp (deprecation-safe on Python 3.12+)."""
    return dt.datetime.now(dt.UTC).replace(tzinfo=None)


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=utcnow,
        onupdate=utcnow,
    )


class User(db.Model, UserMixin, TimestampMixin):
    __tablename__ = "user"
    __table_args__ = (
        UniqueConstraint("username_key", name="uq_user_username_key"),
    )

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    username_key = db.Column(db.String(255), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    failed_login_count = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)

    projects = db.relationship("Project", back_populates="user")
    tasks = db.relationship("Task", back_populates="user")
    deleted_batches = db.relationship("DeletedBatch", back_populates="user")

    @validates("username")
    def _normalize_username(self, key: str, value: str) -> str:
        self.username_key = value.casefold()
        return value

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def is_locked(self) -> bool:
        return bool(self.locked_until and self.locked_until > utcnow())

    def register_failure(self, max_attempts: int, lock_minutes: int) -> None:
        self.failed_login_count = (self.failed_login_count or 0) + 1
        if self.failed_login_count >= max_attempts:
            self.locked_until = utcnow() + dt.timedelta(minutes=lock_minutes)
            self.failed_login_count = 0

    def clear_failures(self) -> None:
        self.failed_login_count = 0
        self.locked_until = None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.username!r}>"


class Project(db.Model, TimestampMixin):
    __tablename__ = "project"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_project_id_user_id"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    name = db.Column(db.String(120), nullable=False)
    color = db.Column(db.String(20), nullable=False, default="blue")
    notes = db.Column(db.Text, nullable=False, default="")

    user = db.relationship("User", back_populates="projects")
    tasks = db.relationship(
        "Task", back_populates="project", lazy="dynamic",
        order_by="Task.position, Task.id",
        foreign_keys="Task.project_id",
    )

    @property
    def root_tasks(self):
        return self.tasks.filter(Task.parent_id.is_(None))

    @property
    def task_count(self) -> int:
        return self.tasks.count()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Project {self.name!r}>"


class TaskDate(db.Model, TimestampMixin):
    """A single date on which the user wants to work on a task.

    Date ranges and multi-date schedules are expanded into one row per day
    at write time, which keeps every calendar query a simple BETWEEN.
    """
    __tablename__ = "task_date"
    __table_args__ = (UniqueConstraint("task_id", "date", name="uq_task_date"),)

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(
        db.Integer, db.ForeignKey("task.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    date = db.Column(db.Date, nullable=False, index=True)

    task = db.relationship("Task", back_populates="work_dates")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TaskDate {self.task_id} {self.date}>"


class Task(db.Model, TimestampMixin):
    __tablename__ = "task"
    __table_args__ = (
        UniqueConstraint("id", "user_id", name="uq_task_id_user_id"),
        ForeignKeyConstraint(
            ["project_id", "user_id"],
            ["project.id", "project.user_id"],
            name="fk_task_project_owner",
        ),
        ForeignKeyConstraint(
            ["parent_id", "user_id"],
            ["task.id", "task.user_id"],
            name="fk_task_parent_owner",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["recurrence_source_id", "user_id"],
            ["task.id", "task.user_id"],
            name="fk_task_recurrence_source_owner",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    title = db.Column(db.String(255), nullable=False)
    notes = db.Column(db.Text, nullable=False, default="")
    done = db.Column(db.Boolean, nullable=False, default=False, index=True)
    due_date = db.Column(db.Date, nullable=True, index=True)
    position = db.Column(db.Integer, nullable=False, default=0)
    recurrence = db.Column(db.String(20), nullable=False, default="none")
    recurrence_source_id = db.Column(
        db.Integer, db.ForeignKey("task.id", ondelete="SET NULL"), nullable=True, index=True,
    )

    project_id = db.Column(
        db.Integer, db.ForeignKey("project.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    parent_id = db.Column(
        db.Integer, db.ForeignKey("task.id", ondelete="CASCADE"), nullable=True, index=True,
    )

    user = db.relationship("User", back_populates="tasks")
    project = db.relationship(
        "Project", back_populates="tasks", foreign_keys=[project_id]
    )
    parent = db.relationship(
        "Task", remote_side=[id], foreign_keys=[parent_id],
        backref=db.backref("children", order_by="Task.position, Task.id"),
    )
    work_dates = db.relationship(
        "TaskDate", back_populates="task", cascade="all, delete-orphan",
        order_by="TaskDate.date",
    )

    # --- hierarchy helpers -------------------------------------------------
    def descendant_ids(self) -> list[int]:
        """All ids in the subtree rooted at this task (excluding itself)."""
        rows = db.session.execute(
            text(
                "WITH RECURSIVE sub(id) AS ("
                "  SELECT :id"
                "  UNION ALL"
                "  SELECT t.id FROM task t JOIN sub s ON t.parent_id = s.id"
                ") SELECT id FROM sub WHERE id != :id"
            ),
            {"id": self.id},
        ).fetchall()
        return [r[0] for r in rows]

    def ancestors(self) -> list["Task"]:
        """Chain of parents, root first."""
        if "_ancestors_cache" in self.__dict__:
            return self._ancestors_cache
        chain, node = [], self.parent
        seen = set()
        while node is not None and node.id not in seen and node.user_id == self.user_id:
            seen.add(node.id)
            chain.append(node)
            node = node.parent
        return list(reversed(chain))

    def progress(self) -> tuple[int, int] | None:
        """(done_leaves, total_leaves) over the subtree's leaf tasks.

        A task with no children is not a "project" and gets no progress bar.
        Views that render many cards pre-compute this in one pass and set
        ``_progress_cache``; the fallback keeps single-task detail pages
        correct without any special setup.
        """
        if (
            "_progress_cache" in self.__dict__
            and self.__dict__.get("_progress_version") == _task_version()
        ):
            return self._progress_cache
        ids = self.descendant_ids()
        if not ids:
            return None
        tasks = Task.query.filter(
            Task.id.in_([self.id, *ids]),
            Task.user_id == self.user_id,
        ).all()
        leaves = [t for t in tasks if not t.children]
        if not leaves:
            return None
        return sum(1 for t in leaves if t.done), len(leaves)

    def descendant_objects(self) -> list["Task"]:
        """Every task in this subtree, including itself."""
        ids = self.descendant_ids()
        if not ids:
            return [self]
        return Task.query.filter(
            Task.id.in_([self.id, *ids]),
            Task.user_id == self.user_id,
        ).all()

    def next_sibling_position(self) -> int:
        q = db.session.query(db.func.max(Task.position)).filter(
            Task.parent_id == self.parent_id,
            Task.user_id == self.user_id,
        )
        if self.project_id is not None:
            q = q.filter(Task.project_id == self.project_id)
        else:
            q = q.filter(Task.project_id.is_(None))
        current = q.scalar()
        return (current or 0) + 1

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Task {self.id} {self.title!r}>"


class DeletedBatch(db.Model, TimestampMixin):
    """A JSON snapshot of a deleted subtree, so it can be restored."""
    __tablename__ = "deleted_batch"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    summary = db.Column(db.String(255), nullable=False, default="")
    payload = db.Column(db.Text, nullable=False, default="")

    user = db.relationship("User", back_populates="deleted_batches")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DeletedBatch {self.id} {self.summary!r}>"
