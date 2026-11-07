"""Run in a transient systemd unit; creates only explicitly labelled test identities."""
from datetime import datetime, timezone
import base64
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import time
import urllib.error
import urllib.request
import uuid

access=json.loads(Path('/etc/vpn-traffic/web-access.json').read_text())
origin=access['url'].split('/traffic/')[0]
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
authorization='Basic '+base64.b64encode((access['username']+':'+access['password']).encode()).decode()
token=''
fixture=''


def request(path,payload=None,authorized=True):
    global token
    headers={'Authorization':authorization} if authorized else {}
    if payload is not None:
        if not token:token=request('api/session')['token']
        headers.update({'Origin':origin,'Content-Type':'application/json','X-VPN-CSRF':token})
    req=urllib.request.Request(access['url']+path,data=json.dumps(payload).encode() if payload is not None else None,headers=headers)
    with opener.open(req,timeout=50) as response:return json.load(response)


def state(id):
    return next(d for d in request('api/summary')['devices'] if d['id']==id)


def wait_state(id,predicate,seconds=25):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        current=state(id)
        if predicate(current):return current
        time.sleep(.3)
    raise AssertionError('Identity did not reach the expected quota state')


def main():
    global fixture
    original=json.loads(Path('/root/vpn-traffic-before-1.1.0-manifest.json').read_text())
    original_ids={d['device'] for d in original['devices']}
    before=request('api/summary')
    previous=Path('/root/vpn-traffic-quota-fixture.json')
    if previous.exists() and any(d['id']==json.loads(previous.read_text())['id'] and d['label']=='QA 配额 "停用验证"' for d in before['devices']):
        fixture=json.loads(previous.read_text())['id']
        request('api/links/'+fixture+'/reset',{})
        request('api/links/'+fixture+'/update',{'quota':1024*1024,'reset_day':15,'reset_time':'09:30'})
    else:
        item=request('api/links',{'request_id':str(uuid.uuid4()),'label':'QA 配额 "停用验证"','quota':1024*1024,'reset_day':15,'reset_time':'09:30','enabled':False})
        fixture=item['id']
    Path('/root/vpn-traffic-quota-fixture.json').write_text(json.dumps({'id':fixture,'label':'QA 配额 "停用验证"'}))
    request('api/links/'+fixture+'/update',{'enabled':True})
    wait_state(fixture,lambda d:d['active'])
    manifest=json.loads(Path('/root/xray-deployment/manifest.json').read_text())
    device=next(d for d in manifest['devices'] if d['device']==fixture)
    server_ip=access['url'].split('/')[2].split(':')[0]
    class Stream(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            if self.client_address[0] not in (server_ip,'127.0.0.1'):
                self.send_error(403);return
            self.send_response(200);self.send_header('Content-Length',str(8388608));self.end_headers()
            try:
                for _ in range(256):
                    self.wfile.write(b'x'*32768);self.wfile.flush();time.sleep(.05)
            except OSError:pass
    upstream=ThreadingHTTPServer((server_ip,0),Stream)
    threading.Thread(target=upstream.serve_forever,daemon=True).start()
    with tempfile.TemporaryDirectory(prefix='quota-check-',dir='/root/xray-deployment') as temp:
        client=json.loads((Path('/root/xray-deployment/clients')/(fixture+'-xray.json')).read_text())
        client['inbounds'][0]['port']=10886
        client['outbounds'][0]['settings']['vnext'][0]['address']='127.0.0.1'
        file=Path(temp)/'client.json';file.write_text(json.dumps(client));file.chmod(0o600)
        proc=subprocess.Popen(['/usr/local/bin/xray','run','-config',str(file)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                try:
                    with socket.create_connection(('127.0.0.1',10886),timeout=.1):break
                except OSError:time.sleep(.1)
            old_pid=subprocess.check_output(['systemctl','show','xray','-p','MainPID','--value'],text=True).strip()
            download=subprocess.run(['curl','-fsS','--max-time','20','--noproxy','','--proxy','socks5h://127.0.0.1:10886',f'http://{server_ip}:{upstream.server_address[1]}/'],capture_output=True,timeout=25)
            blocked=wait_state(fixture,lambda d:not d['active'] and d['blocked']=='quota')
            assert blocked['period_used']>=1024*1024
            assert download.returncode not in (0,28) and len(download.stdout)<8388608,'Existing transfer was not cut off before timeout'
            assert old_pid!=subprocess.check_output(['systemctl','show','xray','-p','MainPID','--value'],text=True).strip()
            retry=subprocess.run(['curl','-fsS','--max-time','5','--noproxy','','--proxy','socks5h://127.0.0.1:10886','https://api.ipify.org'],capture_output=True,timeout=8)
            assert retry.returncode!=0,'An exhausted identity accepted a new proxy request'
        finally:
            proc.terminate();proc.wait(timeout=8)
            upstream.shutdown();upstream.server_close()
    request('api/links/'+fixture+'/reset',{})
    restored=wait_state(fixture,lambda d:d['active'] and d['period_used']==0)
    assert restored['total']>=blocked['total']
    previous_pid=subprocess.check_output(['systemctl','show','xray','-p','MainPID','--value'],text=True).strip()
    edited=request('api/links/'+fixture+'/update',{'quota':2*1024**3,'reset_day':28,'reset_time':'10:15'})
    assert not edited['restarted']
    assert previous_pid==subprocess.check_output(['systemctl','show','xray','-p','MainPID','--value'],text=True).strip()
    schedule=state(fixture)
    assert schedule['quota']==2*1024**3 and schedule['reset_day']==28 and schedule['reset_time']=='10:15'
    subprocess.run(['systemctl','restart','vpn-traffic-manager'],check=True)
    for _ in range(40):
        try:
            if request('api/health')['manager_ok']:break
        except (OSError,urllib.error.HTTPError):pass
        time.sleep(.25)
    persisted=state(fixture)
    assert persisted['quota']==schedule['quota'] and persisted['next_reset']==schedule['next_reset']
    request('api/links/'+fixture+'/revoke',{})
    assert not state(fixture)['active']
    after=request('api/summary')
    for d in after['devices']:
        if d['id'] in original_ids:
            assert d['quota']==0 and d['active'] and d['enabled']
            assert d['total']>=next(p['total'] for p in before['devices'] if p['id']==d['id'])
    final_manifest=json.loads(Path('/root/xray-deployment/manifest.json').read_text())
    for old in original['devices']:
        current=next(d for d in final_manifest['devices'] if d['device']==old['device'])
        assert all(current[key]==old[key] for key in ('uuid','short_id','uri'))
    report={'tested_utc':datetime.now(timezone.utc).isoformat(),'monthly_quota_suspended_identity':True,'existing_long_transfer_terminated':True,'new_connections_denied_after_quota':True,'manual_period_reset_restored_identity':True,'historical_total_retained':True,'custom_reset_schedule_saved':True,'quota_and_schedule_persisted_after_manager_restart':True,'quota_only_edit_did_not_restart_xray':True,'existing_two_identities_remain_unlimited':True,'existing_credentials_unchanged':True,'limit_bytes':1024*1024,'recorded_at_suspension':blocked['period_used'],'client_received_before_cutoff':len(download.stdout)}
    Path('/root/vpn-traffic-quota-verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    try:main()
    finally:
        if fixture:
            try:request('api/links/'+fixture+'/revoke',{})
            except Exception:pass
