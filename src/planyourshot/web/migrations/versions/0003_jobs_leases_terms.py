"""durable jobs, database-backed rate limits and leases, terms acceptance

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column('terms_accepted_at', sa.DateTime(), nullable=True))

    op.create_table('jobs',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('script_id', sa.String(length=32), nullable=True),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=10), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('max_attempts', sa.Integer(), nullable=False),
    sa.Column('progress_done', sa.Integer(), nullable=False),
    sa.Column('progress_total', sa.Integer(), nullable=False),
    sa.Column('error', sa.Text(), nullable=False),
    sa.Column('available_at', sa.DateTime(), nullable=False),
    sa.Column('locked_at', sa.DateTime(), nullable=True),
    sa.Column('locked_by', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_jobs_script_id', 'jobs', ['script_id'], unique=False)
    op.create_index('ix_jobs_user_id', 'jobs', ['user_id'], unique=False)
    op.create_index('ix_jobs_status_available', 'jobs', ['status', 'available_at', 'id'], unique=False)

    op.create_table('rate_limit_hits',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('key', sa.String(length=200), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_rate_limit_hits_key_created', 'rate_limit_hits', ['key', 'created_at'], unique=False)

    op.create_table('leases',
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('token', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('name')
    )
    op.create_index('ix_leases_expires_at', 'leases', ['expires_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_leases_expires_at', table_name='leases')
    op.drop_table('leases')
    op.drop_index('ix_rate_limit_hits_key_created', table_name='rate_limit_hits')
    op.drop_table('rate_limit_hits')
    op.drop_index('ix_jobs_status_available', table_name='jobs')
    op.drop_index('ix_jobs_user_id', table_name='jobs')
    op.drop_index('ix_jobs_script_id', table_name='jobs')
    op.drop_table('jobs')
    with op.batch_alter_table('users') as batch:
        batch.drop_column('terms_accepted_at')
