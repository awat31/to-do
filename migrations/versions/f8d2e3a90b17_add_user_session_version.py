"""Add user.session_version for password-change session invalidation.

Revision ID: f8d2e3a90b17
Revises: c3d9a1f47b20
"""
import sqlalchemy as sa
from alembic import op

revision = "f8d2e3a90b17"
down_revision = "c3d9a1f47b20"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(
            sa.Column("session_version", sa.Integer, nullable=False, server_default="0")
        )


def downgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("session_version")
