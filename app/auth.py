"""Account registration, authentication, and password management."""
import math

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy.exc import IntegrityError

from .forms import ChangePasswordForm, DeleteAccountForm, LoginForm, SignupForm
from .models import User, db, utcnow
from .utils import safe_next

bp = Blueprint("auth", __name__)


def _find_user(username: str) -> User | None:
    """Look up a username using the same case-folded key enforced by the DB."""
    return User.query.filter_by(username_key=username.casefold()).first()


@bp.route("/signup", methods=("GET", "POST"))
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = SignupForm()
    if form.validate_on_submit():
        username = form.username.data
        if _find_user(username) is not None:
            form.username.errors.append("That username is already taken.")
        else:
            user = User(username=username)
            user.set_password(form.password.data)
            db.session.add(user)
            try:
                db.session.commit()
            except IntegrityError:
                # A simultaneous signup won the unique constraint.
                db.session.rollback()
                form.username.errors.append("That username is already taken.")
            else:
                login_user(user)
                flash(f"Welcome, {user.username}.", "ok")
                return redirect(url_for("main.dashboard"))
    return render_template("auth/signup.html", form=form)


@bp.route("/login", methods=("GET", "POST"))
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form = LoginForm()
    if form.validate_on_submit():
        user = _find_user(form.username.data)
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


@bp.route("/account", methods=("GET", "POST"))
@login_required
def account():
    form = ChangePasswordForm()
    delete_form = DeleteAccountForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            form.current_password.errors.append("Current password is incorrect.")
        else:
            current_user.set_password(form.new_password.data)
            current_user.clear_failures()
            db.session.commit()
            flash("Password changed.", "ok")
            return redirect(url_for("auth.account"))
    return render_template("auth/account.html", form=form, delete_form=delete_form)


def _require_admin() -> None:
    if not current_user.is_admin:
        abort(403)


def _delete_user(user_id: int) -> None:
    db.session.execute(User.__table__.delete().where(User.id == user_id))
    db.session.commit()


@bp.route("/admin/users")
@login_required
def admin_users():
    _require_admin()
    users = User.query.order_by(User.username_key).all()
    return render_template("auth/users.html", users=users)


@bp.route("/admin/users/<int:user_id>/delete", methods=("POST",))
@login_required
def delete_user(user_id: int):
    _require_admin()
    user = db.session.get(User, user_id)
    if user is None:
        abort(404)
    if user.id == current_user.id:
        flash("Use your account page to delete your own account.", "error")
        return redirect(url_for("auth.admin_users"))

    username = user.username
    _delete_user(user.id)
    flash(f"Deleted {username!r} and all of their data.", "ok")
    return redirect(url_for("auth.admin_users"))


@bp.route("/account/delete", methods=("POST",))
@login_required
def delete_account():
    if current_user.is_admin:
        flash("Administrator accounts cannot be deleted.", "error")
        return redirect(url_for("auth.account"))

    form = DeleteAccountForm()
    if form.validate_on_submit():
        if current_user.check_password(form.current_password.data):
            username = current_user.username
            _delete_user(current_user.id)
            session.clear()
            logout_user()
            flash(f"Account {username!r} and all of its data were deleted.", "ok")
            return redirect(url_for("auth.login"))
        form.current_password.errors.append("Current password is incorrect.")

    return render_template(
        "auth/account.html",
        form=ChangePasswordForm(),
        delete_form=form,
    ), 400


@bp.route("/logout", methods=("POST",))
@login_required
def logout():
    # Clear app state first. Flask-Login queues deletion of the remember-me
    # cookie via a marker in the session, so clearing afterwards would drop
    # that marker and the next request would silently log the user back in.
    session.clear()
    logout_user()
    return redirect(url_for("auth.login"))
