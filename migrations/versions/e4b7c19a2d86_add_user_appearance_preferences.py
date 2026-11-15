"""Add per-user appearance preferences.

Revision ID: e4b7c19a2d86
Revises: cc31a6e2f640
"""
import sqlalchemy as sa
from alembic import op

revision = "e4b7c19a2d86"
down_revision = "cc31a6e2f640"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(
            sa.Column(
                "highlight_color",
                sa.String(length=7),
                nullable=False,
                server_default=sa.text("'#f97316'"),
            )
        )
        batch_op.add_column(
            sa.Column(
                "night_mode",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )


def downgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("night_mode")
        batch_op.drop_column("highlight_color")
