"""Single-user authentication (admin account)."""
import math

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from .forms import LoginForm
from .models import User, db, utcnow
from .utils import safe_next

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=("GET", "POST"))
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data.strip()).first()
        if user is not None and user.is_locked():
            remaining = (user.locked_until - utcnow()).total_seconds()
            minutes = max(1, math.ceil(remaining / 60))
            flash(
                f"Too many failed attempts. Try again in about {minutes} minute"
                f"{'' if minutes == 1 else 's'}.",
                "error",
            )
            return render_template("auth/login.html", form=form), 429

        if user is not None and user.check_password(form.password.data):
            user.clear_failures()
            db.session.commit()
            login_user(user, remember=bool(form.remember.data))
            session.permanent = bool(form.remember.data)
            nxt = safe_next(request.args.get("next"), url_for("main.dashboard"))
            return redirect(nxt)

        if user is not None:
            user.register_failure(
                current_app.config["LOGIN_MAX_ATTEMPTS"],
                current_app.config["LOGIN_LOCK_MINUTES"],
            )
            db.session.commit()
            if user.is_locked():
                flash(
                    "Too many failed attempts. This account is temporarily locked.",
                    "error",
                )
                return render_template("auth/login.html", form=form), 429
        current_app.logger.warning(
            "Failed login attempt for %r from %s", form.username.data, request.remote_addr
        )
        flash("Invalid username or password.", "error")
    return render_template("auth/login.html", form=form)


@bp.route("/logout", methods=("POST",))
@login_required
def logout():
    # Clear app state first. Flask-Login queues deletion of the remember-me
    # cookie via a marker in the session, so clearing afterwards would drop
    # that marker and the next request would silently log the user back in.
    session.clear()
    logout_user()
    return redirect(url_for("auth.login"))
