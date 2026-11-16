"""Start local HTTP services against an existing dedicated _test database, run browser flow,
then verify persisted facts. Requires npm build and Playwright Chromium installation.
Resets ONLY the designated test database; never starts/stops an existing user service.
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
    os.chdir(ROOT)
    env=os.environ.copy()
    if not env.get('DATABASE_URL'):
        raise SystemExit('Set DATABASE_URL to a dedicated PostgreSQL _test database first.')
    env.update(PYTHONPATH=str(ROOT/'backend'),APP_ENV='test',PUBLIC_ORIGIN='http://localhost:3000',PYTHONIOENCODING='utf-8')
    for port in [8000,3000]:
        with socket.socket() as sock:
            try:sock.bind(('127.0.0.1',port))
            except OSError:raise SystemExit(f'Port {port} is in use; stop your existing dev service first.')
    subprocess.run([sys.executable,'scripts/reset_test_db.py'],env=env,check=True)
    cache=ROOT/'.cache'
    cache.mkdir(exist_ok=True)
    node=shutil.which('node')
    if not node:raise SystemExit('Node.js is required')
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    logs=[]
    processes=[]
    try:
        commands=[('backend',[sys.executable,'-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000','--no-access-log'],ROOT),
                  ('frontend',[node,'node_modules/next/dist/bin/next','start','--hostname','127.0.0.1','--port','3000'],ROOT/'frontend')]
        for name,args,cwd in commands:
            log=(cache/f'flow-{name}.log').open('w',encoding='utf-8');logs.append(log)
            processes.append(subprocess.Popen(args,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=flags))
        for url in ['http://127.0.0.1:8000/health/ready','http://localhost:3000']:
            for _ in range(60):
                if any(p.poll() is not None for p in processes):raise RuntimeError('Service exited; inspect .cache/flow-*.log')
                try:
                    with urllib.request.urlopen(url,timeout=2) as r:
                        if r.status==200:break
                except OSError:time.sleep(.25)
            else:raise RuntimeError('Service not ready: '+url)
        result=subprocess.run([node,'node_modules/@playwright/test/cli.js','test'],cwd=ROOT/'frontend',env=env,
            capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
        (cache/'browser-flow-result.txt').write_text(result.stdout+result.stderr,encoding='utf-8')
        print(result.stdout,result.stderr,flush=True)
        if result.returncode:raise SystemExit(result.returncode)
        import psycopg
        with psycopg.connect(env['DATABASE_URL'].replace('postgresql+psycopg://','postgresql://'),connect_timeout=3) as db:
            session=db.execute('select id,state,data from sessions').fetchone()
            assert session and session[1]=='CLOSED' and session[2]['verified']
            counts={table:db.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in ['sessions','messages','events','operations','business_effects','call_records']}
            assert counts['sessions']==1 and counts['messages']==7
            assert counts['operations']==counts['business_effects']==counts['call_records']==1
            assert db.execute('select status from operations').fetchone()[0]=='succeeded'
            seqs=[row[0] for row in db.execute('select seq from events order by seq')]
            assert seqs==list(range(1,len(seqs)+1))
            version=db.execute('select version()').fetchone()[0]
            evidence={'session_id':session[0],'state':session[1],'verified':True,'counts':counts,
                'operation_status':'succeeded','event_sequence_contiguous':True,'postgres':version,
                'agent':'DemoAgent','model':'FakeModel','business':'MockBusiness',
                'browser_flow':'login/start/3 turns/writeback/reload passed'}
        (ROOT/'docs/flow-evidence.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print('PASS: database facts match browser flow:',counts)
    finally:
        for process in reversed(processes):
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
        for log in logs:log.close()

if __name__=='__main__':main()
