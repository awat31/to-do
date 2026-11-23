"""Query helpers shared by the calendar views, dashboard and project pages.

Task trees can be deep. Rendering every card used to trigger dozens of lazy
loads per task; the helpers at the bottom of this module load the
relationships in a couple of queries and cache breadcrumbs, sub-task progress
and child lists directly on the ORM instances.

Every query requires a ``user_id`` so one user's tasks can never leak into
another user's dashboard, calendar, project list or search results.
"""
import datetime as dt
from collections import defaultdict

from sqlalchemy.orm import selectinload

from .models import Task, TaskDate, _task_version


def due_in(
    start: dt.date,
    end: dt.date,
    include_done: bool = True,
    *,
    user_id: int,
) -> list[Task]:
    """A user's tasks with a due date inside [start, end]."""
    query = Task.query.filter(
        Task.user_id == user_id,
        Task.due_date >= start,
        Task.due_date <= end,
    )
    if not include_done:
        query = query.filter(Task.done.is_(False))
    # Task.id tiebreak keeps the ordering deterministic when two tasks share
    # a due date and title.
    return query.order_by(Task.due_date, Task.title, Task.id).all()


def work_rows(
    start: dt.date,
    end: dt.date,
    include_done: bool = True,
    *,
    user_id: int,
) -> list[tuple[Task, dt.date]]:
    """(task, work_date) pairs for a user's dates inside [start, end].

    A task scheduled on several days appears once per day, which is what the
    calendar grouping needs.
    """
    query = (
        Task.query
        .join(TaskDate, Task.id == TaskDate.task_id)
        .filter(
            Task.user_id == user_id,
            TaskDate.date >= start,
            TaskDate.date <= end,
        )
    )
    if not include_done:
        query = query.filter(Task.done.is_(False))
    return (
        query
        .order_by(TaskDate.date, Task.title, Task.id)
        .with_entities(Task, TaskDate.date)
        .all()
    )


def due_groups(start: dt.date, end: dt.date, key=None, *, user_id: int) -> dict:
    """Group a user's due-in-period tasks by key(date); default the date."""
    key = key or (lambda d: d)
    groups: dict = defaultdict(list)
    for t in due_in(start, end, user_id=user_id):
        groups[key(t.due_date)].append(t)
    return groups


def work_groups(start: dt.date, end: dt.date, key=None, *, user_id: int) -> dict:
    key = key or (lambda d: d)
    groups: dict = defaultdict(list)
    for task, d in work_rows(start, end, user_id=user_id):
        groups[key(d)].append(task)
    return groups


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
        children = _children_of(task)
        kids = filter_task_tree(children, True) if children else []
        if task.done and not kids:
            continue
        task._filtered_children = kids
        out.append(task)
    return out


def _children_of(task: Task) -> list[Task]:
    if "_children_cache" in task.__dict__:
        return task._children_cache
    return list(task.children)


def apply_task_order(task_ids, tasks: list[Task], visible: list[Task]) -> str | None:
    """Reorder visible siblings while keeping hidden siblings in their slots."""
    if not isinstance(task_ids, list) or not all(
        isinstance(task_id, int) and not isinstance(task_id, bool)
        for task_id in task_ids
    ):
        return "Invalid task order."
    by_id = {task.id: task for task in visible}
    if len(task_ids) != len(set(task_ids)) or set(task_ids) != set(by_id):
        return "Task list changed. Reload and try again."
    reordered = iter(by_id[task_id] for task_id in task_ids)
    for position, task in enumerate(tasks):
        if task.id in by_id:
            task = next(reordered)
        task.position = position
    return None


# ---------------------------------------------------------------------------
# Batch preparation (query-count friendly)
# ---------------------------------------------------------------------------

def load_all_tasks(user_id: int) -> list[Task]:
    """Load one user's tasks plus projects and work dates in three queries."""
    return (
        Task.query
        .filter(Task.user_id == user_id)
        .options(selectinload(Task.project), selectinload(Task.work_dates))
        .all()
    )


def prepare_tasks(
    tasks: list[Task] | None = None,
    *,
    user_id: int | None = None,
    done_last: bool = False,
) -> list[Task]:
    """Attach child lists, breadcrumbs and progress to every task.

    All tasks are loaded once so breadcrumbs and roll-ups never need a
    per-card query. A caller that already ran ``load_all_tasks(user_id)`` can
    pass the result in to avoid loading the same rows twice. When loading
    here, ``user_id`` is mandatory so another user's tree cannot leak in.
    With ``done_last`` the children of each task are ordered with open
    tasks first and completed ones at the bottom.
    """
    if tasks is None:
        if user_id is None:
            raise ValueError("user_id is required when loading tasks")
        all_tasks = load_all_tasks(user_id)
    else:
        all_tasks = tasks
    by_id = {t.id: t for t in all_tasks}
    children: dict[int, list[Task]] = defaultdict(list)
    for t in all_tasks:
        if t.parent_id is not None:
            children[t.parent_id].append(t)
    if done_last:
        for kids in children.values():
            kids.sort(key=lambda t: (t.done, t.position, t.id))
    else:
        for kids in children.values():
            kids.sort(key=lambda t: (t.position, t.id))

    for t in all_tasks:
        t._children_cache = children.get(t.id, [])
        chain, node, seen = [], t.parent, set()
        while node is not None and node.id not in seen:
            seen.add(node.id)
            chain.append(node)
            node = by_id.get(node.parent_id) if node.parent_id else None
        t._ancestors_cache = list(reversed(chain))

    counts: dict[int, tuple[int, int]] = {}
    visiting: set[int] = set()

    def rollup(task: Task) -> tuple[int, int]:
        if task.id in counts:
            return counts[task.id]
        if task.id in visiting:  # defensive: never loop on corrupt data
            return (0, 0)
        visiting.add(task.id)
        kids = task._children_cache
        if not kids:
            result = (1 if task.done else 0, 1)
        else:
            done = total = 0
            for kid in kids:
                kd, kt = rollup(kid)
                done += kd
                total += kt
            result = (done, total)
        visiting.discard(task.id)
        counts[task.id] = result
        return result

    version = _task_version()
    for t in all_tasks:
        rollup(t)
        t._progress_cache = None if not t._children_cache else counts[t.id]
        t._progress_version = version

    return all_tasks
