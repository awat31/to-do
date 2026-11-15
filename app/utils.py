"""Small shared helpers: safe redirects, form errors, CSRF, dates."""
import datetime as dt
from urllib.parse import urlsplit

from flask import flash, redirect, request
from flask_wtf.csrf import validate_csrf
from werkzeug.exceptions import BadRequest
from wtforms.validators import ValidationError


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


def csrf_valid(token: str | None) -> bool:
    """Validate a token without turning a bad token into an exception.

    ``validate_csrf`` raises a WTForms ``ValidationError`` (a ``ValueError``),
    not ``BadRequest``; catching the wrong type used to hide the failure.
    """
    try:
        validate_csrf(token or "")
    except (ValidationError, BadRequest):
        return False
    return True


def notes_payload() -> tuple[dict, str]:
    """Read a notes save that may be JSON (fetch) or form data (sendBeacon).

    Returns ``(data, csrf_token)``. The form path matters for the page-hide
    beacon: browsers cannot attach the X-CSRFToken header to sendBeacon, so
    the token travels in a FormData field instead.
    """
    body = request.get_json(silent=True)
    data = body if isinstance(body, dict) else request.form
    token = request.headers.get("X-CSRFToken") or data.get("csrf_token", "")
    return data, token


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
