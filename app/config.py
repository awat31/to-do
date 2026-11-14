"""Application configuration objects selected via the FLASK_CONFIG env var."""
import os


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "")
    try:
        return int(raw)
    except ValueError:
        return default


class Config:
    """Shared defaults. Secrets are injected by the app factory from the
    instance directory so they survive across environments."""
    SECRET_KEY = os.environ.get("SECRET_KEY")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None
    MAX_CONTENT_LENGTH = 1 * 1024 * 1024  # 1 MiB is plenty for this app
    # Widest allowed "work on" range in days, to guard against typos.
    MAX_WORK_RANGE_DAYS = 90
    # Login/session hardening
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME_DAYS = 30


class ProductionConfig(Config):
    DEBUG = False
    TESTING = False
    # Only set when the app is served behind HTTPS (e.g. a TLS reverse proxy),
    # otherwise browsers silently drop the session cookie over plain http://.
    SESSION_COOKIE_SECURE = os.environ.get("TODO_SECURE_COOKIES", "") == "1"


class DevelopmentConfig(Config):
    DEBUG = True
    SESSION_COOKIE_SECURE = False


class TestingConfig(Config):
    TESTING = True
    WTF_CSRF_ENABLED = False
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"


_CONFIGS = {
    "production": ProductionConfig,
    "development": DevelopmentConfig,
    "testing": TestingConfig,
}


def get_config_class():
    return _CONFIGS.get(os.environ.get("FLASK_CONFIG", "production"), ProductionConfig)
