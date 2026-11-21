"""PR3门槛独立反例：所有分数/测量均为单元测试合成值，不构成真实语音或保留集证据。"""
from copy import deepcopy
from dataclasses import dataclass
import math
import pytest
from app.rsi.gate import HARD_SAFETY_ASSERTIONS,evaluate_gate,adopt,record_rejection,rollback,load_version_store


@dataclass
class Score:
    scenario_id: str
    passed: bool=True
    arm: str='synthetic-unit-only'
    repeat: int=1
    def completion(self):return float(self.passed)
    @property
    def assertions(self):
        return [{'id':key,'passed':True} for key in HARD_SAFETY_ASSERTIONS]+[{'id':'business_effect_once','passed':self.passed}]


def evidence():
    schema='gate-v2-evidence-1'
    arm=lambda prefix:{'calls':10,'turns':30,'failed_calls':0,'p50_ms':2000.0,'sample_ids':[prefix+str(i) for i in range(10)]}
    voice={'schema_version':schema,'source':'real_voice','source_refs':['sha256:'+'a'*64],'baseline':arm('b'),'candidate':arm('c')}
    measure={'real_model_requests':30,'model_ttft_p50_ms':1000.0,'total_cost':1.0}
    cost={'schema_version':schema,'source':'real_model','source_refs':['sha256:'+'b'*64],'cost_unit':'CNY',
          'baseline':deepcopy(measure),'candidate':{**measure,'model_ttft_p50_ms':1100.0,'total_cost':1.1}}
    return voice,cost


def gate(voice,cost,**extra):
    params={'dev_baseline':[Score('d'+str(i%24)) for i in range(72)],
        'dev_candidate':[Score('d'+str(i%24)) for i in range(72)],
        'holdout_baseline':[Score('h'+str(i),i!=0) for i in range(12)],
        'holdout_candidate':[Score('h'+str(i)) for i in range(12)],
        'repeats':3,'budget_snapshot':{'requests':144,'exhausted':False,'rejected_by_budget':0},
        'voice_evidence':voice,'cost_evidence':cost,'holdout_split':'synthetic-unit-only',
        'holdout_provenance':{'file_leakage':False,'reviewer_exposure':False}}
    params.update(extra)
    return evaluate_gate(**params)


def test_exact_thresholds_are_permitted_only_when_all_other_conditions_hold():
    voice,cost=evidence();result=gate(voice,cost)
    assert result.decision=='adopt' and result.evidence_sufficient


@pytest.mark.parametrize('section,key,value',[
    ('voice','calls',9),('voice','turns',29),('voice','p50_ms',2000.01),('voice','failed_calls',1),
    ('voice','calls',-1),('voice','calls',True),('voice','turns',-1),('voice','failed_calls',-1),
    ('voice','failed_calls',11),('voice','p50_ms',-1),('voice','p50_ms',float('nan')),('voice','p50_ms',float('inf')),
    ('cost','real_model_requests',-1),('cost','real_model_requests',True),
    ('cost','model_ttft_p50_ms',1101),('cost','model_ttft_p50_ms',float('nan')),
    ('cost','total_cost',1.101),('cost','total_cost',-1),('cost','total_cost',float('inf')),
])
def test_insufficient_invalid_or_worse_evidence_never_adopts(section,key,value):
    voice,cost=evidence();target=voice if section=='voice' else cost
    target['candidate'][key]=value
    assert gate(voice,cost).decision!='adopt'


@pytest.mark.parametrize('section,path',[
    ('voice',('baseline',)),('voice',('candidate',)),('voice',('baseline','calls')),('voice',('candidate','calls')),
    ('voice',('baseline','failed_calls')),('voice',('candidate','failed_calls')),('voice',('candidate','sample_ids')),
    ('voice',('source_refs',)),('voice',('source',)),('cost',('baseline',)),('cost',('candidate',)),
    ('cost',('baseline','real_model_requests')),('cost',('candidate','real_model_requests')),
    ('cost',('baseline','model_ttft_p50_ms')),('cost',('baseline','total_cost')),('cost',('source_refs',)),
])
def test_missing_baseline_candidate_counts_or_provenance_refuses(section,path):
    voice,cost=evidence();target=voice if section=='voice' else cost
    for key in path[:-1]:target=target[key]
    del target[path[-1]]
    result=gate(voice,cost)
    assert result.decision=='refuse_auto_adopt' and not result.evidence_sufficient


@pytest.mark.parametrize('section',('voice','cost'))
def test_synthetic_or_duplicate_source_is_not_real_evidence(section):
    voice,cost=evidence();target=voice if section=='voice' else cost
    target['source']='synthetic'
    assert gate(voice,cost).decision=='refuse_auto_adopt'


def test_original_one_sample_probe_is_refused():
    cost={'model_ttft_p50_ms':100,'baseline_ttft_p50_ms':100,'real_model_requests':1}
    result=gate({'real_voice_samples':1,'calls':1,'turns':1,'p50_ms':9000,'failure_rate':1.0},cost)
    assert result.decision=='refuse_auto_adopt' and not result.evidence_sufficient


def test_duplicate_call_identifiers_refuse_even_when_count_claims_ten():
    voice,cost=evidence();voice['candidate']['sample_ids']=['same']*10
    assert gate(voice,cost).decision=='refuse_auto_adopt'


@pytest.mark.parametrize('field',('model_ttft_p50_ms','total_cost'))
@pytest.mark.parametrize('value',(0,-1,float('nan'),float('inf')))
def test_invalid_baseline_never_skips_cost_or_ttft_comparison(field,value):
    voice,cost=evidence();cost['baseline'][field]=value
    assert gate(voice,cost).decision=='refuse_auto_adopt'


def test_missing_real_evidence_or_exposed_holdout_keeps_refusal():
    voice,cost=evidence()
    assert gate(None,cost).decision=='refuse_auto_adopt'
    assert gate(voice,cost,holdout_provenance={'file_leakage':False,'reviewer_exposure':True}).decision=='refuse_auto_adopt'


def test_refusal_does_not_change_active_version_and_cas_rollback_remain_enforced(tmp_path):
    voice,cost=evidence();voice['candidate']['calls']=9;bad=gate(voice,cost)
    path=tmp_path/'versions.json';store={'active_version':'verified-v1','history':[{'action':'adopt','to':'verified-v1'}]}
    record_rejection(bad,store=store,path=path)
    with pytest.raises(PermissionError):adopt(bad,expected_active='verified-v1',path=path)
    assert load_version_store(path)['active_version']=='verified-v1'
    with pytest.raises(ValueError):rollback(to_version='never-verified',reason='unit',path=path)
    voice,cost=evidence()
    with pytest.raises(ValueError):adopt(gate(voice,cost),expected_active='stale',path=path)
    assert load_version_store(path)['active_version']=='verified-v1'


def test_hard_safety_failure_rejects_even_without_runtime_evidence():
    class UnsafeScore(Score):
        @property
        def assertions(self):
            return [{'id':key,'passed':False} for key in HARD_SAFETY_ASSERTIONS]
    result=gate(None,None,holdout_candidate=[UnsafeScore('unsafe')]*12)
    assert result.decision=='reject' and not result.evidence_sufficient


@pytest.mark.parametrize('section,key,value',[
    ('voice','p50_ms',2001),('voice','failed_calls',1),
    ('cost','model_ttft_p50_ms',1101),('cost','total_cost',1.101)])
def test_complete_measured_threshold_failures_reject(section,key,value):
    voice,cost=evidence();target=voice if section=='voice' else cost
    target['candidate'][key]=value
    result=gate(voice,cost)
    assert result.decision=='reject' and result.evidence_sufficient
