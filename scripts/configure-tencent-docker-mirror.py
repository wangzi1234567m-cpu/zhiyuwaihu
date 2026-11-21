"""Configure Tencent's official internal Docker Hub mirror on the inspected cloud host.
Preserves unrelated daemon settings; validates a candidate before atomic replacement.
Does not restart Docker or pull images. Run as root on the Ubuntu server.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.error import HTTPError, URLError
from urllib.request import urlopen
from uuid import uuid4

MIRROR='https://mirror.ccs.tencentyun.com'


def merged_config(original):
    if not isinstance(original,dict):raise ValueError('Docker configuration must be a JSON object.')
    mirrors=original.get('registry-mirrors',[])
    if not isinstance(mirrors,list) or any(not isinstance(item,str) for item in mirrors):
        raise ValueError('Existing registry-mirrors must be a list of strings.')
    return {**original,'registry-mirrors':[MIRROR,*[item for item in mirrors if item.rstrip('/')!=MIRROR]]}


def main():
    if os.name!='posix' or os.geteuid()!=0:raise SystemExit('Run on the Tencent Ubuntu server with sudo python3.')
    # Check trusted TLS connectivity to the documented internal mirror before changes.
    try:
        with urlopen(MIRROR+'/v2/',timeout=15) as response:status=response.status
    except HTTPError as error:status=error.code
    except (URLError,TimeoutError):raise SystemExit('Mirror connection failed. Configuration unchanged; check cloud network/DNS first.')
    if status not in (200,401):raise SystemExit(f'Mirror returned HTTP {status}. Configuration unchanged.')
    print(f'Mirror TLS/network check: HTTP {status}. Image pull still needs verification.')
    target=Path('/etc/docker/daemon.json')
    if target.is_symlink():raise SystemExit('daemon.json is a symlink; review its management before changing it.')
    original_bytes=target.read_bytes() if target.exists() else None
    try:original=json.loads(original_bytes) if original_bytes is not None else {}
    except ValueError:raise SystemExit('Existing daemon.json is not valid JSON. No changes made.')
    try:updated=merged_config(original)
    except ValueError as error:raise SystemExit(str(error))
    if updated==original:
        print('Tencent mirror already configured. Inspect docker info and pull result.')
        return
    target.parent.mkdir(parents=True,exist_ok=True)
    candidate=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',prefix='.zhiyu-daemon-',suffix='.json',dir=target.parent,delete=False) as file:
            candidate=Path(file.name)
            json.dump(updated,file,indent=2);file.write('\n')
        result=subprocess.run(['dockerd','--validate','--config-file',str(candidate)],capture_output=True,text=True)
        if result.returncode:raise SystemExit('Docker rejected candidate configuration. Original unchanged; inspect locally without sharing secrets.')
        current=target.read_bytes() if target.exists() else None
        if current!=original_bytes:raise SystemExit('Configuration changed during validation. Original retained; review before retrying.')
        if original_bytes is not None:
            backup=target.with_name('daemon.json.zhiyu-backup-'+uuid4().hex)
            shutil.copy2(target,backup);backup.chmod(0o600)
            print('Previous configuration backed up:',backup)
        os.replace(candidate,target)
        print('Mirror configuration saved. Unrelated settings retained. Docker not restarted yet.')
    finally:
        if candidate and candidate.exists():candidate.unlink()


if __name__=='__main__':main()
