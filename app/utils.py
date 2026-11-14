"""Small shared helpers: safe redirects, form errors, dates."""
import datetime as dt
from urllib.parse import urlsplit

from flask import flash, redirect


def safe_next(target: str | None, fallback: str):
    """Return ``target`` only when it is a local, relative URL.

    Anything with a scheme/host, a protocol-relative prefix (``//``), a
    backslash (browsers treat it as a slash), or control characters is
    rejected. This keeps `?next=` parameters from becoming open redirects.
    """
    if not target:
        return fallback
    if any(ch in target for ch in ("\r", "\n", "\\")):
        return fallback
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return fallback
    if not parts.path.startswith("/") or parts.path.startswith("//"):
        return fallback
    return target


def redirect_back(target: str | None, fallback: str):
    return redirect(safe_next(target, fallback))


def flash_form_errors(form) -> None:
    """Surface validation errors for forms submitted without a re-render."""
    for field, errors in form.errors.items():
        label = getattr(form, field).label.text if hasattr(form, field) else field
        for error in errors:
            flash(f"{label}: {error}", "error")


def shift_month(d: dt.date, months: int) -> dt.date:
    """Move a date by whole months, clamping the day to the month end."""
    import calendar

    month_index = d.month - 1 + months
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return dt.date(year, month, day)
