"""Offline final evidence: keep every variant and all failed attempts."""
import hashlib,json,sys,os
from pathlib import Path
from collections import Counter
from summarize_realtime_measurement import summarize,distribution
from report_realtime_latency import compare
ROOT=Path(__file__).resolve().parents[1]


def load(path):return json.loads(path.read_text(encoding='utf-8'))


def main():
    final_path=ROOT/'.cache/v04-latency-plan-20261125-final.json'
    formal=load(final_path);full=summarize(formal)
    old=ROOT.parent/'v04-latency/.cache/v04-latency-20261124-b.json'
    if not old.exists():old=ROOT/'.cache/evidence/latency-old-20261124.json'
    comparison=compare(load(old),formal)
    comparison['metadata_compatible']=comparison['same_conditions']
    comparison['observational_numeric_targets_passed']=comparison['latency_gate_passed']
    comparison['strict_same_conditions_verified']=False
    comparison['same_conditions']=False
    comparison['latency_gate_passed']=False
    comparison['note']='Absolute P50 and observed numeric P95/failure targets passed; historical dates, WAN and knowledge/text differ, so strict same-condition improvement is not established.'
    variants=[]
    for path in sorted((ROOT/'.cache').glob('v04-latency-plan-20261125-*.json')):
        raw=load(path);summary=summarize(raw)
        variants.append({'run':raw['metadata']['run_id'],'raw_path':path.relative_to(ROOT).as_posix(),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
            'config':raw['metadata'].get('provider'),'counts':summary['counts'],'latency':summary['valid_effective_answer'],
            'failures':summary['failures'],'connection_failures':summary['connection_failures'],'measurement_invariants':summary['measurement_invariants']['all_passed'],
            'per_attempt':[{'call':s['call'],'case':s['case'],'status':s['status'],'latency_ms':s.get('effective_answer_ms'),'failure':s.get('failure'),'barge_in_observed':s.get('barge_in_observed')} for s in raw['samples']]})
    matrices={}
    for name in ('tts-candidates','tts-short-control'):
        path=ROOT/'.cache/ddl-plan'/(name+'.json');raw=load(path)
        matrices[name]={'raw_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'component_only':True,'not_browser_latency':True,'profiles':{}}
        for backend in raw['profiles']:
            rows=[s for s in raw['samples'] if s['backend']==backend];success=[s for s in rows if s['status']=='success']
            matrices[name]['profiles'][backend]={'attempts':len(rows),'success':len(success),'failures':[{'case':s['case'],'attempt':s['within_connection'],'code':s.get('code')} for s in rows if s['status']!='success'],
                'first_voiced_pcm':distribution([s['first_voiced_pcm_ms'] for s in success]),'per_case':{case:{'text_chars':next(s['text_chars'] for s in rows if s['case']==case),'first_voiced_pcm':distribution([s['first_voiced_pcm_ms'] for s in success if s['case']==case])} for case in ['identity','guarantee','source']}}
    shadows=[call['shadow'] for path in (ROOT/'.cache').glob('v04-latency-plan-20261125-*.json') for call in load(path)['calls'] if call.get('shadow')]
    final_budget=None
    if os.getenv('DATABASE_URL','').endswith('zhiyu_ddl_plan_20261125_test'):
        import psycopg
        with psycopg.connect(os.environ['DATABASE_URL'].replace('postgresql+psycopg://','postgresql://')) as db:
            final_budget=dict(db.execute("select split_part(key,':',4),data->'count' from request_receipts where key like 'cloud-budget:%v04-latency-plan-20261125:%'").fetchall())
    demo_path=ROOT/'.cache/ddl-plan/final-demo.json'
    demo=load(demo_path) if demo_path.exists() else None
    if demo:demo={k:demo.get(k) for k in ['passed','attempt','attempt_report','synthetic_microphone','real_cloud_audio','voice_auto_close','voice_operations','spoken_barge_in','mock_jingmai_closed','video','audio_offset_ms','failure','mux_exit']}
    evidence={'date':'2026-11-25','application_freeze':'d164cd1','knowledge_version':'workbench-reviewed-20261125-v3','formal':full,'comparison_to_old_1621ms':comparison,'old_baseline_stage_summary':summarize(load(old))['stages'],
        'variants':variants,'tts_experiments':matrices,'shadow':shadows,
        'shadow_timing_limitation':'Initial isolation observer reused an already-set audio_started event for attempts 2-4; those onset timings are invalid and must not be used. All three complete candidates had text_matches=false; no candidate reached RTC. Observer subsequently corrected. Raw original results retained.',
        'verification':{'backend_passed':556,'browser_passed':5,'typecheck':'passed','production_build':'passed','voice_writes_enabled':False,'physical_speaker_verified':False,'trusted_playback_proof':False,'real_cloud_business_writes':0},
        'all_attempts_retained':True,'final_authorized_budget':{'limits':{'native_realtime_session':26,'native_realtime_turn':130,'tts':200},'used':final_budget},'workbench_demo':demo,'comparison_limits':['Historical and current runs use the same fixed WAVs and browser measurement; WAN/provider conditions and dates are not identical.','Old 1.6s text length/knowledge version differ from v3; stage medians cannot be added or treated as causal attribution.','Synthetic fixed audio over localhost RTC plus real provider WAN does not validate physical microphone/speaker, TURN, packet loss or double talk.','Natural-pause recognition did not pass; semantic300 is not the delivery default.','Formal stable-profile failure rate and experimental-variant failures are reported separately, not erased or combined to claim a winner.']}
    (ROOT/'docs/ddl-final-performance-20261125.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (ROOT/'docs/ddl-final-comparison-20261125.json').write_text(json.dumps(comparison,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'counts':full['counts'],'latency':full['valid_effective_answer'],'comparison':comparison,'invariants':full['measurement_invariants']['all_passed']},ensure_ascii=False))

if __name__=='__main__':main()