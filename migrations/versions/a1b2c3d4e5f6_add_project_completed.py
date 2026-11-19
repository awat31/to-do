"""Add completed flag to projects.

Revision ID: a1b2c3d4e5f6
Revises: f8d2e3a90b17
"""
import sqlalchemy as sa
from alembic import op


revision = "a1b2c3d4e5f6"
down_revision = "f8d2e3a90b17"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project") as batch_op:
        batch_op.add_column(
            sa.Column(
                "completed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch_op.create_index("ix_project_completed", ["completed"])


def downgrade():
    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_index("ix_project_completed")
        batch_op.drop_column("completed")
