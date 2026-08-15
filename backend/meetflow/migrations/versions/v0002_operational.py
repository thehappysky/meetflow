"""Durable work ownership and role conversation journals."""
import sqlalchemy as sa
from alembic import op

revision = '0002'
down_revision = '0001'


def upgrade():
    op.create_table('work_leases', sa.Column('id', sa.String(160), primary_key=True),
                    sa.Column('owner', sa.String(120), nullable=True), sa.Column('generation', sa.Integer(), nullable=False),
                    sa.Column('expires_at', sa.Float(), nullable=False), sa.Column('attempts', sa.Integer(), nullable=False))
    op.create_table('agent_conversations', sa.Column('id', sa.String(240), primary_key=True),
                    sa.Column('run_id', sa.String(80), sa.ForeignKey('runs.id'), nullable=False),
                    sa.Column('task_key', sa.String(), nullable=False), sa.Column('fingerprint', sa.String(64), nullable=False),
                    sa.Column('steps', sa.Integer(), nullable=False), sa.Column('messages', sa.JSON(), nullable=False),
                    sa.UniqueConstraint('run_id', 'task_key'))
    op.create_index('ix_agent_conversations_run_id', 'agent_conversations', ['run_id'])


def downgrade():
    raise RuntimeError('Restore a verified backup to roll back; destructive schema downgrade is disabled')
