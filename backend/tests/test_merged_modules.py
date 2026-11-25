"""Selected knowledge releases must also control the injected routing policy."""
from uuid import uuid4

from app.db import Conversation, Session
from app.seed import fixture_id
from test_integration import setup, pytestmark


async def test_selected_release_drives_routing(setup, monkeypatch):
    from publish_reviewed_knowledge import publish

    client, _, services = setup
    case = services.cases.get_case('demo', fixture_id(1))
    entry = services.knowledge.lookup(case.scenario_id, '', ['synthetic'], 'demo-k1')[0]
    entry = entry.model_copy(update={
        'entry_id': uuid4(), 'knowledge_version': 'merged-jingmai-test',
        'channel': 'jingmai', 'required_slots': ['brand_name', 'missing_items'],
        'approved_text': '{brand_name}请补齐{missing_items}',
    })
    publish(entry.knowledge_version, [entry])
    monkeypatch.setenv('ZHIYU_KNOWLEDGE_VERSION', entry.knowledge_version)
    response = await client.post('/api/v1/sessions',
        json={'application_id': str(case.application_id), 'mode': 'text'},
        headers={'Idempotency-Key': str(uuid4())})
    assert response.status_code == 201
    view = response.json()
    assert view['route'] == 'phone'  # Written knowledge cannot change a task's default route.
    sid = view['session_id']
    with Session() as db:
        data = db.get(Conversation, sid).data
        assert data['knowledge_version'] == entry.knowledge_version
        decision = data['routing_decision']
        assert 'channel_phone' in decision['reason_codes']
        assert any(str(entry.entry_id) in item['knowledge_entry_ids']
                   for item in decision['evidence'])
