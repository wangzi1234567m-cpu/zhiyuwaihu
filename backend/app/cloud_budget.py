"""Persistent conservative daily attempt caps. Not a provider balance estimator."""
from sqlalchemy import text
from .db import Session, RequestReceipt, now
from .adapters import DomainError

def reserve_call(kind,limit):
    day=now().date().isoformat()
    key=f'cloud-budget:{day}:{kind}'
    with Session.begin() as db:
        db.execute(text('SELECT pg_advisory_xact_lock(hashtext(:key))'),{'key':key})
        row=db.get(RequestReceipt,key)
        count=row.data['count'] if row else 0
        if count>=limit: raise DomainError('LOCAL_CLOUD_LIMIT','已达到本地云调用上限',429)
        if row: row.data={'count':count+1}
        else: db.add(RequestReceipt(key=key,payload_hash='budget',data={'count':1}))

def observe_model_first_content(request,duration_ms):
    if not request.session_id:return
    from .db import Conversation
    from .coordinator import emit
    from .contracts import MetricRecorded
    from sqlalchemy import select
    with Session.begin() as db:
        row=db.scalar(select(Conversation).where(Conversation.id==str(request.session_id)).with_for_update())
        if row:
            emit(db,row,MetricRecorded(type='metric.recorded',name='model_ttft',duration_ms=duration_ms,outcome='success',
                clock_domain='server.monotonic',measurement_method='deepseek_http_request_to_first_content'),
                turn=request.turn_id,generation=request.generation_id)
