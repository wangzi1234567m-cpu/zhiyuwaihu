"""Approved-text-only TTS component A/B; never plays audio or reports RTC acceptance."""
import argparse,asyncio,hashlib,json,os,sys,time
from dataclasses import replace
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))

async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-real-tts',action='store_true');args=parser.parse_args()
    if not args.run_real_tts or os.environ.get('LATENCY_OPTIMIZATION_AUTHORIZED')!='20261125-20-62-100':
        raise SystemExit('Explicit authorized bounded TTS experiment required')
    if not os.environ.get('DATABASE_URL','').endswith('_test'):raise SystemExit('Dedicated shared experiment database required')
    from dotenv import load_dotenv
    load_dotenv(ROOT.parents[1]/'.env',override=False)
    from app.config import settings
    from app.cloud_budget import reserve_call
    from app.realtime.speech import StreamingSpeech
    baseline=json.loads((ROOT/'.cache/v04-latency-opt-20261125-ab400.json').read_text(encoding='utf-8'))
    texts={s['case']:s['answer_text'] for s in baseline['samples'] if s['status']=='success'}
    output=ROOT/'.cache/ddl-opt/tts-drain-ab.json'
    if output.exists():raise SystemExit('Existing component experiment retained; no implicit rerun')
    result={'component_only':True,'played_audio':False,'business_writes':False,'samples':[]}
    def save():output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    def reserve(kind,limit):reserve_call('v04-latency-opt-20261125:'+kind,100)
    for case,enabled in [('identity',False),('identity',True),('guarantee',True),('guarantee',False),('source',False),('source',True)]:
        config=replace(settings,realtime_tts_drain_enabled=enabled,realtime_diagnostics_enabled=True)
        speech=StreamingSpeech(config,reserve)
        try:
            for index in range(2):
                row={'case':case,'drain_enabled':enabled,'within_pair':index+1,'text_sha256':hashlib.sha256(texts[case].encode()).hexdigest(),'text_chars':len(texts[case]),'status':'pending'}
                result['samples'].append(row);save()
                start=time.perf_counter_ns();first=None;total=0;before=len(speech.tts.probe.events)
                try:
                    async for pcm in speech.stream(texts[case],asyncio.Event()):
                        if first is None:first=time.perf_counter_ns()
                        total+=len(pcm)
                    row.update(status='success',first_pcm_ms=(first-start)/1e6 if first else None,total_ms=(time.perf_counter_ns()-start)/1e6,pcm_bytes=total)
                except Exception as error:
                    row.update(status='failed',code=getattr(error,'code',type(error).__name__))
                row['events']=list(speech.tts.probe.events)[before:];save()
                print(json.dumps({k:row.get(k) for k in ['case','drain_enabled','within_pair','status','first_pcm_ms']}),flush=True)
        finally:await speech.close()
    save()

if __name__=='__main__':asyncio.run(main())
