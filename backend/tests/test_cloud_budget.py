import os
import pytest
from app.cloud_budget import reserve_call
from app.adapters import DomainError
from app.db import Session,RequestReceipt,engine
from sqlalchemy import delete

pytestmark=[pytest.mark.integration,pytest.mark.skipif(os.getenv('RUN_DB_TESTS')!='1',reason='dedicated PostgreSQL required')]

def test_budget_survives_adapter_recreation():
    assert (engine.url.database or '').endswith('_test')
    with Session.begin() as db:db.execute(delete(RequestReceipt).where(RequestReceipt.key.like('cloud-budget:%:test-budget')))
    reserve_call('test-budget',1)
    with pytest.raises(DomainError) as error:reserve_call('test-budget',1)
    assert error.value.code=='LOCAL_CLOUD_LIMIT'
