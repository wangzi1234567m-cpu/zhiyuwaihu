"""Persist message order independently of clock precision or UUID order."""
from alembic import op
import sqlalchemy as sa

revision='0002'
down_revision='0001'

def upgrade():
    op.add_column('messages',sa.Column('position',sa.Integer(),nullable=True))
    op.execute("""WITH ranked AS (
        SELECT id,row_number() OVER (PARTITION BY session_id ORDER BY created_at,
            CASE WHEN data->'view'->>'role'='user' THEN 0 ELSE 1 END,id) AS n FROM messages
        ) UPDATE messages SET position=ranked.n FROM ranked WHERE messages.id=ranked.id""")
    op.alter_column('messages','position',nullable=False)
    op.create_unique_constraint('uq_messages_session_position','messages',['session_id','position'])
    op.execute("""UPDATE sessions SET data=jsonb_set(data,'{message_count}',
        to_jsonb((SELECT count(*) FROM messages WHERE messages.session_id=sessions.id)))""")

def downgrade():
    op.drop_constraint('uq_messages_session_position','messages',type_='unique')
    op.drop_column('messages','position')
