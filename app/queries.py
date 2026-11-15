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
    return query.order_by(Task.due_date, Task.title).all()


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
        .order_by(TaskDate.date, Task.title)
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
) -> list[Task]:
    """Attach child lists, breadcrumbs and progress to every task.

    All tasks are loaded once so breadcrumbs and roll-ups never need a
    per-card query. A caller that already ran ``load_all_tasks(user_id)`` can
    pass the result in to avoid loading the same rows twice. When loading
    here, ``user_id`` is mandatory so another user's tree cannot leak in.
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


def build_forest(
    all_tasks: list[Task],
    *,
    hide_done: bool = False,
    predicate=None,
    sort_key=None,
) -> list[Task]:
    """Turn prepared tasks into display roots, optionally filtered or sorted.

    ``predicate`` selects matching tasks; their ancestors and descendants are
    kept so the tree structure and context remain visible. A supplied
    ``sort_key`` orders roots and each task's siblings.
    """
    by_id = {t.id: t for t in all_tasks}
    keep_ids = set(by_id)

    if predicate is not None:
        matching = {t.id for t in all_tasks if predicate(t)}
        keep_ids = set(matching)
        for task_id in list(matching):
            node = by_id.get(task_id)
            while node is not None and node.parent_id in by_id:
                keep_ids.add(node.parent_id)
                node = by_id.get(node.parent_id)
            # A matching parent brings its subtree along.
            node = by_id.get(task_id)
            if node is not None:
                stack = list(node._children_cache)
                while stack:
                    child = stack.pop()
                    keep_ids.add(child.id)
                    stack.extend(child._children_cache)

    visible_children: dict[int, list[Task]] = {}
    for t in all_tasks:
        if t.id not in keep_ids:
            continue
        kids = [k for k in t._children_cache if k.id in keep_ids]
        visible_children[t.id] = kids

    def prune(task: Task) -> tuple[Task, list[Task]] | None:
        kids = [prune(k) for k in visible_children.get(task.id, [])]
        kids = [entry[0] for entry in kids if entry is not None]
        if sort_key is not None:
            kids.sort(key=sort_key)
        if hide_done and task.done and not kids:
            return None
        task._filtered_children = kids
        return (task, kids)

    roots = [t for t in all_tasks if t.id in keep_ids and (t.parent_id is None or t.parent_id not in keep_ids)]
    roots = [t for t in roots if prune(t) is not None]
    if sort_key is None:
        sort_key = lambda t: (t.done, t.due_date is None, t.due_date or dt.date.max, t.position, t.id)
    return sorted(roots, key=sort_key)
