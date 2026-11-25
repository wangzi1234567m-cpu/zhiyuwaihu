"""Explicit test-only reset; refuses normal development databases."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.db import engine,Session
from app.seed import seed,seed_workbench
from sqlalchemy import text
if not (engine.url.database or '').endswith('_test'):
    raise SystemExit('Refusing reset: database name must end in _test')
with Session.begin() as db:
    db.execute(text('TRUNCATE request_receipts, confirmations, call_records, business_effects, operations, events, messages, sessions, knowledge, applications CASCADE'))
seed_workbench() if '--workbench' in sys.argv else seed()
print('Dedicated test database reset.')
