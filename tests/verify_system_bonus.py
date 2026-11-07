"""Live quota/temporary-allowance acceptance; reset expiry is checked on a DB copy."""
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import base64
import json
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import uuid

sys.path.insert(0,'/usr/lib/vpn-traffic')
import store

access=json.loads(Path('/etc/vpn-traffic/web-access.json').read_text())
config=json.loads(Path('/etc/vpn-traffic/config.json').read_text())
origin=access['url'].split('/traffic/')[0]
authorization='Basic '+base64.b64encode((access['username']+':'+access['password']).encode()).decode()
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
token=''
fixture=''


def request(path,payload=None):
    global token
    headers={'Authorization':authorization}
    if payload is not None:
        if not token:token=request('api/session')['token']
        headers.update({'Origin':origin,'Content-Type':'application/json','X-VPN-CSRF':token})
    req=urllib.request.Request(access['url']+path,data=json.dumps(payload).encode() if payload is not None else None,headers=headers)
    with opener.open(req,timeout=50) as response:return json.load(response)


def state():return next(d for d in request('api/summary')['devices'] if d['id']==fixture)


def wait(predicate):
    for _ in range(80):
        current=state()
        if predicate(current):return current
        time.sleep(.3)
    raise AssertionError('Quota state did not converge')


def main():
    global fixture
    before=request('api/summary')
    prior=json.loads(Path('/root/vpn-traffic-before-1.2.0-status.json').read_text())
    for d in prior['devices']:
        current=next(x for x in before['devices'] if x['id']==d['id'])
        assert current['quota']==d['quota'] and current['period_used']>=d['period_used'] and current['total']>=d['total']
    item=request('api/links',{'request_id':str(uuid.uuid4()),'label':'QA 系统临时额度验证','quota':1048576,'enabled':False})
    fixture=item['id']
    Path('/root/vpn-traffic-quota-fixture.json').write_text(json.dumps({'id':fixture,'label':'QA 系统临时额度验证'}))
    request('api/links/'+fixture+'/update',{'enabled':True})
    server_ip=config['server']
    class Stream(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):
            if self.client_address[0] not in (server_ip,'127.0.0.1'):
                self.send_error(403);return
            self.send_response(200);self.send_header('Content-Length','8388608');self.end_headers()
            try:
                for _ in range(256):
                    self.wfile.write(b'x'*32768);self.wfile.flush();time.sleep(.05)
            except OSError:pass
    upstream=ThreadingHTTPServer((server_ip,0),Stream)
    threading.Thread(target=upstream.serve_forever,daemon=True).start()
    with tempfile.TemporaryDirectory(prefix='system-allowance-',dir='/root/xray-deployment') as temp:
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
            common=['curl','-fsS','--max-time','20','--noproxy','','--proxy','socks5h://127.0.0.1:10886']
            transfer=subprocess.run(common+[f'http://{server_ip}:{upstream.server_address[1]}/'],capture_output=True,timeout=25)
            blocked=wait(lambda d:not d['active'] and d['blocked']=='quota')
            assert transfer.returncode not in (0,28) and blocked['period_used']>=1048576
            rejected=subprocess.run(common+['https://api.ipify.org'],capture_output=True,timeout=25)
            assert rejected.returncode!=0
            operation=str(uuid.uuid4())
            granted=request('api/links/'+fixture+'/bonus',{'amount':1048576,'request_id':operation})
            restored=wait(lambda d:d['active'] and d['available']>0)
            assert granted['available']==1048576 and restored['quota']==1048576
            assert restored['period_used']>=blocked['period_used'] and restored['total']>=blocked['total']
            good=subprocess.run(common+['https://api.ipify.org'],capture_output=True,text=True,timeout=25)
            assert good.returncode==0 and good.stdout.strip()==server_ip
            bonus=state()['bonus']
            request('api/links/'+fixture+'/bonus',{'amount':1048576,'request_id':operation})
            assert state()['bonus']==bonus
        finally:
            proc.terminate();proc.wait(timeout=8);upstream.shutdown();upstream.server_close()
    # Exercise the next system reset without changing production account ledgers.
    with tempfile.TemporaryDirectory() as temp:
        copy=Path(temp)/'snapshot.sqlite3'
        with store.database(config['database'],readonly=True) as source,sqlite3.connect(copy) as destination:source.backup(destination)
        old=store.summary(copy,server_ip)
        with store.database(copy,readonly=True) as db:generation=dict(db.execute('SELECT key,value FROM meta'))['generation']
        store.record(copy,{},generation,old['schedule']['next_reset']+15)
        new=store.summary(copy,server_ip,old['schedule']['next_reset']+15)
        assert all(d['bonus']==0 and d['period_used']==0 for d in new['devices'])
        for d in new['devices']:
            original=next(x for x in old['devices'] if x['id']==d['id'])
            assert d['quota']==original['quota'] and d['total']==original['total']
    request('api/links/'+fixture+'/revoke',{})
    after=request('api/summary')
    assert after['schedule']==before['schedule']
    for d in prior['devices']:
        current=next(x for x in after['devices'] if x['id']==d['id'])
        assert current['quota']==d['quota'] and current['period_used']>=d['period_used']
    report={'tested_utc':datetime.now(timezone.utc).isoformat(),'globalScheduleSingleSource':True,'migrationPreservedUserQuotasAndUsage':True,'overQuotaIdentityStopped':True,'existingTransferInterrupted':True,'newConnectionsDeniedWhenExhausted':True,'temporaryAllowanceRestoredRealProxyConnection':True,'baseMonthlyQuotaUnchanged':True,'usageAndHistoryNotResetByGrant':True,'grantRetryIdempotent':True,'nextSystemResetClearsAllTemporaryAllowancesOnDatabaseCopy':True,'resetPreservesBaseQuotasAndHistoricalTotalsOnDatabaseCopy':True,'productionScheduleAndLedgersNotReset':True}
    Path('/root/vpn-traffic-system-verification.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    try:main()
    finally:
        if fixture:
            try:request('api/links/'+fixture+'/revoke',{})
            except Exception:pass
