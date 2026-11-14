"""Query helpers shared by the calendar views, dashboard and project pages."""
import datetime as dt
from collections import defaultdict

from sqlalchemy import func

from .models import Task, TaskDate


def due_in(start: dt.date, end: dt.date) -> list[Task]:
    """Tasks with a due date inside [start, end]."""
    return (
        Task.query
        .filter(Task.due_date >= start, Task.due_date <= end)
        .order_by(Task.due_date, Task.title)
        .all()
    )


def work_rows(start: dt.date, end: dt.date) -> list[tuple[Task, dt.date]]:
    """(task, work_date) pairs for work dates inside [start, end].

    A task scheduled on several days appears once per day, which is what the
    calendar grouping needs.
    """
    return (
        Task.query
        .join(TaskDate, Task.id == TaskDate.task_id)
        .filter(TaskDate.date >= start, TaskDate.date <= end)
        .order_by(TaskDate.date, Task.title)
        .with_entities(Task, TaskDate.date)
        .all()
    )


def due_groups(start: dt.date, end: dt.date, key=None) -> dict:
    """Group due-in-period tasks by key(date); default the date itself."""
    key = key or (lambda d: d)
    groups: dict = defaultdict(list)
    for t in due_in(start, end):
        groups[key(t.due_date)].append(t)
    return groups


def work_groups(start: dt.date, end: dt.date, key=None) -> dict:
    key = key or (lambda d: d)
    groups: dict = defaultdict(list)
    for task, d in work_rows(start, end):
        groups[key(d)].append(task)
    return groups


def upcoming(days: int = 7, since: dt.date | None = None) -> list[tuple[dt.date, list]]:
    """(date, due_tasks) for the next `days` days that actually have tasks."""
    start = since or dt.date.today()
    end = start + dt.timedelta(days=days - 1)
    groups = due_groups(start, end)
    return sorted(groups.items())


def filter_task_tree(tasks, hide_done: bool) -> list:
    """Prune done tasks from a task tree.

    A done task is dropped unless it still has open sub-tasks, in which case
    it stays visible (struck through) as a container so its children don't
    disappear. The filtered child list is attached as `_filtered_children`
    for the task_tree template macro.
    """
    if not hide_done:
        return list(tasks)
    out = []
    for task in tasks:
        kids = filter_task_tree(task.children, True) if task.children else []
        if task.done and not kids:
            continue
        task._filtered_children = kids
        out.append(task)
    return out
