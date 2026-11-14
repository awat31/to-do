"""Application factory and shared extensions."""
import logging
import os
import secrets
from pathlib import Path

from flask import Flask, render_template
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_wtf import CSRFProtect

from . import periods
from .config import get_config_class
from .models import User, db
from .palette import PROJECT_COLORS

csrf = CSRFProtect()
migrate = Migrate()
login_manager = LoginManager()
login_manager.login_view = "auth.login"



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
    """Create the single admin account on first run.

    Password comes from TODO_ADMIN_PASSWORD, otherwise a random one is
    generated and written to instance/admin_password.txt (chmod 600).
    """
    with app.app_context():
        if User.query.count() > 0:
            return
        import getpass
        password = os.environ.get("TODO_ADMIN_PASSWORD")
        generated = False
        if not password:
            password = secrets.token_urlsafe(14)
            generated = True
        user = User(username="admin")
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
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
    app.config.from_object(config_class or get_config_class())

    Path(app.instance_path).mkdir(parents=True, exist_ok=True)
    _instance_secret_key(app)

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
        return db.session.get(User, int(user_id))

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

    @app.context_processor
    def inject_globals():
        return {
            "PROJECT_COLORS": PROJECT_COLORS,
            "color_hex": lambda name: PROJECT_COLORS.get(name, PROJECT_COLORS["blue"]),
            "today": periods.today(),
            "kinds": periods.KINDS,
        }

    @app.errorhandler(404)
    def not_found(error):
        return render_template("errors/error.html", code=404, message="Page not found."), 404

    @app.errorhandler(500)
    def server_error(error):
        return render_template("errors/error.html", code=500, message="Something went wrong."), 500

    @app.route("/healthz")
    def healthz():
        return {"status": "ok"}

    with app.app_context():
        from sqlalchemy import inspect
        if not inspect(db.engine).has_table("user"):
            app.logger.warning(
                "Database is not initialised yet; run `flask --app wsgi db upgrade` "
                "(the systemd service does this automatically)."
            )
        else:
            _ensure_admin(app)

    return app
