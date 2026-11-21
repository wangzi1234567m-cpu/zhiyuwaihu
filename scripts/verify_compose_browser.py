"""Browser smoke against a fresh _test DB, then restore the local demo backend.
Temporarily recreates this project's backend; never clears the demo database.
Run after docker compose up --build -d. Local synthetic use only.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import quote
from uuid import uuid4
import yaml
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]


def run(args,**kwargs):
    return subprocess.run(args,cwd=ROOT,check=True,timeout=120,**kwargs)


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    values=dotenv_values(ROOT/'.env')
    database='zhiyu_browser_'+uuid4().hex[:8]+'_test'
    assert database.startswith('zhiyu_browser_') and database.endswith('_test')
    run(['docker','compose','exec','-T','db','createdb','-U','zhiyu',database])
    cache=ROOT/'.cache';cache.mkdir(exist_ok=True)
    overlay=cache/'compose-browser-test.yaml'
    password=quote(values.get('POSTGRES_PASSWORD') or 'local-dev-only',safe='')
    overlay.write_text(yaml.safe_dump({'services':{'backend':{'environment':{
        'DATABASE_URL':'postgresql+psycopg://zhiyu:'+password+'@db:5432/'+database,
        'APP_ENV':'test','MODEL_PROVIDER':'fake','TTS_PROVIDER':'manual','VOICE_ENABLED':'false',
        'CLOUD_CALLS_ENABLED':'false','FREE_QUOTA_CONFIRMED':'false'}}}}),encoding='utf-8')
    try:
        run(['docker','compose','-f','compose.yaml','-f',str(overlay),'up','--no-build','-d','--wait','backend'])
        env=os.environ.copy()
        env.update(PLAYWRIGHT_BROWSERS_PATH=str(cache/'pw-browsers'),
            DEMO_PASSWORD=values.get('DEMO_PASSWORD') or 'demo-local-only',E2E_URL='http://localhost:3000')
        result=subprocess.run(['node','node_modules/@playwright/test/cli.js','test'],cwd=ROOT/'frontend',env=env,
            capture_output=True,text=True,encoding='utf-8',timeout=120)
        (cache/'tts-browser-flow.log').write_text(result.stdout+result.stderr,encoding='utf-8')
        print(result.stdout+result.stderr)
        if result.returncode:raise RuntimeError('Browser test failed; see .cache/tts-browser-flow.log')
        sql="""SELECT json_build_object('sessions',(SELECT count(*) FROM sessions),
            'closed',(SELECT count(*) FROM sessions WHERE state='CLOSED'),
            'operations',(SELECT count(*) FROM operations),
            'effects',(SELECT count(*) FROM business_effects),'records',(SELECT count(*) FROM call_records));"""
        data=run(['docker','compose','exec','-T','db','psql','-U','zhiyu','-d',database,'-At','-c',sql],
            capture_output=True,text=True,encoding='utf-8')
        facts=json.loads(data.stdout)
        assert all(value==1 for value in facts.values()),facts
        (cache/'tts-browser-evidence.json').write_text(json.dumps({'test_database':database,'facts':facts},indent=2),encoding='utf-8')
        print('PASS: fresh test database has one completed operation/effect/writeback.')
    finally:
        # Restore the original DATABASE_URL and switches even when the browser fails.
        run(['docker','compose','up','--no-build','-d','--wait','backend'])
        print('Original demo backend restored. Test database retained as evidence.')


if __name__=='__main__':main()
