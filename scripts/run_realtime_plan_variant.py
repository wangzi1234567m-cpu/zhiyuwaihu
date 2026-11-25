"""One user-authorized bounded variant. All variants share one unchanged budget ledger."""
import argparse,json,os,secrets,socket,subprocess,sys,time,urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
WORKSPACE=ROOT.parents[1] if ROOT.parent.name=='tmp' else ROOT

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--phase',required=True,choices=['baseline','semantic','semantic400','natural','barge','diagnostic','final','segmented','natural400','natural300','segmentedcontrol','server300','qwenws','bargefixed','segmentedws','bargefixed2','demo'])
    parser.add_argument('--vad',type=int,required=True,choices=[250,300,400])
    parser.add_argument('--calls',type=int,default=1,choices=range(1,13))
    parser.add_argument('--per-case',type=int,default=3,choices=[0,3,10])
    parser.add_argument('--plan')
    parser.add_argument('--ui-recording',action='store_true')
    parser.add_argument('--mode',choices=['server_vad','semantic_vad_ec'],default='server_vad')
    parser.add_argument('--speech',choices=['qwen_http','qwen_ws','cosyvoice_ws'],default='qwen_http')
    parser.add_argument('--turns',type=int,choices=range(1,9),default=3)
    args=parser.parse_args()
    if os.environ.get('LATENCY_PLAN_AUTHORIZED')!='20261125-26-130-200':raise SystemExit('New approved plan envelope required')
    if not os.environ.get('DATABASE_URL','').endswith('_test'):raise SystemExit('Explicit dedicated _test DB required')
    cache=ROOT/'.cache/ddl-plan';cache.mkdir(parents=True,exist_ok=True)
    control=cache/'control.json'
    if not control.exists():control.write_text(json.dumps({'token':secrets.token_urlsafe(32)}),encoding='utf-8')
    token=json.loads(control.read_text(encoding='utf-8'))['token']
    run='v04-latency-plan-20261125-'+args.phase
    env=os.environ.copy();env.pop('LATENCY_OPTIMIZATION_AUTHORIZED',None);env.update(PYTHONPATH=str(ROOT/'backend'),APP_ENV='test',PUBLIC_ORIGIN='http://127.0.0.1:18765',
      LATENCY_PLAN_AUTHORIZED='20261125-26-130-200',LATENCY_RUN_ID=run,LATENCY_CONTROL_TOKEN=token,
      LATENCY_WORKSPACE=str(WORKSPACE),LATENCY_VARIANT='opt-vad-'+str(args.vad),LATENCY_CALLS=str(args.calls),
      LATENCY_TARGET_PER_CASE=str(args.per_case),LATENCY_TARGET_VALID=str(len(json.loads(Path(args.plan).read_text(encoding='utf-8')))) if args.plan else str(args.per_case*3),
      REALTIME_VAD_MODE=args.mode,REALTIME_SPEECH_BACKEND=args.speech,REALTIME_VAD_SILENCE_MS=str(args.vad),REALTIME_VAD_THRESHOLD='0.5',REALTIME_DIAGNOSTICS_ENABLED='true',
      DEMO_PASSWORD='local-latency-only',REALTIME_WEBRTC_ENABLED='true',REALTIME_APPLICATION_IDS='00000000-0000-0000-0000-000000000001',
      REALTIME_STUN_URLS='[]',REALTIME_TURN_URLS='',VOICE_ENABLED='false',VOICE_WRITES_ENABLED='false',MODEL_PROVIDER='fake',
      TTS_PROVIDER='qwen',CLOUD_CALLS_ENABLED='true',FREE_QUOTA_CONFIRMED='true',MAX_DAILY_NATIVE_SESSIONS='26',
      MAX_DAILY_NATIVE_TURNS=str(args.turns),MAX_DAILY_TTS_CALLS='200',ZHIYU_KNOWLEDGE_VERSION='workbench-reviewed-20261125-v3',
      PLAYWRIGHT_BROWSERS_PATH=str(WORKSPACE/'.cache/pw-browsers'),PYTHONIOENCODING='utf-8')
    if args.ui_recording:env.update(LATENCY_WORKBENCH_UI='true',E2E_URL='http://127.0.0.1:18765')
    if args.plan:env['LATENCY_AUDIO_PLAN']=str(Path(args.plan).resolve())
    else:env.pop('LATENCY_AUDIO_PLAN',None)
    with socket.socket() as sock:
      try:sock.bind(('127.0.0.1',18765))
      except OSError:raise SystemExit('18765 occupied; no existing process stopped')
    client=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    code="import sys;sys.path.insert(0,'scripts');import uvicorn;from measure_realtime_app import app;server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18765,access_log=False,timeout_graceful_shutdown=5));app.state.measurement_server=server;server.run()"
    with (cache/(args.phase+'.server.log')).open('a',encoding='utf-8') as log:
      server=subprocess.Popen([sys.executable,'-c',code],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
      try:
        for _ in range(80):
          if server.poll() is not None:raise RuntimeError('Measurement host exited; inspect private log')
          try:
            with client.open('http://127.0.0.1:18765/health/ready',timeout=1) as r:
              if r.status==200:break
          except OSError:time.sleep(.25)
        else:raise RuntimeError('Measurement host not ready')
        with (cache/(args.phase+'.browser.log')).open('a',encoding='utf-8') as out:
          command=['node','scripts/record_final_demo.cjs','--run-real-demo'] if args.ui_recording else ['node','scripts/measure_realtime_browser.cjs','--run-real-latency']
          result=subprocess.run(command,cwd=ROOT,env=env,stdout=out,stderr=subprocess.STDOUT,timeout=22*60+30)
        print(json.dumps({'phase':args.phase,'vad_ms':args.vad,'client_exit':result.returncode,'report':'.cache/ddl-plan/final-demo.json' if args.ui_recording else '.cache/'+run+'.json'}),flush=True)
        if result.returncode:raise SystemExit(result.returncode)
      finally:
        try:
          req=urllib.request.Request('http://127.0.0.1:18765/eval/stop',data=b'',headers={'X-Latency-Token':token,'Origin':'http://127.0.0.1:18765','X-Requested-With':'zhiyu','Content-Type':'application/json'})
          client.open(req,timeout=3).close()
        except OSError:pass
        try:server.wait(timeout=12)
        except subprocess.TimeoutExpired:
          raise RuntimeError('Owned test server has not stopped; no unrelated process killed')

if __name__=='__main__':main()
