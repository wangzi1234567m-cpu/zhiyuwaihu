"""Seal reviewed source and local test evidence, never credentials or dependencies."""
import argparse,hashlib,json,subprocess,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--include-private-knowledge',action='store_true');parser.add_argument('--replace',action='store_true');args=parser.parse_args()
    destination=ROOT/'.cache/delivery/zhiyu-final-20261125.zip'
    if destination.exists() and not args.replace:raise SystemExit('Existing sealed package retained; use --replace explicitly')
    files=set(filter(None,subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode('utf-8').split('\0')))
    files={p for p in files if not p.startswith('docs/archive/')}
    if args.include_private_knowledge:
        files.update(['private-evidence/business/staging.json','private-evidence/business/reviewed-release-v3-final.json'])
    for report in sorted((ROOT/'.cache').glob('v04-latency-plan-20261125-*.json')):
        files.add(report.relative_to(ROOT).as_posix())
        raw=json.loads(report.read_text(encoding='utf-8'))
        for call in raw.get('calls',[]):
            if call.get('diagnostics_ref'):files.add(call['diagnostics_ref']['path'])
    for name in ('tts-candidates.json','tts-short-control.json','final-freeze.json','final-demo.json','final-diagnostics.json'):
        evidence=ROOT/'.cache/ddl-plan'/name
        if evidence.exists():files.add(evidence.relative_to(ROOT).as_posix())
    for audio in (ROOT/'.cache/ddl-plan').glob('*.wav'):files.add(audio.relative_to(ROOT).as_posix())
    for log in ('regression-final.log','browser-regression.log','typecheck.log','build.log'):
        path=ROOT/'.cache/ddl-plan'/log
        if path.exists():files.add(path.relative_to(ROOT).as_posix())
    for directory in (ROOT/'.cache/ddl-plan').glob('demo-attempt-*'):
        for artifact in directory.rglob('*'):
            if artifact.is_file():files.add(artifact.relative_to(ROOT).as_posix())
    files={p for p in files if (ROOT/p).is_file()}
    for name in files:
        path=(ROOT/name).resolve()
        if not path.is_relative_to(ROOT.resolve()):raise ValueError('Source outside checkout')
        if any(part in ('.git','node_modules','.venv','.next') for part in Path(name).parts):raise ValueError('Dependency/runtime directory in package')
        if Path(name).suffix.lower() in ('.pem','.key','.pfx','.p12','.dump','.sqlite','.sqlite3','.db'):raise ValueError('Credential/database binary excluded')
        if Path(name).name.startswith('.env') and name!='.env.example':raise ValueError('Credential file excluded')
    workspace=ROOT.parents[1] if ROOT.parent.name=='tmp' else ROOT
    extras={}
    for name in ('merchant-1.trimmed.wav','merchant-2.trimmed.wav','merchant-source.trimmed.wav'):
        source=workspace/'.cache/round1-audio'/name
        if source.is_file():extras['.cache/round1-audio/'+name]=source
    old=ROOT.parent/'v04-latency/.cache/v04-latency-20261124-b.json'
    if old.is_file():extras['.cache/evidence/latency-old-20261124.json']=old
    manifest={'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'private_knowledge_included':args.include_private_knowledge,'credentials_included':False,'files':{}}
    destination.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name in sorted(files):
            data=(ROOT/name).read_bytes();manifest['files'][name]={'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)}
            archive.writestr('zhiyu-final/'+name,data)
        for name,path in sorted(extras.items()):
            data=path.read_bytes();manifest['files'][name]={'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data)};archive.writestr('zhiyu-final/'+name,data)
        archive.writestr('zhiyu-final/DELIVERY-MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:raise ValueError('ZIP CRC verification failed')
        for name,record in manifest['files'].items():
            if hashlib.sha256(archive.read('zhiyu-final/'+name)).hexdigest()!=record['sha256']:raise ValueError('Packaged hash mismatch')
    digest=hashlib.sha256(destination.read_bytes()).hexdigest()
    destination.with_suffix('.zip.sha256').write_text(digest+'  '+destination.name+'\n',encoding='ascii')
    print(json.dumps({'output':str(destination),'files':len(manifest['files']),'bytes':destination.stat().st_size,'sha256':digest,'private_team_package':args.include_private_knowledge}))

if __name__=='__main__':main()