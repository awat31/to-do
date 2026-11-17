"""Application configuration objects selected via the FLASK_CONFIG env var."""
import datetime as dt
import os
from typing import ClassVar

from dotenv import find_dotenv, load_dotenv

# Config values below are class attributes evaluated at import time, so the
# .env file has to be loaded first (the app factory loads it too, which is
# harmless but too late for these defaults).
load_dotenv(find_dotenv(usecwd=True), override=False)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer.") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer.")
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off", ""}:
        return False
    raise RuntimeError(f"{name} must be a boolean value.")


class Config:
    """Shared defaults. Secrets are injected by the app factory from the
    instance directory so they survive across environments."""
    SECRET_KEY = os.environ.get("SECRET_KEY") or None
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS: ClassVar[dict] = {"pool_pre_ping": True}
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None
    MAX_CONTENT_LENGTH = 2 * 1024 * 1024  # notes are capped well below this
    MAX_WORK_RANGE_DAYS = 90
    # Login/session hardening
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = dt.timedelta(days=30)
    REMEMBER_COOKIE_DURATION = dt.timedelta(days=30)
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    LOGIN_MAX_ATTEMPTS = _env_int("TODO_LOGIN_MAX_ATTEMPTS", 5)
    LOGIN_LOCK_MINUTES = _env_int("TODO_LOGIN_LOCK_MINUTES", 15)
    # Per-IP throttles (per gunicorn worker): login attempts and signups.
    LOGIN_IP_LIMIT = _env_int("TODO_LOGIN_IP_LIMIT", 20)
    LOGIN_IP_WINDOW_MINUTES = _env_int("TODO_LOGIN_IP_WINDOW_MINUTES", 15)
    SIGNUP_IP_LIMIT = _env_int("TODO_SIGNUP_IP_LIMIT", 5)
    SIGNUP_IP_WINDOW_HOURS = _env_int("TODO_SIGNUP_IP_WINDOW_HOURS", 1)
    # Notes limits (characters) persisted by the autosave endpoints.
    TASK_NOTES_LIMIT = _env_int("TODO_TASK_NOTES_LIMIT", 100_000)
    PROJECT_NOTES_LIMIT = _env_int("TODO_PROJECT_NOTES_LIMIT", 200_000)
    # Local timezone used for "today" and date pickers. UTC is unambiguous
    # and matches the server clock unless the user sets this explicitly.
    TIMEZONE = os.environ.get("TODO_TIMEZONE", "UTC")
    # Optional comma-separated host allow-list (Flask 3.1 TRUSTED_HOSTS).
    TRUSTED_HOSTS = [
        h.strip() for h in os.environ.get("TODO_TRUSTED_HOSTS", "").split(",") if h.strip()
    ] or None


class ProductionConfig(Config):
    DEBUG = False
    TESTING = False
    # Only enable when the app is served behind HTTPS (e.g. a TLS reverse
    # proxy), otherwise browsers silently drop the session cookie over http://.
    SESSION_COOKIE_SECURE = _env_bool("TODO_SECURE_COOKIES")
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE


class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False
    REMEMBER_COOKIE_SECURE = False


class TestingConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    LOGIN_MAX_ATTEMPTS = 99  # tests intentionally exercise failed logins
    # The in-process per-IP limiter is exercised by its own dedicated tests
    # with a small limit; the shared test suite logs in many times from one
    # client, so the default throttle is disabled here.
    LOGIN_IP_LIMIT = 1_000_000
    SIGNUP_IP_LIMIT = 1_000_000


_CONFIGS = {
    "production": ProductionConfig,
    "development": DevelopmentConfig,
    "testing": TestingConfig,
}


def get_config_class():
    name = os.environ.get("FLASK_CONFIG", "production")
    try:
        return _CONFIGS[name]
    except KeyError as exc:
        raise RuntimeError(
            f"FLASK_CONFIG must be one of: {', '.join(_CONFIGS)}."
        ) from exc
