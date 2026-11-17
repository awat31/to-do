"""WTForms used across the app."""
from flask import current_app
from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    PasswordField,
    SelectField,
    StringField,
    TextAreaField,
)
from wtforms.fields.choices import SelectChoice
from wtforms.validators import DataRequired, EqualTo, Length, Optional, Regexp


def _strip(value):
    return value.strip() if isinstance(value, str) else value

RECURRENCE_CHOICES = (
    SelectChoice("none", "Does not repeat"),
    SelectChoice("daily", "Every day"),
    SelectChoice("weekly", "Every week"),
    SelectChoice("monthly", "Every month"),
)


class LoginForm(FlaskForm):
    username = StringField(
        "Username", filters=(_strip,),
        validators=(DataRequired(), Length(max=80)),
    )
    password = PasswordField("Password", validators=(DataRequired(),))
    remember = BooleanField("Keep me signed in on this device")


class SignupForm(FlaskForm):
    """Registration needs only a username and password."""

    username = StringField(
        "Username", filters=(_strip,),
        validators=(DataRequired(), Length(min=3, max=80)),
    )
    password = PasswordField(
        "Password", validators=(DataRequired(), Length(min=8, max=128)),
    )


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField(
        "Current password", validators=(DataRequired(),),
    )
    new_password = PasswordField(
        "New password", validators=(DataRequired(), Length(min=8, max=128)),
    )
    confirm_password = PasswordField(
        "Confirm new password",
        validators=(DataRequired(), EqualTo("new_password", message="Passwords must match.")),
    )


class DisplayNameForm(FlaskForm):
    display_name = StringField(
        "Display Name",
        filters=(_strip,),
        validators=(DataRequired(), Length(max=80)),
    )


class AppearanceForm(FlaskForm):
    highlight_color = StringField(
        "Highlight colour",
        validators=(
            DataRequired(),
            Regexp(r"^#[0-9a-fA-F]{6}$", message="Choose a valid colour."),
        ),
    )
    night_mode = BooleanField("Night mode")


class DeleteAccountForm(FlaskForm):
    current_password = PasswordField(
        "Current password", validators=(DataRequired(),),
    )


class ProjectForm(FlaskForm):
    name = StringField("Name", validators=(DataRequired(), Length(max=120)))
    color = SelectField("Colour", choices=(), coerce=str)


class TaskForm(FlaskForm):
    title = StringField("Title", validators=(DataRequired(), Length(max=255)))
    notes = TextAreaField("Notes", validators=(Optional(),))
    due_date = DateField("Due date", validators=(Optional(),), format="%Y-%m-%d")
    recurrence = SelectField("Repeat", choices=RECURRENCE_CHOICES, default="none")
    project_id = SelectField("Project", coerce=int, validators=(Optional(),))
    parent_id = SelectField("Parent task", coerce=int, validators=(Optional(),))

    def validate(self, *args, **kwargs) -> bool:
        ok = super().validate(*args, **kwargs)
        if ok and self.recurrence.data not in ("none", None) and self.due_date.data is None:
            self.recurrence.errors.append("Give the task a due date to repeat it.")
            return False
        return ok


class WorkDateForm(FlaskForm):
    """Add one date, or a range of dates (leave 'end' empty for a single date)."""
    start = DateField("Date", validators=(Optional(),), format="%Y-%m-%d")
    end = DateField("Range end (optional)", validators=(Optional(),), format="%Y-%m-%d")

    def validate(self, *args, **kwargs) -> bool:
        ok = super().validate(*args, **kwargs)
        if not ok:
            return False
        if self.start.data is None:
            self.start.errors.append("Pick at least one date.")
            return False
        if self.end.data is not None and self.end.data < self.start.data:
            self.end.errors.append("Range end must not be before the start.")
            return False
        if self.end.data is not None:
            span = (self.end.data - self.start.data).days + 1
            max_days = current_app.config["MAX_WORK_RANGE_DAYS"]
            if span > max_days:
                self.end.errors.append(f"Ranges are limited to {max_days} days.")
                return False
        return True


class DueDateForm(FlaskForm):
    due_date = DateField("Due date", validators=(Optional(),), format="%Y-%m-%d")


class QuickTaskForm(FlaskForm):
    """Minimal inline 'add a task' form (used on project pages and sub-tasks)."""
    title = StringField("Title", validators=(DataRequired(), Length(max=255)))


class DeleteForm(FlaskForm):
    pass
