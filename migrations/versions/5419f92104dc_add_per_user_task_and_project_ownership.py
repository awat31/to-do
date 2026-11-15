"""Add per-user task and project ownership

Revision ID: 5419f92104dc
Revises: 224956dfea21
Create Date: 2026-11-15 14:24:28.257689

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5419f92104dc'
down_revision = '224956dfea21'
branch_labels = None
depends_on = None


OWNED_TABLES = ("project", "task", "deleted_batch")


def upgrade():
    # Add the columns nullable first so existing single-user rows can be
    # backfilled, then tighten them to NOT NULL.
    for table in OWNED_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column('user_id', sa.Integer(), nullable=True))
            batch_op.create_index(
                batch_op.f(f'ix_{table}_user_id'), ['user_id'], unique=False
            )
            batch_op.create_foreign_key(
                f'fk_{table}_user_id_user', 'user',
                ['user_id'], ['id'], ondelete='CASCADE',
            )

    # Existing data belonged to the original admin account. New installs have
    # no rows at this point, so the update is a no-op there.
    bind = op.get_bind()
    first_user_id = bind.execute(
        sa.text("SELECT id FROM user ORDER BY id LIMIT 1")
    ).scalar()
    if first_user_id is not None:
        for table in OWNED_TABLES:
            bind.execute(
                sa.text(f"UPDATE {table} SET user_id = :uid WHERE user_id IS NULL"),
                {"uid": first_user_id},
            )

    for table in OWNED_TABLES:
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.alter_column(
                'user_id', existing_type=sa.Integer(), nullable=False
            )


def downgrade():
    for table in reversed(OWNED_TABLES):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.drop_constraint(
                f'fk_{table}_user_id_user', type_='foreignkey'
            )
            batch_op.drop_index(batch_op.f(f'ix_{table}_user_id'))
            batch_op.drop_column('user_id')
