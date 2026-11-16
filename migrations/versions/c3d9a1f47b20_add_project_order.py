"""Add user-managed project ordering.

Revision ID: c3d9a1f47b20
Revises: e4b7c19a2d86
"""
import sqlalchemy as sa
from alembic import op


revision = "c3d9a1f47b20"
down_revision = "e4b7c19a2d86"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("project") as batch_op:
        batch_op.add_column(
            sa.Column("position", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade():
    with op.batch_alter_table("project") as batch_op:
        batch_op.drop_column("position")
