"""Privileged, local-only link lifecycle and monthly quota enforcement."""
import copy
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import select
import signal
import socket
import struct
import subprocess
import time
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit
import uuid
import secrets

import collector
import integration
import store
from manager_client import SOCKET

BASE = Path("/root/xray-deployment")
MANIFEST = BASE / "manifest.json"
XRAY = Path("/usr/local/etc/xray/config.json")


def validate_policy(data):
    quota = data.get("quota", 0)
    if isinstance(quota, bool) or not isinstance(quota, int) or not 0 <= quota <= 1024**5:
        raise ValueError("额度必须是 0 到 1 PiB 的整数字节数，0 表示不限额")
    if "reset_day" in data or "reset_time" in data:
        raise ValueError("重置计划由系统统一管理，请在系统设置中修改")
    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("启用状态格式不正确")
    return quota, enabled


def validate_label(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 64 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("连接名称需为 1 到 64 个字符，不能含控制字符")
    return value.strip()


def command(*args, timeout=45):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        integration.atomic(Path("/var/backups/vpn-traffic/last-manager-command.log"), args[0]+"\n"+result.stdout+result.stderr, 0o600)
        raise RuntimeError("服务配置或更新失败，请查看管理服务日志")


def render_exports(manifest, device):
    if "uri" in device:
        parts = urlsplit(device["uri"])
        query = {key: value[0] for key, value in parse_qs(parts.query).items()}
    else:
        query = {"encryption": "none", "security": "reality", "sni": manifest["sni"], "fp": "chrome", "type": "tcp", "flow": "xtls-rprx-vision", "spx": "/" + device["device"]}
        parts = urlsplit("vless://placeholder")
    query.update(pbk=manifest["public_key"], sid=device["short_id"], sni=manifest["sni"])
    device["uri"] = urlunsplit(("vless", f"{device['uuid']}@{manifest['server']}:{manifest['port']}", "", urlencode(query, quote_via=quote), quote(device["label"], safe="")))
    existing = BASE / "clients" / (device["device"] + "-xray.json")
    template = json.loads((existing if existing.exists() else BASE / "clients/android-xray.json").read_text())
    target = template["outbounds"][0]
    target["settings"]["vnext"][0].update(address=manifest["server"], port=manifest["port"])
    target["settings"]["vnext"][0]["users"][0]["id"] = device["uuid"]
    target["streamSettings"]["realitySettings"].update(publicKey=manifest["public_key"], shortId=device["short_id"], serverName=manifest["sni"], spiderX="/"+device["device"])
    integration.atomic(BASE / "clients" / (device["device"] + ".txt"), device["uri"] + "\n", 0o600)
    integration.json_write(BASE / "clients" / (device["device"] + "-xray.json"), template)


def desired(policy):
    if not policy["enabled"]:
        return False, "disabled"
    if policy["quota"] and policy["period_up"] + policy["period_down"] >= policy["quota"] + policy.get("bonus",0):
        return False, "quota"
    return True, ""


class Manager:
    def __init__(self, config):
        self.config = config
        self.pending_publish = True

    def checkpoint(self):
        collector.checkpoint(self.config, True)

    def apply(self, manifest=None, dry_run=False):
        original_manifest = MANIFEST.read_bytes()
        original_config = XRAY.read_bytes()
        manifest = copy.deepcopy(manifest or json.loads(original_manifest))
        config = json.loads(original_config)
        with store.database(self.config["database"], readonly=True) as db:
            policies = {row["email"]: dict(row) for row in db.execute("SELECT * FROM limits")}
        inbound = next(i for i in config["inbounds"] if i.get("tag") == "vless-reality")
        known = {device["device"] for device in manifest["devices"]}
        key = "clients" if "clients" in inbound["settings"] else "users"
        users = [u for u in inbound["settings"][key] if u.get("email") not in known]
        short_ids = []
        states = {}
        for device in manifest["devices"]:
            policy = policies.get(device["device"])
            if policy is None:
                continue
            active, reason = desired(policy)
            states[device["device"]] = (active, reason)
            device["active"] = active
            if active:
                users.append({"id": device["uuid"], "email": device["device"], "flow": "xtls-rprx-vision"})
                short_ids.append(device["short_id"])
        if users:
            # Preserve short IDs belonging to any unmanaged users.
            managed_sids = {d["short_id"] for d in manifest["devices"]}
            short_ids += [sid for sid in inbound["streamSettings"]["realitySettings"]["shortIds"] if sid not in managed_sids]
        inbound["settings"][key] = users
        reality = inbound["streamSettings"]["realitySettings"]
        reality["shortIds"] = list(dict.fromkeys(short_ids)) or [reality["shortIds"][0]]
        core_changed = config != json.loads(original_config)
        manifest_changed = manifest != json.loads(original_manifest)
        if dry_run:
            return {"validated": True}
        if core_changed:
            self.checkpoint()
            candidate = BASE / "traffic-candidate.json"
            try:
                integration.json_write(candidate, config)
                command(self.config["xray_binary"], "run", "-test", "-config", str(candidate))
            finally:
                candidate.unlink(missing_ok=True)
            archive = Path("/var/backups/vpn-traffic/link-changes")
            archive.mkdir(mode=0o700, exist_ok=True)
            stamp = str(time.time_ns())
            integration.atomic(archive / (stamp + "-config.json"), original_config, 0o600)
            integration.atomic(archive / (stamp + "-manifest.json"), original_manifest, 0o600)
        if core_changed or manifest_changed:
            for device in manifest["devices"]:
                render_exports(manifest, device)
            integration.json_write(MANIFEST, manifest)
        if core_changed:
            integration.atomic(XRAY, json.dumps(config, indent=2) + "\n")
            try:
                command("systemctl", "restart", "xray.service")
                command("systemctl", "is-active", "--quiet", "xray.service")
            except Exception:
                integration.atomic(XRAY, original_config)
                integration.atomic(MANIFEST, original_manifest)
                command("systemctl", "restart", "xray.service")
                raise
        with store.database(self.config["database"]) as db:
            for email, (active, reason) in states.items():
                db.execute("UPDATE devices SET active=? WHERE email=?", (int(active), email))
                db.execute("UPDATE limits SET blocked=? WHERE email=?", (reason, email))
        if core_changed or manifest_changed or self.pending_publish:
            self.pending_publish = True
            command("/usr/local/sbin/xray-subscription", "publish")
            self.pending_publish = False
        return {"restarted": core_changed}

    def handle(self, request):
        from updater_client import maintenance
        if maintenance() and request.get("action") != "sharing":
            raise ValueError("系统正在升级，连接管理暂时锁定")
        action = request.get("action")
        if action not in ("create", "update", "reset", "sharing", "rotate", "revoke", "schedule", "bonus"):
            raise ValueError("不支持的管理操作")
        if action == "schedule":
            day,clock=request.get("reset_day"),request.get("reset_time")
            store.reset_bounds(day,clock)
            self.checkpoint()
            return {"schedule":store.update_schedule(self.config["database"],day,clock),"restarted":False}
        manifest = json.loads(MANIFEST.read_text())
        email = request.get("id", "")
        if action == "create":
            operation = request.get("request_id", "")
            if not isinstance(operation, str) or not re.fullmatch(r"[a-zA-Z0-9-]{16,64}", operation):
                raise ValueError("缺少有效的操作标识")
            email = "link-" + hashlib.sha256(operation.encode()).hexdigest()[:12]
            if any(d["device"] == email for d in manifest["devices"]):
                return {"id": email, "created": True}
            if len(manifest["devices"]) >= 256:
                raise ValueError("此版本最多管理 256 条连接")
            label = validate_label(request.get("label", ""))
            quota, enabled = validate_policy(request)
            device = {"device": email, "label": label, "uuid": str(uuid.uuid4()), "short_id": secrets.token_hex(8)}
            manifest["devices"].append(device)
            self.checkpoint()
            with store.database(self.config["database"]) as db:
                db.execute("INSERT OR IGNORE INTO devices(email,label,active) VALUES (?,?,0)", (email, label))
                store.ensure_limit(db, email, enabled)
                db.execute("UPDATE limits SET quota=?,enabled=? WHERE email=?", (quota,int(enabled),email))
            result = self.apply(manifest)
            return {"id": email, "created": True, **result}
        if not isinstance(email, str) or not re.fullmatch(r"[a-zA-Z0-9_.@-]{1,80}", email):
            raise ValueError("无效的连接标识")
        device = next((d for d in manifest["devices"] if d["device"] == email), None)
        if device is None:
            raise ValueError("连接不存在")
        if action == "sharing":
            subscriptions = json.loads((BASE / "subscription.json").read_text())
            subscription = next((d for d in subscriptions["devices"] if d["device"] == email), None)
            return {"id": email, "label": device["label"], "vless": device["uri"], "subscription": subscription["url"] if subscription else "", "active": device.get("active", True)}
        if request.get("dry_run"):
            return {"validated": True, "id": email}
        self.checkpoint()
        with store.database(self.config["database"]) as db:
            policy = dict(db.execute("SELECT * FROM limits WHERE email=?", (email,)).fetchone())
        if action == "update":
            label = validate_label(request.get("label", device["label"]))
            merged = {**policy, **request}
            merged["enabled"] = request.get("enabled", bool(policy["enabled"]))
            quota, enabled = validate_policy(merged)
            device["label"] = label
            with store.database(self.config["database"]) as db:
                db.execute("UPDATE devices SET label=? WHERE email=?", (label,email))
                db.execute("UPDATE limits SET quota=?,enabled=? WHERE email=?", (quota,int(enabled),email))
        elif action == "reset":
            with store.database(self.config["database"]) as db:
                db.execute("UPDATE limits SET period_up=0,period_down=0 WHERE email=?", (email,))
                db.execute("INSERT INTO events(timestamp,kind,message) VALUES (?,?,?)", (time.time(),"manual_reset",email))
        elif action == "revoke":
            with store.database(self.config["database"]) as db:
                db.execute("UPDATE limits SET enabled=0 WHERE email=?", (email,))
        elif action == "rotate":
            device.update(uuid=str(uuid.uuid4()), short_id=secrets.token_hex(8))
            with store.database(self.config["database"]) as db:
                db.execute("UPDATE limits SET enabled=1 WHERE email=?", (email,))
        elif action == "bonus":
            operation=request.get("request_id","")
            if not isinstance(operation,str) or not re.fullmatch(r"[a-zA-Z0-9-]{16,64}",operation):
                raise ValueError("缺少有效的操作标识")
            allowance=store.grant_bonus(self.config["database"],email,request.get("amount"),operation)
            result=self.apply(manifest)
            return {"id":email,**allowance,**result}
        result = self.apply(manifest)
        with store.database(self.config["database"]) as db:
            db.execute("INSERT INTO events(timestamp,kind,message) VALUES (?,?,?)", (time.time(),"link_"+action,email))
        return {"id": email, **result}

    def enforce(self):
        now = time.time()
        with store.database(self.config["database"], readonly=True) as db:
            rows = [dict(row) for row in db.execute("SELECT d.*,l.quota,l.bonus,l.enabled,l.blocked,l.period_up,l.period_down FROM devices d JOIN limits l ON l.email=d.email")]
            plan=store.schedule(db)
        if plan["next_reset"] <= now:
            self.checkpoint()
            self.apply()
            return
        if any(r["enabled"] and r["blocked"] == "quota" and (not r["quota"] or r["period_up"] + r["period_down"] < r["quota"] + r["bonus"]) for r in rows):
            self.apply()
            return
        if not any(r["quota"] and r["enabled"] and r["active"] for r in rows):
            if self.pending_publish:
                self.apply()
            return
        generation, counters = collector.read_counters(self.config)
        exceeded = False
        for row in rows:
            if not row["quota"] or not row["enabled"] or not row["active"]:
                continue
            raw = counters.get(row["email"], {})
            addition = 0
            for direction, old in (("uplink", "raw_up"), ("downlink", "raw_down")):
                current = int(raw.get(direction, row[old] if generation == row["generation"] else 0))
                addition += current - row[old] if generation == row["generation"] and current >= row[old] else current
            if row["period_up"] + row["period_down"] + addition >= row["quota"] + row["bonus"]:
                exceeded = True
        if exceeded:
            self.checkpoint()
            self.apply()


def run(config):
    # Recover any interrupted privileged SQLite transaction before readers open it.
    with store.database(config["database"]) as db:
        db.execute("SELECT COUNT(*) FROM devices").fetchone()
    manager = Manager(config)
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    uid = pwd.getpwnam("vpn-traffic").pw_uid
    path = Path(SOCKET)
    path.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(SOCKET)
        path.chmod(0o660)
        server.listen(8)
        next_check = 0
        manager.apply()
        while running:
            if time.monotonic() >= next_check:
                try:
                    manager.enforce()
                    with store.database(config["database"]) as db:
                        store.meta_set(db,"manager_error","")
                        store.meta_set(db,"manager_seen",time.time())
                except Exception as error:
                    print("Quota enforcement failed: "+str(error),flush=True)
                    with store.database(config["database"]) as db:
                        store.meta_set(db,"manager_error",str(error)[:200])
                next_check = time.monotonic() + 2
            if not select.select([server],[],[],min(1,max(0,next_check-time.monotonic())))[0]:
                continue
            with server.accept()[0] as client:
                client.settimeout(5)
                try:
                    peer = struct.unpack("3i",client.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))[1]
                    if peer not in (0,uid):
                        raise ValueError("无权访问管理服务")
                    data=bytearray()
                    while b"\n" not in data and len(data)<=16384:
                        part=client.recv(4096)
                        if not part:break
                        data.extend(part)
                    if len(data)>16384:
                        raise ValueError("请求过大")
                    request=json.loads(data)
                    reply={"ok":True,"result":manager.handle(request)}
                except (ValueError,KeyError,TypeError) as error:
                    reply={"ok":False,"error":str(error)}
                except Exception as error:
                    print("Management operation failed: "+str(error),flush=True)
                    reply={"ok":False,"error":"操作未完成，请刷新列表确认状态后重试"}
                try:
                    client.sendall(json.dumps(reply,ensure_ascii=False).encode()+b"\n")
                except OSError:
                    pass
    path.unlink(missing_ok=True)
