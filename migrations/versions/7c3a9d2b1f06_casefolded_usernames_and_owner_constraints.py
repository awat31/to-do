"""Enforce case-folded usernames and same-owner task references

Revision ID: 7c3a9d2b1f06
Revises: 5419f92104dc

"""
import sqlalchemy as sa
from alembic import op

revision = "7c3a9d2b1f06"
down_revision = "5419f92104dc"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    users = bind.execute(sa.text("SELECT id, username FROM user")).fetchall()
    keys = {}
    updates = []
    for user_id, username in users:
        key = username.casefold()
        if key in keys:
            raise RuntimeError(
                "Cannot enforce case-insensitive usernames: "
                f"user ids {keys[key]} and {user_id} collide."
            )
        keys[key] = user_id
        updates.append({"id": user_id, "username_key": key})

    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(sa.Column("username_key", sa.String(255), nullable=True))

    if updates:
        bind.execute(
            sa.text("UPDATE user SET username_key = :username_key WHERE id = :id"),
            updates,
        )

    with op.batch_alter_table("user") as batch_op:
        batch_op.alter_column(
            "username_key", existing_type=sa.String(255), nullable=False
        )
        batch_op.create_unique_constraint(
            "uq_user_username_key", ["username_key"]
        )

    with op.batch_alter_table("project") as batch_op:
        batch_op.create_unique_constraint(
            "uq_project_id_user_id", ["id", "user_id"]
        )

    # SQLite checks the self-reference against the old table during the copy.
    op.create_index(
        "ix_task_id_user_id_ref", "task", ["id", "user_id"], unique=True
    )
    with op.batch_alter_table("task") as batch_op:
        batch_op.create_unique_constraint("uq_task_id_user_id", ["id", "user_id"])
        batch_op.create_foreign_key(
            "fk_task_project_owner",
            "project",
            ["project_id", "user_id"],
            ["id", "user_id"],
        )
        batch_op.create_foreign_key(
            "fk_task_parent_owner",
            "task",
            ["parent_id", "user_id"],
            ["id", "user_id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_task_recurrence_source_owner",
            "task",
            ["recurrence_source_id", "user_id"],
            ["id", "user_id"],
        )
    op.drop_index("ix_task_id_user_id_ref", table_name="task")


def downgrade():
    with op.batch_alter_table("task") as batch_op:
        batch_op.drop_constraint("fk_task_recurrence_source_owner", type_="foreignkey")
        batch_op.drop_constraint("fk_task_parent_owner", type_="foreignkey")
        batch_op.drop_constraint("fk_task_project_owner", type_="foreignkey")
        batch_op.drop_constraint("uq_task_id_user_id", type_="unique")

    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_constraint("uq_project_id_user_id", type_="unique")

    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_constraint("uq_user_username_key", type_="unique")
        batch_op.drop_column("username_key")
