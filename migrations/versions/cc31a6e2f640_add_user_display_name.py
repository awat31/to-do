"""Add user display names.

Revision ID: cc31a6e2f640
Revises: b9c2a61e7d34
"""
import sqlalchemy as sa
from alembic import op

revision = "cc31a6e2f640"
down_revision = "b9c2a61e7d34"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.add_column(sa.Column("display_name", sa.String(80), nullable=True))

    op.execute(sa.text("UPDATE user SET display_name = username"))

    with op.batch_alter_table("user") as batch_op:
        batch_op.alter_column(
            "display_name",
            existing_type=sa.String(80),
            nullable=False,
        )


def downgrade():
    with op.batch_alter_table("user") as batch_op:
        batch_op.drop_column("display_name")
