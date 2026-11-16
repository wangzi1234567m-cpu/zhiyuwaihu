"""Initial synthetic text platform schema."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0001'
down_revision = None

def upgrade():
    # Keep this migration independent from future ORM definitions.
    op.create_table('applications', sa.Column('id',sa.String(36),primary_key=True),sa.Column('owner',sa.String(80),nullable=False),sa.Column('data',JSONB,nullable=False))
    op.create_index('ix_applications_owner','applications',['owner'])
    op.create_table('knowledge',sa.Column('id',sa.String(36),primary_key=True),sa.Column('version',sa.String(80),nullable=False),sa.Column('data',JSONB,nullable=False))
    op.create_table('sessions',sa.Column('id',sa.String(36),primary_key=True),sa.Column('application_id',sa.String(36),sa.ForeignKey('applications.id'),nullable=False),sa.Column('owner',sa.String(80),nullable=False),sa.Column('state',sa.String(40),nullable=False),sa.Column('revision',sa.Integer,nullable=False),sa.Column('seq',sa.Integer,nullable=False),sa.Column('data',JSONB,nullable=False))
    for col in ['application_id','owner']: op.create_index(f'ix_sessions_{col}','sessions',[col])
    for table in ['messages','events']:
        cols=[sa.Column('id',sa.String(36),primary_key=True),sa.Column('session_id',sa.String(36),sa.ForeignKey('sessions.id'),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('data',JSONB,nullable=False)]
        if table=='events': cols += [sa.Column('seq',sa.Integer,nullable=False),sa.UniqueConstraint('session_id','seq')]
        op.create_table(table,*cols)
        op.create_index(f'ix_{table}_session_id',table,['session_id'])
    op.create_table('operations',sa.Column('id',sa.String(36),primary_key=True),sa.Column('session_id',sa.String(36),sa.ForeignKey('sessions.id'),nullable=False),sa.Column('key',sa.String(200),nullable=False,unique=True),sa.Column('status',sa.String(30),nullable=False),sa.Column('data',JSONB,nullable=False))
    for col in ['session_id','status']: op.create_index(f'ix_operations_{col}','operations',[col])
    op.create_table('business_effects',sa.Column('key',sa.String(200),primary_key=True),sa.Column('operation_id',sa.String(36),sa.ForeignKey('operations.id'),nullable=False,unique=True),sa.Column('data',JSONB,nullable=False))
    for table in ['call_records','confirmations']:
        op.create_table(table,sa.Column('id',sa.String(36),primary_key=True),sa.Column('session_id',sa.String(36),sa.ForeignKey('sessions.id'),nullable=False,unique=table=='call_records'),sa.Column('data',JSONB,nullable=False))
    op.create_index('ix_confirmations_session_id','confirmations',['session_id'])
    op.create_table('request_receipts',sa.Column('key',sa.String(300),primary_key=True),sa.Column('payload_hash',sa.String(64),nullable=False),sa.Column('data',JSONB,nullable=False))

def downgrade():
    for table in ['request_receipts','confirmations','call_records','business_effects','operations','events','messages','sessions','knowledge','applications']:
        op.drop_table(table)
