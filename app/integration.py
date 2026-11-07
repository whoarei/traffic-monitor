"""Root-only integration with the existing Xray/subscription deployment."""
import copy
from datetime import datetime, timezone
import grp
import json
import os
from pathlib import Path
import pwd
import secrets
import subprocess
import tempfile

import store

ETC = Path("/etc/vpn-traffic")
STATE = ETC / "integration.json"
XRAY = Path("/usr/local/etc/xray/config.json")
SITE = Path("/etc/nginx/sites-available/xray-subscription.conf")
PUBLISHER = Path("/usr/local/sbin/xray-subscription")
BACKUP = Path("/root/xray-deployment/backup.sh")
DEVICE = Path("/usr/local/sbin/xray-device")
DROPIN = Path("/etc/systemd/system/xray.service.d/vpn-traffic.conf")
SNIPPET = Path("/etc/nginx/vpn-traffic/monitor.conf")
INCLUDE = "    include /etc/nginx/vpn-traffic/*.conf;\n"
BACKUP_BLOCK = '''# BEGIN vpn-traffic backup
if [ -x /usr/bin/vpn-traffic ]; then
  /usr/bin/vpn-traffic backup --output /var/backups/vpn-traffic/traffic.sqlite3 >/dev/null
  for traffic_path in \\
    etc/vpn-traffic \\
    etc/nginx/vpn-traffic \\
    etc/systemd/system/xray.service.d/vpn-traffic.conf \\
    usr/lib/systemd/system/vpn-traffic-collector.service \\
    usr/lib/systemd/system/vpn-traffic-manager.service \\
    usr/lib/systemd/system/vpn-traffic-web.service \\
    usr/bin/vpn-traffic \\
    usr/lib/vpn-traffic \\
    usr/share/vpn-traffic \\
    etc/systemd/system/vpn-traffic-updater.service \\
    usr/local/sbin/vpn-traffic-updater \\
    var/lib/vpn-traffic-updater \\
    var/backups/vpn-traffic/traffic.sqlite3
  do
    if [ -e "/$traffic_path" ]; then set -- "$@" "$traffic_path"; fi
  done
fi
# END vpn-traffic backup
'''


def run(*args):
    subprocess.run(args, check=True, timeout=45, stdout=subprocess.DEVNULL)


def atomic(path, content, mode=None, gid=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    stat = path.stat() if path.exists() else None
    fd, temp = tempfile.mkstemp(prefix=".traffic-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(content.encode() if isinstance(content, str) else content)
            out.flush()
            os.fsync(out.fileno())
        os.chmod(temp, mode if mode is not None else (stat.st_mode & 0o777 if stat else 0o600))
        os.chown(temp, 0, gid if gid is not None else (stat.st_gid if stat else 0))
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def json_write(path, value, mode=0o600):
    atomic(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n", mode)


def credentials(reset=False):
    target = ETC / "web-access.json"
    if target.exists() and not reset:
        result = json.loads(target.read_text())
    else:
        config = json.loads((ETC / "config.json").read_text())
        result = {"url": "https://" + config["server"] + ":8443/traffic/", "username": "admin", "password": secrets.token_urlsafe(24)}
        json_write(target, result)
    digest = subprocess.check_output(["openssl", "passwd", "-6", "-stdin"], input=(result["password"] + "\n").encode()).decode().strip()
    atomic(ETC / "htpasswd", result["username"] + ":" + digest + "\n", 0o640, grp.getgrnam("www-data").gr_gid)
    return result


def get_path(obj, parts):
    for part in parts:
        if not isinstance(obj, dict) or part not in obj:
            return False, None
        obj = obj[part]
    return True, obj


def set_path(obj, parts, value, remove=False):
    node = obj
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    if remove:
        node.pop(parts[-1], None)
        for count in range(len(parts) - 1, 0, -1):
            _, parent = get_path(obj, parts[:count])
            if parent != {}:
                break
            _, container = get_path(obj, parts[:count - 1])
            container.pop(parts[count - 1], None)
    else:
        node[parts[-1]] = value


def configure():
    os.umask(0o077)
    for path in (XRAY, SITE, PUBLISHER, BACKUP):
        if not path.is_file():
            raise RuntimeError("This package requires the existing Xray HTTPS-subscription deployment: " + str(path))
    account = pwd.getpwnam("vpn-traffic")
    ETC.mkdir(mode=0o755, parents=True, exist_ok=True)
    ETC.chmod(0o755)
    data = Path("/var/lib/vpn-traffic")
    data.mkdir(mode=0o700, exist_ok=True)
    os.chown(data, account.pw_uid, account.pw_gid)
    data.chmod(0o700)
    manifest = json.loads(Path("/root/xray-deployment/manifest.json").read_text())
    config = {"server": manifest["server"], "xray_binary": "/usr/local/bin/xray", "stats_address": "127.0.0.1:10085",
              "web_listen": "127.0.0.1:9081", "database": str(data / "traffic.sqlite3"), "interval": 30,
              "checkpoint_socket": "/run/vpn-traffic/collector.sock", "web_root": "/usr/share/vpn-traffic/web"}
    if (ETC / "config.json").exists():
        config.update(json.loads((ETC / "config.json").read_text()))
    if config["stats_address"] != "127.0.0.1:10085" or config["web_listen"] != "127.0.0.1:9081" or config["interval"] != 30:
        raise RuntimeError("This package version requires its default loopback ports and 30-second interval")
    json_write(ETC / "config.json", config, 0o644)
    credentials()
    original = json.loads(XRAY.read_text())
    candidate = copy.deepcopy(original)
    state = json.loads(STATE.read_text()) if STATE.exists() else {"changes": []}
    changes = state["changes"]

    def change(parts, value):
        present, old = get_path(candidate, parts)
        if present and old == value:
            return
        if not any(item["path"] == parts for item in changes):
            changes.append({"path": parts, "present": present, "old": copy.deepcopy(old), "new": value})
        set_path(candidate, parts, value)

    if "api" in candidate:
        api = copy.deepcopy(candidate["api"])
        if api.get("listen") != config["stats_address"]:
            raise RuntimeError("An existing Xray API uses a different address; refusing to replace it")
        api["services"] = list(dict.fromkeys(api.get("services", []) + ["StatsService"]))
    else:
        api = {"tag": "traffic-api", "listen": config["stats_address"], "services": ["StatsService"]}
    change(["api"], api)
    if "stats" not in candidate:
        change(["stats"], {})
    devices, seen = [], set()
    labels = {d["device"]: d["label"] for d in manifest["devices"]}
    for inbound in candidate.get("inbounds", []):
        if inbound.get("protocol") != "vless":
            continue
        users = inbound.get("settings", {}).get("clients", inbound.get("settings", {}).get("users", []))
        for user in users:
            email = user.get("email")
            if not email or email in seen:
                raise RuntimeError("Every VLESS identity needs a unique existing email label")
            seen.add(email)
            devices.append({"email": email, "label": labels.get(email, email)})
            for field in ("statsUserUplink", "statsUserDownlink"):
                change(["policy", "levels", str(user.get("level", 0)), field], True)
    if not devices:
        raise RuntimeError("No VLESS identities found")
    publisher = PUBLISHER.read_text()
    publisher = publisher.replace('parser.add_argument("device", choices=["android", "windows"], nargs="?")', 'parser.add_argument("device", nargs="?")')
    publisher = publisher.replace('add_header profile-title "{device[\'label\']}";', 'add_header profile-title {json.dumps(device[\'label\'], ensure_ascii=False)};')
    device_helper = DEVICE.read_text()
    if "# vpn-traffic managed lifecycle" not in device_helper:
        marker = '    backup=subprocess.check_output([str(BASE/"backup.sh")],text=True).strip()'
        if marker not in device_helper:
            raise RuntimeError("Unrecognized device maintenance helper")
        delegate = '''    # vpn-traffic managed lifecycle
    if Path("/run/vpn-traffic-manager/control.sock").exists():
        action = "link-revoke" if args.operation == "revoke" else "link-rotate"
        subprocess.run(["/usr/bin/vpn-traffic", action, "--device", args.device], check=True)
        return
'''
        device_helper = device_helper.replace(marker, delegate + marker, 1)
    device_helper = device_helper.replace('parser.add_argument("device", nargs="?", choices=["android","windows"])', 'parser.add_argument("device", nargs="?")')
    site = SITE.read_text()
    if INCLUDE not in publisher:
        if "    etag off;\n" not in publisher:
            raise RuntimeError("Unrecognized subscription publisher; refusing to edit it")
        publisher = publisher.replace("    etag off;\n", "    etag off;\n" + INCLUDE, 1)
    if INCLUDE not in site:
        if "    etag off;\n" not in site:
            raise RuntimeError("Unrecognized HTTPS subscription site")
        site = site.replace("    etag off;\n", "    etag off;\n" + INCLUDE, 1)
    backup = BACKUP.read_text()
    if "# BEGIN vpn-traffic backup" in backup:
        begin, rest = backup.split("# BEGIN vpn-traffic backup", 1)
        _, end = rest.split("# END vpn-traffic backup\n", 1)
        backup = begin + BACKUP_BLOCK + end
    else:
        if 'tar -C / -czf "$archive" "$@"' not in backup:
            raise RuntimeError("Unrecognized backup script")
        backup = backup.replace('tar -C / -czf "$archive" "$@"', BACKUP_BLOCK + 'tar -C / -czf "$archive" "$@"', 1)
    protected = Path("/var/backups/vpn-traffic")
    protected.mkdir(mode=0o700, exist_ok=True)
    snapshots = protected / ("before-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    snapshots.mkdir(mode=0o700)
    paths = (XRAY, SITE, PUBLISHER, BACKUP, DEVICE, DROPIN, SNIPPET)
    originals = {p: (p.read_bytes(), p.stat().st_mode & 0o777, p.stat().st_gid) if p.exists() else None for p in paths}
    for i, (path, value) in enumerate(originals.items()):
        if value:
            atomic(snapshots / (str(i) + "-" + path.name), value[0], 0o600)
    xray_changed = original != candidate
    try:
        if xray_changed:
            atomic(XRAY, json.dumps(candidate, indent=2) + "\n")
        atomic(PUBLISHER, publisher)
        atomic(SITE, site)
        atomic(BACKUP, backup)
        atomic(DEVICE, device_helper)
        atomic(DROPIN, "[Service]\n# Ask the collector to save counters before the Xray process exits.\nExecStop=-+/usr/bin/vpn-traffic checkpoint --quiet\n", 0o644)
        atomic(SNIPPET, Path("/usr/share/vpn-traffic/nginx.conf").read_bytes(), 0o644)
        store.initialize(config["database"], devices)
        for path in data.iterdir():
            if path.name.startswith("traffic.sqlite3"):
                os.chown(path, account.pw_uid, account.pw_gid)
                path.chmod(0o600)
        run(config["xray_binary"], "run", "-test", "-config", str(XRAY))
        run("nginx", "-t")
        run("systemctl", "daemon-reload")
        if xray_changed:
            run("systemctl", "restart", "xray.service")
            run("systemctl", "is-active", "--quiet", "xray.service")
        run("systemctl", "reload", "nginx.service")
        state.update(version=store.VERSION, server=config["server"], snapshot=str(snapshots))
        json_write(STATE, state)
    except BaseException:
        for path, value in originals.items():
            if value:
                atomic(path, value[0], value[1], value[2])
            else:
                path.unlink(missing_ok=True)
        subprocess.run(["systemctl", "daemon-reload"])
        if xray_changed:
            subprocess.run(["systemctl", "restart", "xray.service"])
        subprocess.run(["systemctl", "reload", "nginx.service"])
        raise
    print("Xray statistics, protected dashboard, and persistent database configured")


def disable():
    if not STATE.exists():
        return
    state = json.loads(STATE.read_text())
    config = json.loads(XRAY.read_text())
    for item in reversed(state["changes"]):
        present, current = get_path(config, item["path"])
        if present and current == item["new"]:
            set_path(config, item["path"], item["old"], remove=not item["present"])
    temp = XRAY.with_name("traffic-remove.candidate.json")
    try:
        atomic(temp, json.dumps(config, indent=2) + "\n", 0o600)
        run("/usr/local/bin/xray", "run", "-test", "-config", str(temp))
        atomic(XRAY, temp.read_bytes())
    finally:
        temp.unlink(missing_ok=True)
    DROPIN.unlink(missing_ok=True)
    SNIPPET.unlink(missing_ok=True)
    STATE.unlink()
    # Empty wildcard includes and the conditional backup hook remain harmless.
    run("systemctl", "daemon-reload")
    run("systemctl", "restart", "xray.service")
    run("nginx", "-t")
    run("systemctl", "reload", "nginx.service")
