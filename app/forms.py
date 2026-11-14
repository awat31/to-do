"""WTForms used across the app."""
from flask_wtf import FlaskForm
from wtforms import DateField, IntegerField, PasswordField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, ValidationError

MAX_WORK_RANGE_DAYS = 90


class LoginForm(FlaskForm):
    username = StringField("Username", validators=(DataRequired(), Length(max=80)))
    password = PasswordField("Password", validators=(DataRequired(),))


class ProjectForm(FlaskForm):
    name = StringField("Name", validators=(DataRequired(), Length(max=120)))
    color = SelectField("Colour", choices=(), coerce=str)


class TaskForm(FlaskForm):
    title = StringField("Title", validators=(DataRequired(), Length(max=255)))
    notes = TextAreaField("Notes", validators=(Optional(),))
    due_date = DateField("Due date", validators=(Optional(),), format="%Y-%m-%d")
    project_id = SelectField("Project", coerce=int, validators=(Optional(),))
    parent_id = SelectField("Parent task", coerce=int, validators=(Optional(),))


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
            if span > MAX_WORK_RANGE_DAYS:
                self.end.errors.append(f"Ranges are limited to {MAX_WORK_RANGE_DAYS} days.")
                return False
        return True


class DueDateForm(FlaskForm):
    due_date = DateField("Due date", validators=(Optional(),), format="%Y-%m-%d")


class NotesForm(FlaskForm):
    notes = TextAreaField("Notes")


class QuickTaskForm(FlaskForm):
    """Minimal inline 'add a task' form (used on project pages and sub-tasks)."""
    title = StringField("Title", validators=(DataRequired(), Length(max=255)))


class DeleteForm(FlaskForm):
    pass


class PeriodJumpForm(FlaskForm):
    value = StringField(validators=(DataRequired(),))
