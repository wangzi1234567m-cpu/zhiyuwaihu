"""Browser acceptance against an already migrated, seeded, isolated _test database.

Does not reset data or stop existing services. Run once against a fresh database.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]

def main():
    sys.stdout.reconfigure(encoding='utf-8')
    env=os.environ.copy()
    url=env.get('DATABASE_URL','')
    if not url.endswith('_test'):
        raise SystemExit('Use a dedicated, fresh _test database.')
    env.update(PYTHONPATH=str(ROOT/'backend'),APP_ENV='test',PUBLIC_ORIGIN='http://localhost:3000',
               PYTHONIOENCODING='utf-8',MODEL_PROVIDER='fake',VOICE_ENABLED='false',
               REALTIME_WEBRTC_ENABLED='false',CLOUD_CALLS_ENABLED='false',VOICE_WRITES_ENABLED='false')
    for port in [8000,3000]:
        with socket.socket() as sock:
            try:sock.bind(('127.0.0.1',port))
            except OSError:raise SystemExit(f'Port {port} in use; no existing service was stopped.')
    cache=ROOT/'.cache/workbench'
    cache.mkdir(parents=True,exist_ok=True)
    processes=[];logs=[]
    try:
        commands=[('backend',[sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000','--no-access-log'],ROOT),
                  ('frontend',[shutil.which('node'),'node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','3000'],ROOT/'frontend')]
        for name,args,cwd in commands:
            log=(cache/f'{name}.log').open('w',encoding='utf-8');logs.append(log)
            processes.append(subprocess.Popen(args,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,
                                             creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0))
        for target in ['http://127.0.0.1:8000/health/ready','http://localhost:3000']:
            for _ in range(80):
                if any(p.poll() is not None for p in processes):raise RuntimeError('Service exited; inspect .cache/workbench logs')
                try:
                    with urllib.request.urlopen(target,timeout=2) as result:
                        if result.status==200:break
                except OSError:time.sleep(.25)
            else:raise RuntimeError('Service not ready')
        result=subprocess.run([shutil.which('node'),'node_modules/@playwright/test/cli.js','test'],cwd=ROOT/'frontend',env=env,
                              capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=240)
        (cache/'browser-result.txt').write_text(result.stdout+result.stderr,encoding='utf-8')
        print(result.stdout,result.stderr,flush=True)
        if result.returncode:raise SystemExit(result.returncode)
        import psycopg
        with psycopg.connect(url.replace('postgresql+psycopg://','postgresql://')) as db:
            states=db.execute("select application_id,state,data->>'verified' from sessions order by application_id").fetchall()
            counts={table:db.execute(f'select count(*) from {table}').fetchone()[0] for table in ['sessions','operations','business_effects','call_records']}
            assert counts=={'sessions':7,'operations':3,'business_effects':3,'call_records':3},counts
            assert sum(state=='CLOSED' and verified=='true' for _,state,verified in states)==3
            assert db.execute("select count(*) from operations where status='succeeded'").fetchone()[0]==3
            assert db.execute('select count(*) from (select session_id,seq,row_number() over(partition by session_id order by seq) expected from events) e where seq<>expected').fetchone()[0]==0
            evidence={'counts':counts,'closed_authorization_cases':3,'direct_jingmai':'backend route verified; execution blocked by missing API',
                      'unanswered':'three backend rule-preview outcomes verified; provider no-answer ingress unavailable',
                      'event_sequence_contiguous':True,'runtime':'FakeModel + MockBusiness + isolated PostgreSQL',
                      'real_cloud_latency_measured':False}
        (ROOT/'docs/workbench-test-results.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print('PASS persisted database facts:',counts)
    finally:
        for process in reversed(processes):
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        for log in logs:log.close()

if __name__=='__main__':main()
