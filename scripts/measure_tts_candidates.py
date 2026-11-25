"""Controlled approved-text TTS matrix; PCM onset is NOT browser end-to-end latency."""
import argparse,asyncio,json,os,sys,time,hashlib,wave
from pathlib import Path
from dataclasses import replace
from array import array
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))

async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-real-tts',action='store_true');parser.add_argument('--repeats',type=int,choices=[1,5],default=5);parser.add_argument('--short-text-control',action='store_true');parser.add_argument('--resume',action='store_true');args=parser.parse_args()
    from realtime_plan_budget import require_authorization,reserve
    require_authorization()
    if not args.run_real_tts:raise SystemExit('Explicit real TTS flag required')
    from dotenv import load_dotenv
    load_dotenv(ROOT.parents[1]/'.env',override=False)
    from app.config import settings
    from app.realtime.speech import create_speech,speech_profile
    folder=ROOT/'.cache/ddl-plan';folder.mkdir(parents=True,exist_ok=True)
    path=folder/('tts-short-control.json' if args.short_text_control else 'tts-candidates.json')
    if path.exists() and not args.resume:raise SystemExit('Existing experiment retained; explicit --resume required')
    source=ROOT.parent/'v04-latency/.cache/v04-latency-20261124-b.json' if args.short_text_control else ROOT/'.cache/v04-latency-opt-20261125-ab400.json'
    if not source.exists():source=ROOT/'.cache/evidence/latency-old-20261124.json' if args.short_text_control else ROOT/'.cache/v04-latency-plan-20261125-final.json'
    raw=json.loads(source.read_text(encoding='utf-8'));texts={s['case']:s['answer_text'] for s in raw['samples'] if s['status']=='success'}
    result={'component_only':True,'native_connections':0,'repeats':args.repeats,'samples':[],'profiles':{},'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()}
    if path.exists():
        previous=json.loads(path.read_text(encoding='utf-8'))
        if previous['source_sha256']!=result['source_sha256'] or previous['repeats']!=args.repeats:raise SystemExit('Frozen TTS input mismatch')
        result=previous
    def save():path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    backends=['qwen_http'] if args.short_text_control else ['qwen_http','qwen_ws','cosyvoice_ws']
    for backend in backends:
        c=replace(settings,realtime_speech_backend=backend,realtime_diagnostics_enabled=True,realtime_tts_drain_enabled=True)
        result['profiles'][backend]=speech_profile(c)
        failed=False
        for case in ['identity','guarantee','source']:
            speech=create_speech(c,reserve)
            try:
                for n in range(args.repeats):
                    if any(s['backend']==backend and s['case']==case and s['within_connection']==n+1 for s in result['samples']):continue
                    row={'backend':backend,'case':case,'within_connection':n+1,'status':'pending','text_sha256':hashlib.sha256(texts[case].encode()).hexdigest(),'text_chars':len(texts[case])}
                    result['samples'].append(row);save();start=time.perf_counter_ns();first=None;voiced=None;pcm=bytearray();before=len(speech.tts.probe.events)
                    try:
                        async for chunk in speech.stream(texts[case],asyncio.Event()):
                            at=time.perf_counter_ns()
                            if first is None:first=at
                            pcm.extend(chunk)
                            values=array('h',chunk)
                            if voiced is None and (sum(v*v for v in values)/len(values))**.5/32768>.004:voiced=at
                        if first is None or voiced is None:raise ValueError('No audible PCM')
                        row.update(status='success',first_pcm_ms=(first-start)/1e6,first_voiced_pcm_ms=(voiced-start)/1e6,total_ms=(time.perf_counter_ns()-start)/1e6,pcm_bytes=len(pcm))
                        audio=folder/(backend+'-'+case+'-'+str(n+1)+('-short' if args.short_text_control else '')+'.wav')
                        with wave.open(str(audio),'wb') as wav:wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(pcm)
                        row['audio']=str(audio.relative_to(ROOT)).replace('\\','/')
                    except Exception as error:
                        row.update(status='failed',code=getattr(error,'code',type(error).__name__));failed=True
                    row['events']=list(speech.tts.probe.events)[before:];save();print(json.dumps({k:row.get(k) for k in ['backend','case','within_connection','status','code','first_pcm_ms','first_voiced_pcm_ms']}),flush=True)
                    if failed and row.get('code') in ('TTS_AUTH_FAILED','TTS_PERMISSION_DENIED','TTS_QUOTA_EXHAUSTED'):break
                    failed=False
            finally:await speech.close()
            if failed:break
    save()

if __name__=='__main__':asyncio.run(main())
