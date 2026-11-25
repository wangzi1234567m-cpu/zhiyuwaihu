"""Local-only diagnostic attribution; never infer provider internals from socket send."""
import argparse,hashlib,json,math,statistics
from pathlib import Path
from summarize_realtime_measurement import decompose,distribution


def numeric(values):return [x for x in values if isinstance(x,(int,float)) and math.isfinite(x)]
def ns_distribution(values):return distribution([v/1e6 for v in numeric(values)])


def inspect(sample):
    state=sample.get('server',{});diag=state.get('diagnostics') or {}
    offset=sample.get('clock_sync',{}).get('offset_ms')
    inp=sample.get('input') or {}
    if offset is None or not inp:return {}
    start,end=inp['start_ms']+offset,inp['speech_end_ms']+offset
    traces=[t for t in state.get('traces',[]) if t['at_ms']>=end-200]
    stop=next((t for t in traces if t['kind']=='provider_event' and t.get('event')=='input_audio_buffer.speech_stopped'),None)
    until=stop['at_ms']+100 if stop else end+8000
    def events(key):return [e for e in (diag.get(key) or {}).get('events',[]) if start-100<=e['at_ns']/1e6<=until]
    frames=[e for e in events('session') if e['event']=='input_frame']
    voiced=[e for e in frames if e.get('rms',0)>.004 and e.get('queued_at_ns',0)/1e6<=until]
    ws=[e for e in events('provider') if e['event']=='websocket_send' and e.get('kind')=='input_audio_buffer.append']
    result={'gateway_pings':[t for t in state.get('traces',[]) if t.get('kind')=='gateway_ping'],
      'input_frames':len(frames),'input_queue_wait':ns_distribution([e.get('queue_wait_ns') for e in frames]),
      'input_receive_gap':ns_distribution([e.get('received_gap_ns') for e in frames]),
      'input_send_gap':ns_distribution([e.get('send_gap_ns') for e in frames]),
      'websocket_send':ns_distribution([e.get('send_ns') for e in ws]),
      'websocket_lock_wait':ns_distribution([e.get('lock_wait_ns') for e in ws]),
      'probe_dropped':{key:value.get('dropped') for key,value in diag.items() if isinstance(value,dict)}}
    if voiced and stop:
      last=voiced[-1]
      result['server_last_voiced_sequence']=last['sequence']
      result['server_received_last_voice_to_stopped_ms']=round(stop['at_ms']-last['queued_at_ns']/1e6,3)
      result['server_sent_last_voice_to_stopped_ms']=round(stop['at_ms']-last['at_ns']/1e6,3)
      result['last_voiced_receive_relative_to_calibrated_browser_end_ms']=round(last['queued_at_ns']/1e6-end,3)
      result['provider_audio_end_minus_sent_content_end_ms']=round(stop.get('audio_end_ms',0)-last['sequence']*20,3)
    text=sample.get('answer_text','');sha=hashlib.sha256(text.encode()).hexdigest()
    tts=(diag.get('tts') or {}).get('events',[])
    req=next((e for e in tts if e['event']=='tts_request_start' and e.get('text_sha256')==sha and e['at_ns']/1e6>=end-200),None)
    if req:
      tt=[e for e in tts if e.get('request_id')==req['request_id']]
      def first(name):return next((e for e in tt if e['event']==name),None)
      head,block,pcm=first('tts_response_headers'),first('tts_first_audio_block'),first('tts_first_pcm')
      result['tts']={'text_chars':req['text_chars'],'connection_reused_observed':head.get('connection_reused_observed') if head else None,
        'request_to_headers_ms':round((head['at_ns']-req['at_ns'])/1e6,3) if head else None,
        'headers_to_first_audio_ms':round((block['at_ns']-head['at_ns'])/1e6,3) if head and block else None,
        'first_audio_to_pcm_ms':round((pcm['at_ns']-block['at_ns'])/1e6,3) if block and pcm else None,
        'http_events':[{'name':e['name'],'since_request_ms':round((e['at_ns']-req['at_ns'])/1e6,3)} for e in tt if e['event']=='tts_http_trace'],
        'errors':[e for e in tt if e['event']=='tts_error']}
    output=(diag.get('output') or {}).get('events',[])
    permit=next((n for n in sample.get('browser',{}).get('notices',[]) if isinstance(n,dict) and n.get('type')=='playback.permit' and n.get('generation_id')==sample.get('generation_id')),None)
    if permit:
      sent=[e for e in output if e['event']=='output_frame' and e.get('epoch')==permit['epoch']]
      puts={e['queued_at_ns']:e['producer_wait_ns'] for e in output if e['event']=='output_enqueue'}
      residence=[max(0,e['queue_wait_including_producer_ns']-puts.get(e['queued_at_ns'],0)) for e in sent]
      result['output']={'sent_frames':len(sent),'queue_residence':ns_distribution(residence),
        'producer_backpressure':ns_distribution([puts[e['queued_at_ns']] for e in sent if e['queued_at_ns'] in puts]),
        'first_frame_residence_ms':round(residence[0]/1e6,3) if residence else None,
        'schedule_lag':ns_distribution([e.get('schedule_lag_ns') for e in sent])}
    result['stages_ms']=decompose(sample)
    return result


def report(path):
    raw=json.loads(path.read_text(encoding='utf-8'));samples=raw['samples']
    for sample in samples:
        state=sample.get('server') or {};ref=state.get('diagnostics_ref')
        if ref:
            data=(Path(__file__).resolve().parents[1]/ref['path']).read_bytes()
            if hashlib.sha256(data).hexdigest()!=ref['sha256']:raise ValueError('Diagnostic sidecar hash mismatch')
            state['diagnostics']=json.loads(data)

    valid=[s for s in samples if s['status']=='success' and s.get('cohort',True)]
    rows=[{'call':s['call'],'case':s['case'],'status':s['status'],'cohort':s.get('cohort',True),'failure':s.get('failure'),
       'effective_answer_ms':s.get('effective_answer_ms'),'business_valid':s.get('business_valid'),
       'diagnostic':inspect(s)} for s in samples]
    failed_connections=[{'call':c['index'],'failure':c['failure'],'code_commit':c.get('code_commit'),
      'opening_diagnostics':((c.get('final') or {}).get('diagnostics') or {}).get('provider')}
      for c in raw['calls'] if c.get('failure') and not c.get('samples')]
    total_attempts=len(samples)+len(failed_connections)
    return {'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'metadata':raw['metadata'],
      'calls':len(raw['calls']),'attempts':total_attempts,'turn_attempts':len(samples),'failed_connections':failed_connections,'valid_cohort':len(valid),'all_successes':sum(s['status']=='success' for s in samples),
      'failure_rate':(sum(s['status']!='success' for s in samples)+len(failed_connections))/total_attempts if total_attempts else None,
      'effective_answer':distribution([s.get('effective_answer_ms') for s in valid]),
      'per_case':{key:distribution([s.get('effective_answer_ms') for s in valid if s['case']==key]) for key in sorted({s['case'] for s in samples})},
      'rows':rows,'limitations':['Small A/B samples are not formal acceptance.','Queue and WebSocket timings are local observations; completed send is not provider acknowledgement.','Cross-process stage boundaries use explicit minimum-RTT clock calibration; total answer latency stays in browser.performance.','First PCM of the reviewed answer segment is not word-aligned onset of a specific answer sentence.']}


def main():
    p=argparse.ArgumentParser();p.add_argument('inputs',nargs='+',type=Path);p.add_argument('--output',type=Path,required=True);args=p.parse_args()
    result={'variants':[report(path) for path in args.inputs]}
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps([{'run':v['metadata']['run_id'],'attempts':v['attempts'],'valid':v['valid_cohort'],'effective_answer':v['effective_answer']} for v in result['variants']]))

if __name__=='__main__':main()
