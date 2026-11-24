from alembic import context
from app.db import Base, engine
from app.realtime_business.events import RealtimeEventRow  # register additive table metadata
from app.config import settings

def run():
    if context.is_offline_mode():
        context.configure(url=settings.database_url,target_metadata=Base.metadata,literal_binds=True,dialect_opts={'paramstyle':'named'})
        with context.begin_transaction():
            context.run_migrations()
        return
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()

run()
