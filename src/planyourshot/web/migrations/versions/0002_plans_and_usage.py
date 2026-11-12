"""plans, disabled accounts, refused messages and usage metering

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('plan', sa.String(length=16), nullable=False, server_default='free'))
        batch.add_column(sa.Column('disabled', sa.Boolean(), nullable=False, server_default=sa.false()))
    with op.batch_alter_table('messages') as batch:
        batch.add_column(sa.Column('refused', sa.Boolean(), nullable=False, server_default=sa.false()))

    op.create_table('usage_events',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('script_id', sa.String(length=32), nullable=True),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('input_chars', sa.Integer(), nullable=False),
    sa.Column('output_chars', sa.Integer(), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.Column('run_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_usage_events_user_id', 'usage_events', ['user_id'], unique=False)
    op.create_index('ix_usage_events_created_at', 'usage_events', ['created_at'], unique=False)
    op.create_index('ix_usage_user_kind_created', 'usage_events', ['user_id', 'kind', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_usage_user_kind_created', table_name='usage_events')
    op.drop_index('ix_usage_events_created_at', table_name='usage_events')
    op.drop_index('ix_usage_events_user_id', table_name='usage_events')
    op.drop_table('usage_events')
    with op.batch_alter_table('messages') as batch:
        batch.drop_column('refused')
    with op.batch_alter_table('users') as batch:
        batch.drop_column('disabled')
        batch.drop_column('plan')
