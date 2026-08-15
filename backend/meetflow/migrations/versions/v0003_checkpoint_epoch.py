"""Isolate checkpoints from a stale owner after lease takeover."""
import sqlalchemy as sa
from alembic import op

revision = '0003'
down_revision = '0002'


def upgrade():
    op.add_column('work_leases', sa.Column('checkpoint_epoch', sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    raise RuntimeError('Restore a verified backup to roll back; destructive schema downgrade is disabled')
