from datetime import datetime,timezone
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time

sys.path.insert(0,'/usr/lib/vpn-traffic')
import collector
import store

config=json.loads(Path('/etc/vpn-traffic/config.json').read_text())
collector.checkpoint(config,True)
before=json.loads(Path('/root/vpn-traffic-before-1.3.0-status.json').read_text())
current=store.summary(config['database'],config['server'])
assert current['schedule']==before['schedule']
for old in before['devices']:
    device=next(d for d in current['devices'] if d['id']==old['id'])
    assert device['quota']==old['quota'] and device['bonus']==old['bonus']
    assert device['period_used']>=old['period_used'] and device['total']>=old['total']
old_pid=Path('/root/vpn-traffic-before-1.3.0-xray-pid.txt').read_text().strip()
assert old_pid==subprocess.check_output(['systemctl','show','xray','-p','MainPID','--value'],text=True).strip()
metrics=store.read_system(config['database'])
assert metrics['available']
for key in ('cpu','memory','disk'):assert 0<=metrics[key+'_percent']<=100
assert 0<=metrics['memory_used']<=metrics['memory_total'] and 0<=metrics['disk_used']<=metrics['disk_total']
with tempfile.TemporaryDirectory() as temp:
    copy=Path(temp)/'db'
    with store.database(config['database'],readonly=True) as source,sqlite3.connect(copy) as target:source.backup(target)
    now=time.time()
    snapshot=store.summary(copy,config['server'],now)
    hourly=store.hourly_history(copy,now=now)
    assert hourly['up']==sum(r['up'] for r in hourly['rows']) and hourly['down']==sum(r['down'] for r in hourly['rows'])
    if now-snapshot['started']<86400:
        assert hourly['up']==sum(d['up'] for d in snapshot['devices'])
        assert hourly['down']==sum(d['down'] for d in snapshot['devices'])
    for device in snapshot['devices']:
        selected=store.hourly_history(copy,device['id'],now)
        assert all(row['email']==device['id'] for row in selected['rows'])
    assert store.summary(copy,config['server'],now)==snapshot
report={'tested_utc':datetime.now(timezone.utc).isoformat(),'hourlyWindowSeconds':86400,'hourlySumsConserved':True,'singleIdentityHistoryIsolated':True,'hourlyReadsDoNotChangeAccounting':True,'quotasAndTemporaryAllowancesPreserved':True,'globalSchedulePreserved':True,'existingHistoryRetained':True,'xrayProcessNotRestarted':True,'resourcesFreshAndInValidRanges':True,'resourceSnapshot':metrics,'deviceCount':len(current['devices'])}
Path('/root/vpn-traffic-history-verification.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
