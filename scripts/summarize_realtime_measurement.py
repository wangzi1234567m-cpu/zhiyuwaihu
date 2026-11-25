"""Summarize real evidence, retaining invalid/failed attempts and clock uncertainty."""
import argparse
import json
import math
import time
from collections import Counter
from pathlib import Path

def distribution(values):
    values = sorted(x for x in values if isinstance(x, (int, float)) and math.isfinite(x))
    def percentile(p):
        return round(values[max(0, math.ceil(len(values)*p)-1)], 2) if values else None
    return {'n': len(values), 'p50_ms': percentile(.5), 'p95_ms': percentile(.95),
            'min_ms': round(values[0], 2) if values else None,
            'max_ms': round(values[-1], 2) if values else None}

def decompose(sample):
    traces = sample.get('server', {}).get('traces', [])
    if not sample.get('input') or not sample.get('clock_sync'):
        return {}
    offset = sample['clock_sync']['offset_ms']
    end = sample['input']['speech_end_ms'] + offset
    # Filter the current input, excluding the opening and previous turns.
    traces = [t for t in traces if t['at_ms'] >= end-200]
    def first(kind, **fields):
        return next((t for t in traces if t['kind'] == kind and
            all(t.get(k) == v for k,v in fields.items())), None)
    stop = first('provider_event', event='input_audio_buffer.speech_stopped')
    final = first('input_final')
    reviewed = first('reviewed_segment_received', generation_id=sample.get('generation_id'))
    accepted = first('business_accepted', generation_id=sample.get('generation_id'))
    submitted = first('business_submit_start')
    decision_start, decision_end = first('business_decision_start'), first('business_decision_end')
    synthesis = next((t for t in traces if t['kind']=='synthesis_start' and not t['waiting'] and
        reviewed and t['at_ms'] >= reviewed['at_ms']), None)
    pcm = first('synthesis_first_pcm', synthesis_id=synthesis['synthesis_id']) if synthesis else None
    answer = next((m for m in sample.get('browser', {}).get('metrics', []) if m['name']=='first_answer_audio'), None)
    stages = {}
    def delta(name, a, b):
        if a is not None and b is not None:
            stages[name] = round(b-a, 3)
    delta('input_transport_vad_return', end, stop['at_ms'] if stop else None)
    delta('native_understanding_after_stop', stop['at_ms'] if stop else None, final['at_ms'] if final else None)
    delta('business_review_and_event_delivery', final['at_ms'] if final else None, reviewed['at_ms'] if reviewed else None)
    delta('speech_dispatch', reviewed['at_ms'] if reviewed else None, synthesis['at_ms'] if synthesis else None)
    delta('tts_first_pcm', synthesis['at_ms'] if synthesis else None, pcm['at_ms'] if pcm else None)
    delta('pcm_to_browser_observation', pcm['at_ms'] if pcm else None, answer['at']+offset if answer else None)
    delta('business_accept', submitted['at_ms'] if submitted else None, accepted['at_ms'] if accepted else None)
    delta('pure_agent_decision', decision_start['at_ms'] if decision_start else None, decision_end['at_ms'] if decision_end else None)
    sample['stages_ms'] = stages
    return stages

def summarize(report):
    samples = report['samples']
    valid = [s for s in samples if s.get('status')=='success' and s.get('cohort',True)]
    for sample in samples:
        decompose(sample)
    all_audio = [s for s in samples if s.get('effective_answer_ms') is not None]
    stage_names = sorted({k for s in valid for k in s.get('stages_ms', {})})
    additive = ('input_transport_vad_return', 'native_understanding_after_stop',
                'business_review_and_event_delivery', 'speech_dispatch',
                'tts_first_pcm', 'pcm_to_browser_observation')
    invariants = []
    for sample in valid:
        parts = sample.get('stages_ms', {})
        complete = all(k in parts for k in additive)
        error = abs(sum(parts[k] for k in additive)-sample['effective_answer_ms']) if complete else None
        reviewed = [t for t in sample.get('server', {}).get('traces', [])
                    if t['kind']=='reviewed_segment_received' and t.get('generation_id')==sample.get('generation_id')]
        invariants.append({'call':sample['call'], 'case':sample['case'],
            'six_stages_present':complete, 'telescoping_error_ms':round(error, 5) if error is not None else None,
            'reviewed_text_matches':len(reviewed)==1 and reviewed[0].get('text')==sample.get('answer_text'),
            'zero_business_effects':sample.get('server', {}).get('effects')==0})
    calls_without_samples = [c['index'] for c in report['calls'] if not c.get('samples')]
    result = {
        'metadata': report['metadata'],
        'counts': {'calls': len(report['calls']), 'attempted_turns': len(samples), 'valid_turns': len(valid),
            'invalid_or_failed_turns':len(samples)-len(valid), 'calls_without_turn_samples':calls_without_samples,
            'statuses':dict(Counter(s['status'] for s in samples))},
        'valid_effective_answer':distribution([s['effective_answer_ms'] for s in valid]),
        'all_observed_answer_audio':distribution([s['effective_answer_ms'] for s in all_audio]),
        'per_case':{case:distribution([s['effective_answer_ms'] for s in valid if s['case']==case])
                    for case in sorted({s['case'] for s in samples})},
        'stages':{name:distribution([s['stages_ms'].get(name) for s in valid]) for name in stage_names},
        'clock_sync_rtt':distribution([s.get('clock_sync',{}).get('rtt_ms') for s in samples]),
        'server_clock': {'implementation':time.get_clock_info('perf_counter' if report.get('metadata',{}).get('provider',{}).get('server_clock')=='perf_counter_ns' else 'monotonic').implementation,
                         'resolution_ms':time.get_clock_info('perf_counter' if report.get('metadata',{}).get('provider',{}).get('server_clock')=='perf_counter_ns' else 'monotonic').resolution*1000},
        'under_2000ms_valid_count':sum(s['effective_answer_ms']<2000 for s in valid),
        'under_2000ms_all_attempt_rate':sum(s['effective_answer_ms']<2000 for s in valid)/len(samples) if samples else None,
        'enough_samples':len(valid)>=30,
        'absolute_p50_under_2000ms':False,
        'full_latency_gate_passed':False,
        'comparison_baseline':None,
        'business_closure_accepted':False,
        'measurement_invariants':{'all_passed':all(x['six_stages_present'] and x['telescoping_error_ms']<.02
            and x['reviewed_text_matches'] and x['zero_business_effects'] for x in invariants),
            'valid_samples_checked':len(invariants), 'samples':invariants},
        'failures':[{'call':s['call'],'case':s['case'],'status':s['status'],'failure':s.get('failure'),
            'provider_errors':[t for t in s.get('server',{}).get('traces',[]) if t.get('event')=='error']}
            for s in samples if s['status']!='success'],
        'notes':[
            'Actual shipped browser/aiortc, native understanding, business guard and Qwen TTS; local gateway with real cloud WAN.',
            'Cross-process stages use minimum-RTT clock synchronization; RTT/2 is a network offset estimate, not a hard error bound.',
            'Endpoint stage includes input transport, provider VAD and event return; provider internals cannot be separated.',
            'Remote decoded PCM observed in browser is not a physical loudspeaker or trusted playback-completion proof.',
            'A single candidate run cannot establish same-condition improvement or the relative P95/failure gate.',
            'Stage P50 values must not be added to infer the end-to-end P50.'
            ,'Server timing uses Windows GetTickCount64 in this run (15.625ms resolution); zero-length short stages mean below clock resolution, not zero work.'
        ]}
    p50 = result['valid_effective_answer']['p50_ms']
    # An attempt without a valid answer is a failed deadline, never zero latency.
    completed = [s for s in samples if s['status'] != 'pending']
    ordered = sorted(s['effective_answer_ms'] for s in valid)
    def all_attempt_quantile(p):
        rank = math.ceil(len(completed)*p)
        return round(ordered[rank-1], 2) if rank and rank <= len(ordered) else None
    result['all_attempt_deadline_distribution'] = {
        'n':len(completed), 'failures_counted_as_no_valid_answer':len(completed)-len(valid),
        'p50_ms':all_attempt_quantile(.5), 'p95_ms':all_attempt_quantile(.95),
        'null_quantile_means_no_valid_answer_at_that_rank':True}
    usage_seen = set()
    usage_totals = Counter()
    latest_budget = {}
    for call in report['calls']:
        state = call.get('final') or next((samples[i].get('server', {}) for i in reversed(call.get('samples', [])) if samples[i].get('server')), {})
        latest_budget = state.get('budget') or latest_budget
        for event in state.get('traces', []):
            identity = event.get('response_id')
            if event.get('event') == 'response.done' and event.get('usage') and identity not in usage_seen:
                usage_seen.add(identity)
                for name in ('total_tokens', 'input_tokens', 'output_tokens'):
                    usage_totals[name] += event['usage'].get(name, 0)
    result['usage'] = {'native_responses_with_usage':len(usage_seen), 'native_tokens':dict(usage_totals),
                       'attempt_reservation_ledger':latest_budget,
                       'price_or_account_balance_estimated':False}
    result['absolute_p50_under_2000ms'] = len(valid)>=30 and p50 is not None and p50<2000
    return result

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('input',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    report=json.loads(args.input.read_text(encoding='utf-8'))
    result=summarize(report)
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('counts','valid_effective_answer','stages','enough_samples','absolute_p50_under_2000ms')},ensure_ascii=True))

if __name__=='__main__':
    main()
