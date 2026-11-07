"""Remove only this task's explicitly recorded, disabled acceptance identities."""
import json
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0,'/usr/lib/vpn-traffic')
import collector
import integration
from manager_client import call
import store

config=json.loads(Path('/etc/vpn-traffic/config.json').read_text())
fixtures=[json.loads(Path('/root/'+name).read_text()) for name in ('vpn-traffic-web-fixture.json','vpn-traffic-quota-fixture.json')]
ids=[d['id'] for d in fixtures]
assert len(set(ids))==2 and all(x.startswith('link-') for x in ids)
manifest_path=Path('/root/xray-deployment/manifest.json')
manifest=json.loads(manifest_path.read_text())
for fixture in fixtures:
    device=next(d for d in manifest['devices'] if d['device']==fixture['id'])
    assert device['label']==fixture['label'] and device['label'].startswith('QA ')
    call('revoke',id=fixture['id'])
collector.checkpoint(config,True)
generation,counters=collector.read_counters(config)
assert not any(id in counters for id in ids),'Test counters still exist in the active Xray process'
subprocess.run(['systemctl','stop','vpn-traffic-manager'],check=True)
try:
    manifest=json.loads(manifest_path.read_text())
    manifest['devices']=[d for d in manifest['devices'] if d['device'] not in ids]
    integration.json_write(manifest_path,manifest)
    with store.database(config['database']) as db:
        for id in ids:
            assert db.execute('SELECT active FROM devices WHERE email=?',(id,)).fetchone()[0]==0
            for table in ('samples','daily','allowance_operations','limits','devices'):
                db.execute('DELETE FROM '+table+' WHERE email=?',(id,))
    for id in ids:
        for suffix in ('.txt','-xray.json','-clash.yaml'):
            (Path('/root/xray-deployment/clients')/(id+suffix)).unlink(missing_ok=True)
        (Path('/var/lib/xray-subscription/feeds')/(id+'.yaml')).unlink(missing_ok=True)
finally:
    subprocess.run(['systemctl','start','vpn-traffic-manager'],check=True)
print('Removed two disabled acceptance fixtures; production identities and history retained')
