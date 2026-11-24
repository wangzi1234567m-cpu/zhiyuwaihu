"""Durable v0.4 adaptation log; original business state and ledgers are retained."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0004'
down_revision = '0003'


def upgrade():
    op.create_table('realtime_events',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('session_id', sa.String(36), sa.ForeignKey('sessions.id'), nullable=False),
        sa.Column('seq', sa.Integer(), nullable=False),
        sa.Column('data', JSONB(), nullable=False),
        sa.UniqueConstraint('session_id', 'seq'))
    op.create_index('ix_realtime_events_session_id', 'realtime_events', ['session_id'])


def downgrade():
    op.drop_table('realtime_events')
