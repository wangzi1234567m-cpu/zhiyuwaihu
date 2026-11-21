"""Inspect, or explicitly stop, nginx.service and Node listeners on TCP 3000.
Ubuntu server only. Does not uninstall software, delete data, or touch SSH.
"""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time


def listeners(port):
    result=subprocess.run(['ss','-H','-ltnp',f'sport = :{port}'],capture_output=True,text=True,check=True)
    return sorted({int(pid) for pid in re.findall(r'pid=(\d+)',result.stdout)})


def identity(pid):
    path=Path('/proc')/str(pid)
    try:
        exe=Path(os.readlink(path/'exe')).name
        start=(path/'stat').read_text().rsplit(')',1)[1].split()[19]
        units=sorted(set(re.findall(r'/([^/\n]+\.service)(?:/|$)',(path/'cgroup').read_text())))
        return {'pid':pid,'executable':exe,'start':start,'manager_units':units}
    except FileNotFoundError:
        return None


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--stop',action='store_true',help='Disable nginx.service and SIGTERM Node listeners on port 3000.')
    args=parser.parse_args()
    if os.name!='posix' or not Path('/proc').exists():raise SystemExit('Run on the Ubuntu server.')
    if os.geteuid()!=0:raise SystemExit('Run using sudo python3.')
    nodes=[info for pid in listeners(3000) if (info:=identity(pid))]
    print(json.dumps({'port_3000':nodes,'ports_80_443':{str(port):[identity(pid) for pid in listeners(port)] for port in (80,443)}},indent=2),flush=True)
    if not args.stop:
        print('Inspection only. Add --stop for the authorized shutdown.')
        return
    if any(info['executable'] not in ('node','nodejs') for info in nodes):
        raise SystemExit('Port 3000 has a non-Node owner. Nothing stopped; review first.')
    loaded=subprocess.run(['systemctl','show','nginx.service','--property=LoadState','--value'],capture_output=True,text=True,check=True)
    if loaded.stdout.strip() not in ('not-found',''):
        subprocess.run(['systemctl','disable','--now','nginx.service'],check=True)
    for info in nodes:
        current=identity(info['pid'])
        if current and current['start']==info['start'] and current['executable']==info['executable']:
            try:os.kill(info['pid'],signal.SIGTERM)
            except ProcessLookupError:pass
    for _ in range(10):
        if all(not listeners(port) for port in (80,443,3000)):break
        time.sleep(.5)
    remaining={str(port):[identity(pid) for pid in listeners(port)] for port in (80,443,3000)}
    print(json.dumps({'remaining_listeners':remaining},indent=2),flush=True)
    if any(remaining.values()):
        raise SystemExit('A listener remains or was restarted. Review the reported manager unit; no broad process kill was attempted.')
    print('Ports 80, 443 and 3000 are free. Nginx autostart disabled; files and applications retained.')


if __name__=='__main__':main()
