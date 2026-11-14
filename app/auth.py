"""Single-user authentication (admin account)."""
from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from .models import User
from .forms import LoginForm

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=("GET", "POST"))
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data.strip()).first()
        if user is not None and user.check_password(form.password.data):
            login_user(user, remember=True)
            nxt = request.args.get("next")
            if nxt and not nxt.startswith("/") and not nxt.startswith(request.host_url):
                nxt = None
            return redirect(nxt or url_for("main.dashboard"))
        current_app.logger.warning("Failed login attempt for %r", form.username.data)
        flash("Invalid username or password.", "error")
    return render_template("auth/login.html", form=form)


@bp.route("/logout", methods=("POST",))
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
