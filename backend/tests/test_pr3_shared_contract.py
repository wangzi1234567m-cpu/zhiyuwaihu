"""魏侧接线/守卫回归；不接管翟的 Mock 事务，也不访问真实供应商。"""
from dataclasses import replace
from uuid import UUID,uuid4
import pytest
from app.adapters import Services,DomainError,FakeModel
from app.guard import ToolGuard
from test_semantic import make_case,make_entry
from test_review_regressions import guard_data,reject_command


def test_legacy_workbench_mode_never_enables_production_glm(monkeypatch):
    monkeypatch.setenv('ZHIYU_MODEL_MODE','glm')
    monkeypatch.setenv('GLM_API_KEY','synthetic-test-value')
    services=Services()
    assert isinstance(services.model,FakeModel)
    assert services.understanding.name=='rules'
    assert services.model_info()['provider']=='fake'
    with pytest.raises(DomainError,match='MODEL_PROVIDER'):Services(model_mode='glm')


def test_main_deepseek_retains_durable_reservation_and_ttft_observer(monkeypatch):
    import app.adapters as adapters
    from app.cloud_budget import reserve_call,observe_model_first_content
    monkeypatch.setattr(adapters,'settings',replace(adapters.settings,model_provider='deepseek'))
    services=Services()
    assert services.model.name=='deepseek'
    assert services.model.reserve is reserve_call
    assert services.model.observe is observe_model_first_content


def test_all_explicit_main_ports_are_preserved():
    ports={name:object() for name in ('agent','model','cases','knowledge','business','policy','guard','voice','repository','understanding')}
    services=Services(**ports)
    assert all(getattr(services,name) is value for name,value in ports.items())


@pytest.mark.parametrize('enabled,proof',[(False,False),(False,True),(True,False),(True,True)])
def test_voice_requires_both_enabled_writes_and_trusted_playback(monkeypatch,enabled,proof):
    import app.config as config
    monkeypatch.setattr(config,'settings',replace(config.settings,voice_writes_enabled=enabled))
    case=make_case();entry=make_entry(case,channel='jingmai');data=guard_data(case)
    data.update(mode='voice',voice_explanation_verified=proof)
    command=reject_command(case,entry,UUID(data['routing_decision']['decision_id']))
    if enabled and proof:ToolGuard().authorize(case,data,command,(entry,))
    else:
        with pytest.raises(DomainError) as exc:ToolGuard().authorize(case,data,command,(entry,))
        assert exc.value.code=='VOICE_EXPLANATION_UNVERIFIED'


def test_voice_playback_proof_does_not_bypass_revision_or_decision_binding(monkeypatch):
    import app.config as config
    monkeypatch.setattr(config,'settings',replace(config.settings,voice_writes_enabled=True))
    case=make_case();entry=make_entry(case,channel='jingmai');data=guard_data(case)
    data.update(mode='voice',voice_explanation_verified=True)
    command=reject_command(case,entry,uuid4())
    with pytest.raises(DomainError):ToolGuard().authorize(case,data,command,(entry,))
    command=command.model_copy(update={'decision_id':UUID(data['routing_decision']['decision_id'])})
    with pytest.raises(DomainError):ToolGuard().authorize(case.model_copy(update={'case_revision':2}),data,command,(entry,))
