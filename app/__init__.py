"""Application factory and shared extensions."""
import os
import secrets
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from flask import Flask, render_template, send_from_directory
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_wtf import CSRFProtect
from sqlalchemy.exc import IntegrityError

from . import periods
from .config import get_config_class
from .models import DeletedBatch, User, db
from .palette import PROJECT_COLORS

csrf = CSRFProtect()
migrate = Migrate()
login_manager = LoginManager()
login_manager.login_view = "auth.login"



def _asset_version(app: Flask) -> str:
    """A cheap cache-buster based on the newest static-file mtime."""
    root = Path(app.static_folder or "")
    try:
        newest = max(
            (p.stat().st_mtime for p in root.rglob("*") if p.is_file()),
            default=0,
        )
    except OSError:  # pragma: no cover
        newest = 0
    return str(int(newest))


def _instance_secret_key(app: Flask) -> str:
    """SECRET_KEY from env, else persisted in the instance dir."""
    if app.config.get("SECRET_KEY"):
        return app.config["SECRET_KEY"]
    path = Path(app.instance_path) / "secret_key"
    if path.is_file():
        app.config["SECRET_KEY"] = path.read_text().strip()
    else:
        app.config["SECRET_KEY"] = secrets.token_urlsafe(48)
        path.write_text(app.config["SECRET_KEY"])
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover
            pass
    return app.config["SECRET_KEY"]


def _ensure_admin(app: Flask) -> None:
    """Seed the initial admin account when the database has no users.

    Multi-user installs create further accounts through the sign-up page.
    Existing passwords are never overwritten on restart: an admin who changes
    their password in the User page must keep it.

    Without TODO_ADMIN_PASSWORD, a random first-run password is generated
    and written to instance/admin_password.txt (chmod 600).
    """
    with app.app_context():
        if User.query.first() is not None:
            return

        password = os.environ.get("TODO_ADMIN_PASSWORD")
        generated = False
        if not password:
            password = secrets.token_urlsafe(14)
            generated = True
        user = User(username="admin")
        user.set_password(password)
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            # Several gunicorn workers can reach first-run together.
            db.session.rollback()
            app.logger.info("Admin user was created by another worker")
            return
        if generated:
            path = Path(app.instance_path) / "admin_password.txt"
            path.write_text(
                "Username: admin\n"
                f"Password: {password}\n"
                "Delete this file after noting the password.\n"
            )
            try:
                os.chmod(path, 0o600)
            except OSError:  # pragma: no cover
                pass
            app.logger.warning(
                "Created admin user; password written to %s", path,
            )
        else:
            app.logger.info("Created admin user from TODO_ADMIN_PASSWORD")


def create_app(config_class=None) -> Flask:
    app = Flask(__name__)
    # .env works for both `flask` CLI and gunicorn workers (CWD-based lookup:
    # frame-based discovery breaks under gunicorn's import machinery).
    dotenv_path = find_dotenv(usecwd=True)
    if dotenv_path:
        load_dotenv(dotenv_path, override=False)
    app.config.from_object(config_class or get_config_class())

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    _instance_secret_key(app)
    app.config["ASSET_VERSION"] = _asset_version(app)

    app.config.setdefault(
        "SQLALCHEMY_DATABASE_URI",
        os.environ.get("TODO_DATABASE", f"sqlite:///{Path(app.instance_path) / 'todo.sqlite3'}"),
    )

    db.init_app(app)
    migrate.init_app(app, db, directory=Path(__file__).resolve().parent.parent / "migrations")
    csrf.init_app(app)
    login_manager.init_app(app)

    from . import auth, calendarview, main, projects, tasks
    app.register_blueprint(auth.bp)
    app.register_blueprint(main.bp)
    app.register_blueprint(tasks.bp)
    app.register_blueprint(projects.bp)
    app.register_blueprint(calendarview.bp)

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return db.session.get(User, int(user_id))
        except (TypeError, ValueError):
            return None

    # --- template helpers --------------------------------------------------
    @app.template_filter("human_date")
    def human_date(value):
        if value is None:
            return ""
        return value.strftime("%a %-d %b %Y")

    @app.template_filter("human_date_short")
    def human_date_short(value):
        if value is None:
            return ""
        return value.strftime("%-d %b")

    @app.template_filter("month_name")
    def month_name(value):
        return periods.MONTH_NAMES[int(value) - 1]

    @app.before_request
    def reset_derived_caches():
        """Derived task caches are request-scoped, even under test clients."""
        db.session.info["todo_task_version"] = (
            db.session.info.get("todo_task_version", 0) + 1
        )

    @app.context_processor
    def inject_globals():
        from flask import session

        batch_id = session.get("last_deleted_batch")
        last_batch = db.session.get(DeletedBatch, batch_id) if batch_id else None
        if batch_id and last_batch is None:
            session.pop("last_deleted_batch", None)
        return {
            "last_deleted_batch": last_batch,
            "PROJECT_COLORS": PROJECT_COLORS,
            "color_hex": lambda name: PROJECT_COLORS.get(name, PROJECT_COLORS["blue"]),
            "today": periods.today(),
            "kinds": periods.KINDS,
            "months": periods.MONTH_NAMES,
            "hide_completed": session.get("hide_completed", False),
            "asset_version": _asset_version(app) if app.debug else app.config["ASSET_VERSION"],
        }

    @app.after_request
    def security_headers(response):
        """Conservative headers that do not require template rewrites."""
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), camera=(), microphone=()"
        )
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "connect-src 'self'; worker-src 'self'; manifest-src 'self'; "
            "form-action 'self'; base-uri 'self'; "
            "frame-ancestors 'none'; object-src 'none'",
        )
        if response.mimetype == "text/html":
            response.headers["Cache-Control"] = "no-store"
        if app.config.get("SESSION_COOKIE_SECURE"):
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response

    @app.errorhandler(400)
    def bad_request(error):
        return render_template(
            "errors/error.html", code=400, message="That request was not valid."
        ), 400

    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/error.html", code=404, message="Page not found."), 404

    @app.errorhandler(413)
    def too_large(error):
        return render_template(
            "errors/error.html", code=413, message="That text is too large to save."
        ), 413

    @app.errorhandler(500)
    def server_error(error):
        return render_template("errors/error.html", code=500, message="Something went wrong."), 500

    @app.route("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.route("/sw.js")
    def service_worker():
        """Serve the worker from the root so it can control the whole app."""
        response = send_from_directory(
            app.static_folder, "sw.js", mimetype="application/javascript"
        )
        response.headers["Service-Worker-Allowed"] = "/"
        response.headers["Cache-Control"] = "no-cache"
        return response

    with app.app_context():
        from sqlalchemy import inspect
        if not inspect(db.engine).has_table("user"):
            app.logger.warning(
                "Database is not initialised yet; run `flask --app wsgi db upgrade` "
                "(the systemd service does this automatically)."
            )
        else:
            # During `flask db upgrade` the app is imported before the schema
            # matches the models; tolerate that and sync on the next start.
            from sqlalchemy.exc import OperationalError

            try:
                _ensure_admin(app)
            except OperationalError:
                app.logger.warning(
                    "User table is out of date; run `flask --app wsgi db upgrade`."
                )

    return app
