"""Add an administrator flag to users.

Revision ID: b9c2a61e7d34
Revises: 7c3a9d2b1f06
"""
import sqlalchemy as sa
from alembic import op

revision = "b9c2a61e7d34"
down_revision = "7c3a9d2b1f06"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(
            sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    op.execute(
        sa.text("UPDATE user SET is_admin = 1 WHERE username_key = 'admin'")
    )


def downgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("is_admin")
