"""Period parsing/formatting for the day / week / month / year calendar views.

Every period is normalised to a (start_date, end_date) inclusive pair, a
human label, and the parameter values for the previous/next period of the
same kind, so templates only deal with simple values.
"""
import calendar as _calendar
import datetime as dt

KINDS = ("day", "week", "month", "year")

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June", "July",
    "August", "September", "October", "November", "December",
]


class BadPeriod(ValueError):
    pass


def today() -> dt.date:
    return dt.date.today()


def parse_day(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def parse_week(value: str) -> dt.date:
    """'2026-W11' -> the Monday starting that ISO week."""
    year, week = value.split("-W")
    return dt.date.fromisocalendar(int(year), int(week), 1)


def parse_month(value: str) -> dt.date:
    year, month = (int(p) for p in value.split("-"))
    return dt.date(year, month, 1)


def parse_year(value: str) -> dt.date:
    return dt.date(int(value), 1, 1)


def week_value(d: dt.date) -> str:
    iso = d.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def month_value(d: dt.date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def period(kind: str, value: str | None = None) -> dict:
    """Resolve a period selector into everything the view needs.

    Returns a dict with: kind, value, start, end, label, granularity
    ('day' | 'month' | None), prev, next, today_value.
    """
    t = today()
    if value is None:
        value = {
            "day": t.isoformat(),
            "week": week_value(t),
            "month": month_value(t),
            "year": f"{t.year:04d}",
        }[kind]

    try:
        if kind == "day":
            start = parse_day(value)
            end = start
            label = start.strftime("%A, %-d %B %Y")
            prev, nxt = start - dt.timedelta(days=1), start + dt.timedelta(days=1)
            granularity = None
            groups = None
        elif kind == "week":
            start = parse_week(value)
            end = start + dt.timedelta(days=6)
            iso = start.isocalendar()
            label = f"Week {iso.week}, {iso.year} ({start.strftime('%-d %b')} – {end.strftime('%-d %b')})"
            prev, nxt = start - dt.timedelta(days=7), start + dt.timedelta(days=7)
            granularity = "day"
        elif kind == "month":
            start = parse_month(value)
            end = start.replace(day=_calendar.monthrange(start.year, start.month)[1])
            label = f"{MONTH_NAMES[start.month - 1]} {start.year}"
            prev, nxt = prev_month(start), next_month(start)
            granularity = "day"
        elif kind == "year":
            start = dt.date(int(value), 1, 1)
            end = dt.date(int(value), 12, 31)
            label = str(start.year)
            prev, nxt = dt.date(start.year - 1, 1, 1), dt.date(start.year + 1, 1, 1)
            granularity = "month"
        else:
            raise BadPeriod(kind)
    except (ValueError, IndexError) as exc:
        raise BadPeriod(f"invalid period {kind}={value!r}") from exc

    out = {
        "kind": kind,
        "value": value,
        "start": start,
        "end": end,
        "label": label,
        "granularity": granularity,
        "prev": {"kind": kind, "value": period_value(kind, prev)},
        "next": {"kind": kind, "value": period_value(kind, nxt)},
        "today": {"kind": kind, "value": period_value(kind, t)},
    }
    return out


def period_value(kind: str, d: dt.date) -> str:
    if kind == "day":
        return d.isoformat()
    if kind == "week":
        return week_value(d)
    if kind == "month":
        return month_value(d)
    return f"{d.year:04d}"


def prev_month(first: dt.date) -> dt.date:
    return dt.date(first.year - 1, 12, 1) if first.month == 1 else dt.date(first.year, first.month - 1, 1)


def next_month(first: dt.date) -> dt.date:
    return dt.date(first.year + 1, 1, 1) if first.month == 12 else dt.date(first.year, first.month + 1, 1)


def group_key(kind: str, d: dt.date) -> tuple:
    """Key a date into the sub-groups shown inside a period view."""
    if kind == "year":
        return (d.month,)  # months January..December
    return (d,)  # individual days


def group_label(kind: str, key: tuple) -> str:
    if kind == "year":
        return MONTH_NAMES[key[0] - 1]
    d = key[0]
    return d.strftime("%A, %-d %B")
