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
from flask_login import current_user, login_required, login_user, logout_user, user_logged_in
from sqlalchemy.exc import IntegrityError

from .forms import (
    AppearanceForm,
    ChangePasswordForm,
    DeleteAccountForm,
    DisplayNameForm,
    LoginForm,
    SignupForm,
)
from .models import User, db, utcnow
from .ratelimit import SlidingWindowLimiter
from .utils import safe_next

bp = Blueprint("auth", __name__)

# Per-worker, per-IP throttles. Sizing: login allows a burst well above the
# per-account lockout threshold (5) so a family/office sharing one NAT
# address is not locked out, while still capping bulk credential stuffing;
# signup is deliberately tight because account creation is rare and abuse
# (spam accounts) is costly to undo.
_login_limiter = SlidingWindowLimiter(limit=20, window_seconds=15 * 60)
_signup_limiter = SlidingWindowLimiter(limit=5, window_seconds=60 * 60)


@user_logged_in.connect
def _reset_login_limit(sender, user, **kwargs):
    """A successful login clears the IP's failure history."""
    _login_limiter.reset(f"login:{request.remote_addr}")


def _client_ip() -> str:
    # Gunicorn's forwarded_allow_ips already restricts which proxies may set
    # X-Forwarded-For, so remote_addr is the trusted client address.
    return request.remote_addr or "unknown"


def _login_allowed() -> bool:
    return _login_limiter.check(
        f"login:{_client_ip()}",
        limit=current_app.config["LOGIN_IP_LIMIT"],
        window=current_app.config["LOGIN_IP_WINDOW_MINUTES"] * 60,
    )


def _signup_allowed() -> bool:
    return _signup_limiter.check(
        f"signup:{_client_ip()}",
        limit=current_app.config["SIGNUP_IP_LIMIT"],
        window=current_app.config["SIGNUP_IP_WINDOW_HOURS"] * 3600,
    )


def _session_is_current(user: User) -> bool:
    """Reject sessions minted before the user's last password change."""
    stored = session.get("_session_version")
    if stored is None:
        return True  # session predates the versioning scheme; treat as current
    return stored == (user.session_version or 0)


def _stamp_session(user: User) -> None:
    session["_session_version"] = user.session_version or 0


def _find_user(username: str) -> User | None:
    """Look up a username using the same case-folded key enforced by the DB."""
    return User.query.filter_by(username_key=username.casefold()).first()


@bp.route("/signup", methods=("GET", "POST"))
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = SignupForm()
    if form.validate_on_submit():
        if not _signup_allowed():
            return render_template("auth/signup.html", form=form), 429
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
                _stamp_session(user)
                flash(f"Welcome, {user.username}.", "ok")
                return redirect(url_for("main.dashboard"))
    return render_template("auth/signup.html", form=form)


@bp.route("/login", methods=("GET", "POST"))
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form = LoginForm()
    if form.validate_on_submit():
        if not _login_allowed():
            return render_template("auth/login.html", form=form), 429
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
            _stamp_session(user)
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
    display_form = DisplayNameForm(obj=current_user)
    appearance_form = AppearanceForm(obj=current_user)
    delete_form = DeleteAccountForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            form.current_password.errors.append("Current password is incorrect.")
        else:
            user = current_user._get_current_object()
            user.set_password(form.new_password.data)
            user.clear_failures()
            # Invalidate every other session (stolen cookies die with the
            # old password); this browser is re-stamped below.
            user.bump_session_version()
            db.session.commit()
            session.clear()
            login_user(user)
            _stamp_session(user)
            flash("Password changed.", "ok")
            return redirect(url_for("auth.account"))
    return render_template(
        "auth/account.html",
        form=form,
        display_form=display_form,
        appearance_form=appearance_form,
        delete_form=delete_form,
    )


@bp.route("/account/appearance", methods=("POST",))
@login_required
def update_appearance():
    form = AppearanceForm()
    if form.validate_on_submit():
        current_user.highlight_color = form.highlight_color.data.lower()
        current_user.night_mode = form.night_mode.data
        db.session.commit()
        flash("Appearance settings updated.", "ok")
        return redirect(url_for("auth.account"))

    return render_template(
        "auth/account.html",
        form=ChangePasswordForm(),
        display_form=DisplayNameForm(obj=current_user),
        appearance_form=form,
        delete_form=DeleteAccountForm(),
    ), 400


@bp.route("/account/display-name", methods=("POST",))
@login_required
def update_display_name():
    form = DisplayNameForm()
    if form.validate_on_submit():
        current_user.display_name = form.display_name.data
        db.session.commit()
        flash("Display name updated.", "ok")
        return redirect(url_for("auth.account"))

    return render_template(
        "auth/account.html",
        form=ChangePasswordForm(),
        display_form=form,
        appearance_form=AppearanceForm(obj=current_user),
        delete_form=DeleteAccountForm(),
    ), 400


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
    if user.is_protected_admin:
        flash("The built-in admin account cannot be deleted.", "error")
        return redirect(url_for("auth.admin_users"))

    is_self = user.id == current_user.id
    username = user.username
    _delete_user(user.id)
    if is_self:
        session.clear()
        logout_user()
        flash(f"Account {username!r} and all of its data were deleted.", "ok")
        return redirect(url_for("auth.login"))

    flash(f"Deleted {username!r} and all of their data.", "ok")
    return redirect(url_for("auth.admin_users"))


@bp.route("/admin/users/<int:user_id>/admin", methods=("POST",))
@login_required
def set_admin(user_id: int):
    _require_admin()
    value = request.form.get("is_admin")
    if value not in ("0", "1"):
        abort(400)

    user = db.session.get(User, user_id)
    if user is None:
        abort(404)

    make_admin = value == "1"
    if user.is_admin == make_admin:
        label = "an administrator" if make_admin else "a regular user"
        flash(f"{user.username!r} is already {label}.", "error")
        return redirect(url_for("auth.admin_users"))

    user.is_admin = make_admin
    db.session.commit()
    if user.id == current_user.id:
        flash("Your administrator privileges were revoked.", "ok")
        return redirect(url_for("auth.account"))

    flash(
        f"Granted administrator privileges to {user.username!r}."
        if make_admin
        else f"Revoked administrator privileges from {user.username!r}.",
        "ok",
    )
    return redirect(url_for("auth.admin_users"))


@bp.route("/account/delete", methods=("POST",))
@login_required
def delete_account():
    if current_user.is_protected_admin:
        flash("The built-in admin account cannot be deleted.", "error")
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
        display_form=DisplayNameForm(obj=current_user),
        appearance_form=AppearanceForm(obj=current_user),
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
