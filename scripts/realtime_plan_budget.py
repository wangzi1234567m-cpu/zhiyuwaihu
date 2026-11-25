"""Explicit user-approved experiment envelope; all new variants share this ledger."""
import os
LIMITS={'native_realtime_session':26,'native_realtime_turn':130,'tts':200}
BUDGET_ID='v04-latency-plan-20261125'
AUTHORIZATION='20261125-26-130-200'

def require_authorization():
    if os.getenv('LATENCY_PLAN_AUTHORIZED')!=AUTHORIZATION or not os.getenv('DATABASE_URL','').endswith('_test'):
        raise RuntimeError('Explicit approved plan and dedicated shared test DB required')

def reserve(kind,unused_limit):
    require_authorization()
    if kind not in LIMITS:raise ValueError('Unknown cloud budget kind')
    from app.cloud_budget import reserve_call
    reserve_call(BUDGET_ID+':'+kind,LIMITS[kind])
