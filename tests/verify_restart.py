"""Run as a systemd transient job so restarting a VPN cannot interrupt the check."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0,"/usr/lib/vpn-traffic")
import collector
import store

config=json.loads(Path('/etc/vpn-traffic/config.json').read_text())
collector.checkpoint(config,True)
before=store.summary(config['database'],config['server'])
oldpid=subprocess.check_output(['systemctl','show','xray','-p','MainPID','--value'],text=True).strip()
subprocess.run(['systemctl','restart','xray'],check=True)
for attempt in range(30):
    try:
        collector.checkpoint(config,True)
        break
    except Exception:
        time.sleep(.2)
else:
    raise RuntimeError('Collector did not recover after Xray restart')
after=store.summary(config['database'],config['server'])
newpid=subprocess.check_output(['systemctl','show','xray','-p','MainPID','--value'],text=True).strip()
assert oldpid!=newpid and after['healthy']
prior={d['id']:d for d in before['devices']}
assert all(d['up']>=prior[d['id']]['up'] and d['down']>=prior[d['id']]['down'] for d in after['devices'])
with store.database(config['database'],readonly=True) as db:
    assert db.execute("SELECT COUNT(*) FROM events WHERE kind='xray_restart'").fetchone()[0]>=1
report={'tested_utc':datetime.now(timezone.utc).isoformat(),'normal_xray_restart_preserved_totals':True,'collector_recovered':True,'before':{d['id']:d['total'] for d in before['devices']},'after':{d['id']:d['total'] for d in after['devices']}}
Path('/root/vpn-traffic-restart-verification.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report))
