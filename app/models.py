"""Database models: projects, tasks (recursive), work dates, users."""
import datetime as dt

from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from sqlalchemy import UniqueConstraint, text
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()


def utcnow() -> dt.datetime:
    """Naive UTC timestamp (deprecation-safe on Python 3.12+)."""
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class TimestampMixin:
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    updated_at = db.Column(
        db.DateTime, nullable=False, default=utcnow,
        onupdate=utcnow,
    )


class User(db.Model, UserMixin, TimestampMixin):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.username!r}>"


class Project(db.Model, TimestampMixin):
    __tablename__ = "project"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    color = db.Column(db.String(20), nullable=False, default="blue")
    notes = db.Column(db.Text, nullable=False, default="")

    tasks = db.relationship(
        "Task", back_populates="project", lazy="dynamic",
        order_by="Task.position, Task.id",
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

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(255), nullable=False)
    notes = db.Column(db.Text, nullable=False, default="")
    done = db.Column(db.Boolean, nullable=False, default=False, index=True)
    due_date = db.Column(db.Date, nullable=True, index=True)
    position = db.Column(db.Integer, nullable=False, default=0)

    project_id = db.Column(
        db.Integer, db.ForeignKey("project.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    parent_id = db.Column(
        db.Integer, db.ForeignKey("task.id", ondelete="CASCADE"), nullable=True, index=True,
    )

    project = db.relationship("Project", back_populates="tasks")
    parent = db.relationship("Task", remote_side=[id], backref=db.backref("children"))
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
        chain, node = [], self.parent
        seen = set()
        while node is not None and node.id not in seen:
            seen.add(node.id)
            chain.append(node)
            node = node.parent
        return list(reversed(chain))

    def progress(self) -> tuple[int, int] | None:
        """(done_leaves, total_leaves) over the subtree's leaf tasks."""
        ids = [self.id, *self.descendant_ids()]
        tasks = Task.query.filter(Task.id.in_(ids)).all()
        leaves = [t for t in tasks if not t.children]
        if not leaves:
            return None
        return sum(1 for t in leaves if t.done), len(leaves)

    def next_sibling_position(self) -> int:
        q = db.session.query(db.func.max(Task.position)).filter(
            Task.parent_id == self.parent_id,
        )
        if self.project_id is not None:
            q = q.filter(Task.project_id == self.project_id)
        else:
            q = q.filter(Task.project_id.is_(None))
        current = q.scalar()
        return (current or 0) + 1

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Task {self.id} {self.title!r}>"
