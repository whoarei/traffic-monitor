"""Acceptance checks on the deployed host. Reads secrets without printing them."""
from datetime import datetime, timezone
import base64
import json
from pathlib import Path
import socket
import sqlite3
import ssl
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request

sys.path.insert(0,"/usr/lib/vpn-traffic")
import store

BASE=Path("/root/xray-deployment")
config=json.loads(Path("/etc/vpn-traffic/config.json").read_text())
access=json.loads(Path("/etc/vpn-traffic/web-access.json").read_text())
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPSHandler(context=ssl.create_default_context()))
auth="Basic "+base64.b64encode((access["username"]+":"+access["password"]).encode()).decode()
checks={}


def get(path, expected=200, authorized=True):
    request=urllib.request.Request(access["url"]+path,headers={"Authorization":auth} if authorized else {})
    try:
        with opener.open(request,timeout=20) as response:
            status,body=response.status,response.read()
    except urllib.error.HTTPError as error:
        status,body=error.code,error.read()
    assert status==expected,(path,status,expected)
    return body


def snapshot():
    subprocess.run(["/usr/bin/vpn-traffic","checkpoint","--quiet"],check=True)
    return store.summary(config["database"],config["server"])


def main():
    get("",401,False)
    get("api/summary",401,False)
    checks["unauthenticated_page_and_api_denied"]=True
    assert b"VPN" in get("")
    assert json.loads(get("api/health"))["ok"]
    get("api/history?from=bad&to=2026-11-07",400)
    get("api/history?from=2026-11-08&to=2026-11-07",400)
    checks["authenticated_web_and_invalid_query_handling"]=True
    manifest=json.loads((BASE/"manifest.json").read_text())
    assert manifest==json.loads(Path("/root/vpn-traffic-before-manifest.json").read_text())
    checks["vpn_identities_and_links_unchanged"]=True
    transfer=[]
    with tempfile.TemporaryDirectory(prefix="traffic-acceptance-",dir=BASE) as temp:
        for index,device in enumerate(manifest["devices"]):
            before=snapshot()
            prior=next(d for d in before["devices"] if d["id"]==device["device"])
            client=json.loads((BASE/"clients"/(device["device"]+"-xray.json")).read_text())
            port=10884+index
            client["inbounds"][0]["port"]=port
            client["outbounds"][0]["settings"]["vnext"][0]["address"]="127.0.0.1"
            file=Path(temp)/(device["device"]+".json")
            file.write_text(json.dumps(client));file.chmod(0o600)
            proc=subprocess.Popen([config["xray_binary"],"run","-config",str(file)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            try:
                for _ in range(50):
                    try:
                        with socket.create_connection(("127.0.0.1",port),timeout=.1):break
                    except OSError:time.sleep(.1)
                result=subprocess.run(["curl","-fsS","--max-time","25","--noproxy","","--proxy",f"socks5h://127.0.0.1:{port}","https://speed.cloudflare.com/__down?bytes=262144"],capture_output=True,timeout=30)
                assert result.returncode==0 and len(result.stdout)==262144,(device["device"],"proxy download failed",result.returncode)
            finally:
                proc.terminate();proc.wait(timeout=8)
            after=snapshot()
            current=next(d for d in after["devices"] if d["id"]==device["device"])
            assert current["up"]>prior["up"] and current["down"]-prior["down"]>=262144
            transfer.append({"device":device["device"],"downloaded_bytes":262144,"recorded_upload_delta":current["up"]-prior["up"],"recorded_download_delta":current["down"]-prior["down"],"passed":True})
    checks["real_proxy_traffic_per_identity"]=transfer
    state=snapshot()
    day=datetime.now(store.TZ).date().isoformat()
    for device in state["devices"]:
        history=json.loads(get("api/history?from="+day+"&to="+day+"&device="+device["id"]))
        assert all(row["email"]==device["id"] for row in history["rows"])
        assert history["down"]>=262144
        csv=get("api/export.csv?from="+day+"&to="+day+"&device="+device["id"])
        assert csv.startswith(b"\xef\xbb\xbf") and device["label"].encode() in csv
    checks["per_device_history_and_csv"]=True
    publisher_pid=subprocess.check_output(["systemctl","show","xray","-p","MainPID","--value"],text=True).strip()
    subscriptions=json.loads((BASE/"subscription.json").read_text())
    urls=[d["url"] for d in subscriptions["devices"]]
    subprocess.run(["/usr/local/sbin/xray-subscription","publish"],check=True,stdout=subprocess.DEVNULL)
    assert publisher_pid==subprocess.check_output(["systemctl","show","xray","-p","MainPID","--value"],text=True).strip()
    assert urls==[d["url"] for d in json.loads((BASE/"subscription.json").read_text())["devices"]]
    for url in urls:
        with opener.open(url,timeout=15) as response:assert response.status==200 and b"reality-opts:" in response.read()
    assert json.loads(get("api/health"))["ok"]
    checks["subscription_republish_preserves_dashboard_urls_and_xray"]=True
    backup=Path(subprocess.check_output([str(BASE/"backup.sh")],text=True).strip())
    with tarfile.open(backup) as archive:
        name="var/backups/vpn-traffic/traffic.sqlite3"
        assert name in archive.getnames()
        with tempfile.TemporaryDirectory() as temp:
            database=Path(temp)/"snapshot.sqlite3"
            database.write_bytes(archive.extractfile(name).read())
            with sqlite3.connect(database) as db:assert db.execute("PRAGMA integrity_check").fetchone()[0]=="ok"
            archived=store.summary(database,config["server"])
            assert all(d["down"]>=262144 for d in archived["devices"])
    checks["full_backup_contains_consistent_sqlite_snapshot"]=True
    checks["backup"]=str(backup)
    report={"tested_utc":datetime.now(timezone.utc).isoformat(),"server":config["server"],"scope":"Server loopback VPN transfers; trusted public HTTPS and persistence checks", "checks":checks}
    Path("/root/vpn-traffic-verification.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))


if __name__=="__main__":main()
