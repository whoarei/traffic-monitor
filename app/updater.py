"""Independent root update broker and recoverable, immutable per-job workers."""
import argparse
from contextlib import contextmanager
import fcntl
import grp
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import signal
import socket
import socketserver
import sqlite3
import struct
import subprocess
import sys
import tempfile
import tarfile
import threading
import time
import urllib.request
import uuid

from update_protocol import API, MANIFEST_NAME, SIGNATURE_NAME, SERVICES, ReleaseSource, UpdateError, asset_url, package_name, verify_manifest, version_tuple

STATE = Path("/var/lib/vpn-traffic-updater")
ETC = Path("/etc/vpn-traffic")
RUN = Path("/run/vpn-traffic-updater")
SOCKET = RUN / "control.sock"
TERMINAL = {"succeeded", "failed", "rolled_back", "recovery_required"}
PROTECTED = ["/etc/vpn-traffic/config.json", "/etc/vpn-traffic/integration.json", "/etc/vpn-traffic/web-access.json", "/etc/vpn-traffic/htpasswd", "/root/xray-deployment/manifest.json", "/usr/local/etc/xray/config.json", "/usr/local/sbin/xray-subscription", "/usr/local/sbin/xray-device", "/root/xray-deployment/backup.sh", "/etc/nginx/sites-available/xray-subscription.conf", "/etc/nginx/vpn-traffic/monitor.conf", "/etc/systemd/system/xray.service.d/vpn-traffic.conf"]


def atomic(path, value, mode=0o600):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    pending = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with pending.open("wb") as output:
            output.write(value)
            output.flush()
            os.fsync(output.fileno())
        pending.chmod(mode)
        pending.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        pending.unlink(missing_ok=True)


def write_json(path, value):
    atomic(path, (json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n").encode())


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        return default


def command(args, timeout=60, logger=None):
    environment = dict(os.environ, DEBIAN_FRONTEND="noninteractive", APT_LISTCHANGES_FRONTEND="none", LC_ALL="C.UTF-8")
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace", env=environment, start_new_session=True)
    output = []
    def consume():
        for line in process.stdout:
            line = line.strip()
            if line:
                output.append(line[-500:])
                del output[:-50]
                if logger:
                    logger(line[-500:])
    reader = threading.Thread(target=consume, daemon=True)
    reader.start()
    try:
        result = process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        raise UpdateError("系统命令超时，已停止该任务的安装子进程") from None
    finally:
        reader.join(timeout=5)
        process.stdout.close()
    if result:
        raise UpdateError("系统命令失败：" + args[0] + (" · " + output[-1] if output else ""))
    return "\n".join(output)


class Job:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.path = self.folder / "status.json"
        self.lock = threading.Lock()

    def read(self):
        return read_json(self.path)

    def update(self, **values):
        with self.lock:
            data = self.read()
            data.update(values, updated_at=time.time())
            write_json(self.path, data)
        return data

    def log(self, message):
        with self.lock:
            data = self.read()
            data.setdefault("logs", []).append({"time": time.time(), "message": str(message)[:500]})
            data["logs"] = data["logs"][-400:]
            data["updated_at"] = time.time()
            write_json(self.path, data)

    def stage(self, state, progress, message):
        self.update(state=state, progress=progress, message=message)
        self.log(message)


class Host:
    def __init__(self, state=STATE):
        self.state = Path(state)

    def installed(self):
        version = command(["dpkg-query", "-W", "-f=${Version}", "vpn-traffic"]).strip()
        version_tuple(version)
        return version

    def database(self):
        return Path(read_json(ETC / "config.json")["database"])

    def schema(self):
        with sqlite3.connect(self.database().as_uri() + "?mode=ro", uri=True, timeout=15) as db:
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise UpdateError("流量数据库完整性检查失败")
            return db.execute("PRAGMA user_version").fetchone()[0]

    def preflight(self, manifest):
        os_id = re.search(r"^ID=(.+)$", Path("/etc/os-release").read_text(), re.M)
        if not os_id or os_id[1].strip('"') not in ("debian", "ubuntu") or sys.version_info < (3, 10):
            raise UpdateError("更新需要 Debian/Ubuntu 和 Python 3.10 以上版本")
        if shutil.disk_usage(self.state).free < max(128 * 1024**2, manifest["package"]["size"] * 3):
            raise UpdateError("升级需要至少 128 MiB 可用磁盘空间")
        schema = self.schema()
        if not manifest["database"]["min"] <= schema <= manifest["database"]["max"]:
            raise UpdateError("当前数据库格式与目标版本不兼容")
        return schema

    def validate_package(self, path, version):
        for field, expected in (("Package", "vpn-traffic"), ("Version", version), ("Architecture", "all")):
            if command(["dpkg-deb", "--field", str(path), field]).strip() != expected:
                raise UpdateError("安装包的包名、版本或架构不符合签名清单")

    def quiesce(self):
        command(["systemctl", "stop", "vpn-traffic-manager.service"])
        command(["/usr/bin/vpn-traffic", "checkpoint", "--quiet"])

    def backup(self, folder):
        folder.mkdir(mode=0o700, exist_ok=True)
        with sqlite3.connect(self.database().as_uri() + "?mode=ro", uri=True, timeout=15) as source, sqlite3.connect(folder / "traffic.sqlite3") as target:
            source.backup(target)
        (folder / "traffic.sqlite3").chmod(0o600)
        records = []
        for index, value in enumerate(PROTECTED):
            path = Path(value)
            record = {"path": value, "exists": path.exists(), "file": str(index)}
            if path.exists():
                info = path.stat()
                atomic(folder / str(index), path.read_bytes())
                record.update(mode=info.st_mode & 0o777, uid=info.st_uid, gid=info.st_gid)
            records.append(record)
        write_json(folder / "files.json", records)

    def restore_configs(self, folder):
        records = read_json(folder / "files.json")
        if not isinstance(records, list) or [r["path"] for r in records] != PROTECTED:
            raise UpdateError("恢复快照的固定文件清单无效")
        for record in records:
            path = Path(record["path"])
            if record["exists"]:
                atomic(path, (folder / record["file"]).read_bytes(), record["mode"])
                os.chown(path, record["uid"], record["gid"])
            else:
                path.unlink(missing_ok=True)
        command(["systemctl", "daemon-reload"])
        command(["nginx", "-t"])
        command(["systemctl", "reload", "nginx.service"])

    def install(self, path, logger):
        # Only the fixed, already verified application package is passed to APT.
        command(["apt-get", "-o", "DPkg::Lock::Timeout=120", "-o", "Dpkg::Use-Pty=0", "install", "-y", "--reinstall", "--allow-downgrades", "--no-remove", "--no-install-recommends", str(path)], timeout=360, logger=logger)

    def resume_services(self):
        command(["systemctl", "start", *SERVICES])

    def prepare_recovery(self, logger):
        command(["systemctl", "stop", "vpn-traffic-manager.service"])
        try:
            command(["/usr/bin/vpn-traffic", "checkpoint", "--quiet"])
        except UpdateError:
            logger("恢复前尾段采集暂不可用；保留现有数据库，由恢复后的采集器接续统计")

    def health(self, version):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline = time.monotonic() + 50
        while time.monotonic() < deadline:
            try:
                command(["systemctl", "is-active", "--quiet", "xray.service", "nginx.service", *SERVICES], timeout=10)
                status = command(["dpkg-query", "-W", "-f=${Status}", "vpn-traffic"])
                with opener.open("http://127.0.0.1:9081/api/health", timeout=3) as response:
                    healthy = json.load(response)["ok"]
                with opener.open("http://127.0.0.1:9081/api/summary", timeout=3) as response:
                    actual = json.load(response)["version"]
                if healthy and actual == version and "install ok installed" in status and self.installed() == version:
                    self.schema()
                    return True
            except (OSError, ValueError, KeyError, UpdateError):
                pass
            time.sleep(1)
        return False

    def repair(self):
        if "install ok installed" not in command(["dpkg-query", "-W", "-f=${Status}", "vpn-traffic"]):
            command(["dpkg", "--configure", "vpn-traffic"], timeout=120)

    def launch(self, job):
        unit = "vpn-traffic-upgrade-" + job.folder.name
        exists = subprocess.run(["systemctl", "show", unit, "-p", "LoadState", "--value"], capture_output=True, text=True)
        if exists.stdout.strip() == "loaded":
            subprocess.run(["systemctl", "reset-failed", unit], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            command(["systemctl", "start", unit])
            return
        command(["systemd-run", "--unit=" + unit, "--property=Type=exec", "--property=Restart=on-failure", "--property=RestartSec=5", "--property=StartLimitBurst=3", "--property=UMask=0077", "/usr/bin/python3", str(job.folder / "worker/updater.py"), "worker", "--job", job.folder.name])

    def worker_running(self, job):
        result = subprocess.run(["systemctl", "show", "vpn-traffic-upgrade-" + job.folder.name, "-p", "ActiveState", "--value"], capture_output=True, text=True)
        return result.stdout.strip() in ("active", "activating", "reloading")

    def restart_broker(self):
        subprocess.run(["systemctl", "try-restart", "vpn-traffic-updater.service"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


class Engine:
    def __init__(self, state=STATE, run=RUN, key=ETC / "update-signing.pub", host=None, source=None):
        self.state, self.run, self.key = Path(state), Path(run), Path(key)
        self.host = host or Host(self.state)
        self.source = source or ReleaseSource()
        self.lock = threading.Lock()
        for path in (self.state, self.state / "jobs", self.state / "cache"):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)

    @contextmanager
    def control(self):
        with self.lock, (self.state / "control.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def latest_job(self):
        active = read_json(self.state / "active.json", {})
        if active.get("id"):
            return Job(self.state / "jobs" / active["id"]).read()
        return None

    def status(self):
        checked = read_json(self.state / "check.json", {})
        installed = self.host.installed()
        checked.update(installed_version=installed, configured=self.key.is_file(), job=self.latest_job(), maintenance=(self.run / "maintenance").exists())
        candidate = checked.get("candidate")
        checked["available"] = bool(candidate and candidate["expires_at"] > time.time() and version_tuple(candidate["version"]) > version_tuple(installed) and not checked.get("error"))
        return checked

    def check(self):
        with self.control():
            current = self.latest_job()
            if current and current["state"] not in TERMINAL:
                raise UpdateError("已有升级任务正在执行")
            try:
                version = self.source.latest()
                raw = self.source.read(asset_url(version, MANIFEST_NAME), 65536)
                signature = self.source.read(asset_url(version, SIGNATURE_NAME), 64)
                trusted = read_json(self.state / "trusted.json", {})
                manifest = verify_manifest(raw, signature, self.key, expected_version=version, last_seen=trusted.get("published_at", 0))
                installed = self.host.installed()
                if version_tuple(installed) < version_tuple(manifest["minimum_version"]):
                    raise UpdateError("当前版本过旧，需要先完成基础版本迁移")
                self.host.preflight(manifest)
                atomic(self.state / MANIFEST_NAME, raw)
                atomic(self.state / SIGNATURE_NAME, signature)
                write_json(self.state / "trusted.json", {"published_at": manifest["published_at"]})
                checked = {"checked_at": time.time(), "error": "", "candidate": {"version": version, "size": manifest["package"]["size"], "sha256": manifest["package"]["sha256"], "notes": manifest["notes"], "expires_at": manifest["expires_at"], "restart_services": manifest["restart_services"], "release_url": "https://github.com/whoarei/traffic-monitor/releases/tag/v" + version}}
                write_json(self.state / "check.json", checked)
            except (OSError, ValueError, UpdateError) as error:
                checked = read_json(self.state / "check.json", {})
                checked.update(checked_at=time.time(), error=str(error)[:400])
                write_json(self.state / "check.json", checked)
                raise UpdateError(checked["error"]) from None
        return self.status()

    def cached(self, version):
        path = self.state / "cache" / package_name(version)
        receipt = read_json(path.with_suffix(".receipt.json"), {})
        if not path.is_file() or receipt.get("version") != version or not receipt.get("installed") or hashlib.sha256(path.read_bytes()).hexdigest() != receipt.get("sha256"):
            raise UpdateError("缺少可信的已安装版本恢复包，请由管理员先缓存当前安装包")
        self.host.validate_package(path, version)
        return path

    def pin_package(self, path, version):
        target = self.state / "cache" / package_name(version)
        atomic(target, Path(path).read_bytes())
        write_json(target.with_suffix(".receipt.json"), {"version": version, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "installed": True, "cached_at": time.time()})

    def ensure_cached(self, version):
        path = self.state / "cache" / package_name(version)
        if path.exists() or path.with_suffix(".receipt.json").exists():
            return self.cached(version)
        # A manual first installation can fetch its own signed recovery package.
        # Older unsigned releases require an explicit, locally trusted seed.
        raw = self.source.read(asset_url(version, MANIFEST_NAME), 65536)
        signature = self.source.read(asset_url(version, SIGNATURE_NAME), 64)
        manifest = verify_manifest(raw, signature, self.key, expected_version=version)
        download = self.state / "cache" / ("seed-" + package_name(version))
        try:
            asset = manifest["package"]
            self.source.download(asset["url"], download, asset["size"], asset["sha256"])
            seed_installed(self, download, asset["sha256"])
        finally:
            download.unlink(missing_ok=True)
        return self.cached(version)

    def start(self, version, request_id):
        version_tuple(version)
        try:
            identifier = uuid.UUID(request_id).hex
        except (ValueError, AttributeError, TypeError):
            raise UpdateError("升级请求需要有效的唯一操作标识") from None
        with self.control():
            folder = self.state / "jobs" / identifier
            if folder.exists():
                previous = Job(folder).read()
                if previous["target_version"] != version:
                    raise UpdateError("操作标识已经用于其他版本")
                return self.status()
            active = self.latest_job()
            if active and (active["state"] not in TERMINAL or active["state"] == "recovery_required"):
                raise UpdateError("已有升级任务或待处理的恢复任务")
            raw, signature = (self.state / MANIFEST_NAME).read_bytes(), (self.state / SIGNATURE_NAME).read_bytes()
            manifest = verify_manifest(raw, signature, self.key, expected_version=version)
            installed = self.host.installed()
            if version_tuple(version) <= version_tuple(installed) or version_tuple(installed) < version_tuple(manifest["minimum_version"]):
                raise UpdateError("目标版本不是当前可安装的新版本")
            self.host.preflight(manifest)
            self.ensure_cached(installed)
            folder.mkdir(mode=0o700)
            worker = folder / "worker"
            worker.mkdir(mode=0o700)
            for filename in ("updater.py", "update_protocol.py"):
                atomic(worker / filename, (Path(__file__).parent / filename).read_bytes())
            atomic(worker / "trusted.pub", self.key.read_bytes())
            atomic(folder / MANIFEST_NAME, raw)
            atomic(folder / SIGNATURE_NAME, signature)
            write_json(folder / "status.json", {"id": identifier, "target_version": version, "previous_version": installed, "state": "queued", "progress": 0, "message": "升级任务已创建", "created_at": time.time(), "updated_at": time.time(), "installation_started": False, "logs": []})
            descriptor = os.open(self.state / "jobs", os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            write_json(self.state / "active.json", {"id": identifier})
            job = Job(folder)
            try:
                self.host.launch(job)
            except Exception:
                job.stage("failed", 100, "无法启动独立升级任务，现有版本保持运行")
                raise UpdateError("无法启动独立升级服务") from None
        return self.status()

    def maintenance(self, enabled):
        if enabled:
            self.run.mkdir(mode=0o750, parents=True, exist_ok=True)
            atomic(self.run / "maintenance", b"Upgrade in progress\n", 0o644)
        else:
            (self.run / "maintenance").unlink(missing_ok=True)

    def complete(self, job, manifest, path):
        self.verify_package_bytes(path, manifest)
        if self.host.schema() != manifest["database"]["target"]:
            raise UpdateError("升级后的数据库格式与签名清单不一致")
        self.pin_package(path, manifest["version"])
        job.stage("succeeded", 100, "升级完成，服务健康，历史与额度数据保留")
        self.maintenance(False)

    def verify_package_bytes(self, path, manifest):
        asset = manifest["package"]
        path = Path(path)
        if path.stat().st_size != asset["size"] or hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
            raise UpdateError("任务安装包已变化，SHA256 或大小不再匹配签名清单")

    def recover(self, job, manifest, message):
        data = job.read()
        job.stage("rolling_back", 85, "升级未通过验证，正在恢复上一版本")
        try:
            if not manifest["database"]["rollback_compatible"] or self.host.schema() != data["schema_before"]:
                raise UpdateError("数据库格式发生变化，已保留备份，需要管理员处理；未自动回退流量数据")
            previous = self.cached(data["previous_version"])
            self.host.prepare_recovery(job.log)
            self.host.restore_configs(job.folder / "backup")
            self.host.install(previous, job.log)
            self.host.resume_services()
            if not self.host.health(data["previous_version"]):
                raise UpdateError("上一版本的服务健康检查未通过")
            job.stage("rolled_back", 100, "升级失败，已恢复上一版本；当前数据库和已记账流量保留")
            job.update(error=message)
            self.maintenance(False)
        except Exception as error:
            job.stage("recovery_required", 100, "自动恢复未完成，需要管理员处理")
            job.update(error=str(error)[:400])
            job.log(str(error))

    def worker(self, identifier):
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise UpdateError("无效的升级任务标识")
        job = Job(self.state / "jobs" / identifier)
        with (self.state / "installation.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            data = job.read()
            if not data or data["state"] in TERMINAL:
                return
            manifest = None
            try:
                # Running tasks keep their verified bytes and key across package replacement.
                manifest = verify_manifest((job.folder / MANIFEST_NAME).read_bytes(), (job.folder / SIGNATURE_NAME).read_bytes(), job.folder / "worker/trusted.pub", expected_version=data["target_version"], now=data["created_at"] if data["installation_started"] else None)
                package = job.folder / manifest["package"]["name"]
                if data["installation_started"]:
                    self.maintenance(True)
                    job.log("检测到中断的安装任务，正在核对已安装版本")
                    if self.host.installed() == data["target_version"]:
                        try:
                            self.host.repair()
                            self.host.resume_services()
                            if self.host.health(data["target_version"]):
                                self.complete(job, manifest, package)
                                return
                        except Exception:
                            pass
                    self.recover(job, manifest, "升级任务被中断")
                    return
                job.stage("checking", 5, "正在检查兼容性与恢复包")
                schema = self.host.preflight(manifest)
                if self.host.installed() != data["previous_version"]:
                    raise UpdateError("安装版本已被其他操作改变，请重新检查更新")
                self.cached(data["previous_version"])
                job.stage("downloading", 15, "正在下载签名清单指定的安装包")
                asset = manifest["package"]
                self.source.download(asset["url"], package, asset["size"], asset["sha256"])
                self.verify_package_bytes(package, manifest)
                self.host.validate_package(package, manifest["version"])
                job.stage("verified", 40, "签名、大小、SHA256 和包信息均已通过验证")
                self.maintenance(True)
                job.stage("backing_up", 45, "正在保存尾段计数、数据库与配置快照")
                job.update(services_paused=True)
                self.host.quiesce()
                self.host.backup(job.folder / "backup")
                job.update(schema_before=schema, installation_started=True)
                job.stage("installing", 60, "正在安装；网页可能短暂断开，请等待自动重连")
                self.host.install(package, job.log)
                job.stage("health_check", 80, "正在检查版本、数据库、采集和配额服务")
                self.host.resume_services()
                if not self.host.health(manifest["version"]):
                    raise UpdateError("新版本服务健康检查未通过")
                self.complete(job, manifest, package)
            except Exception as error:
                job.log(str(error))
                if job.read()["installation_started"]:
                    if manifest:
                        self.recover(job, manifest, str(error)[:400])
                    else:
                        self.maintenance(True)
                        job.stage("recovery_required", 100, "任务校验失败，已保留安装现场，需要管理员处理")
                        job.update(error=str(error)[:400])
                else:
                    if job.read().get("services_paused"):
                        try:
                            self.host.resume_services()
                        except Exception:
                            pass
                    job.stage("failed", 100, "安装前检查或下载失败，现有版本保留")
                    job.update(error=str(error)[:400])
                    self.maintenance(False)
            finally:
                if job.read()["state"] in TERMINAL:
                    self.host.restart_broker()

    def resume(self):
        data = self.latest_job()
        if not data:
            return
        if data["state"] == "recovery_required":
            self.maintenance(True)
        elif data["state"] in TERMINAL:
            # A power loss can occur after committing the terminal job status
            # and before unlinking its maintenance flag.
            self.maintenance(False)
        elif data["state"] not in TERMINAL:
            job = Job(self.state / "jobs" / data["id"])
            if data["installation_started"] or data.get("services_paused"):
                self.maintenance(True)
            if not self.host.worker_running(job):
                current = job.read()
                if current["state"] in TERMINAL:
                    return
                attempts = current.get("resume_attempts", 0)
                if attempts >= 3:
                    paused = bool(current["installation_started"] or current.get("services_paused"))
                    job.stage("recovery_required" if paused else "failed", 100, "独立任务多次中断，请管理员检查服务日志后重试恢复")
                    self.maintenance(paused)
                    return
                job.update(resume_attempts=attempts + 1)
                self.host.launch(job)


def configure(public_key, service_template):
    public_key, service_template = Path(public_key), Path(service_template)
    group = grp.getgrnam("vpn-traffic").gr_gid
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE.chmod(0o700)
    runtime = STATE / "runtime"
    runtime.mkdir(mode=0o700, exist_ok=True)
    trusted = ETC / "update-signing.pub"
    if trusted.exists() and trusted.read_bytes().strip() != public_key.read_bytes().strip():
        raise UpdateError("已固定的升级公钥不同，需要管理员明确执行密钥轮换")
    atomic(trusted, public_key.read_bytes(), 0o644)
    for name in ("updater.py", "update_protocol.py"):
        atomic(runtime / name, (Path(__file__).parent / name).read_bytes())
    atomic(Path("/etc/systemd/system/vpn-traffic-updater.service"), service_template.read_bytes(), 0o644)
    atomic(Path("/usr/local/sbin/vpn-traffic-updater"), b'#!/bin/sh\nexec /usr/bin/python3 /var/lib/vpn-traffic-updater/runtime/updater.py "$@"\n', 0o755)
    write_json(ETC / "updater.json", {"repository": "whoarei/traffic-monitor", "channel": "stable", "public_key": str(trusted), "state_directory": str(STATE)})
    RUN.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chown(RUN, 0, group)
    RUN.chmod(0o750)
    command(["systemctl", "daemon-reload"])
    command(["systemctl", "enable", "--now", "vpn-traffic-updater.service"])


def seed_installed(engine, path, digest):
    path = Path(path)
    if not re.fullmatch(r"[a-f0-9]{64}", digest) or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise UpdateError("本地恢复包的 SHA256 与管理员提供的可信值不一致")
    version = engine.host.installed()
    engine.host.validate_package(path, version)
    control = subprocess.check_output(["dpkg-deb", "--ctrl-tarfile", str(path)])
    with tarfile.open(fileobj=io.BytesIO(control)) as archive:
        member = next((m for m in archive if m.name.lstrip("./") == "md5sums"), None)
        if not member:
            raise UpdateError("本地恢复包缺少文件校验清单")
        md5sums = archive.extractfile(member).read()
    installed = Path("/var/lib/dpkg/info/vpn-traffic.md5sums").read_bytes()
    if installed.strip() != md5sums.strip():
        raise UpdateError("恢复包与当前已安装包的文件清单不一致")
    for line in md5sums.decode().splitlines():
        checksum, relative = line.split(None, 1)
        if relative.startswith("/") or ".." in Path(relative).parts:
            raise UpdateError("本地恢复包包含非法文件路径")
        if hashlib.md5((Path("/") / relative).read_bytes()).hexdigest() != checksum:
            raise UpdateError("已安装文件被修改，请先核对恢复包与当前版本")
    engine.pin_package(path, version)


def dispatch(engine, request, peer, uid):
    if peer not in (0, uid):
        raise UpdateError("无权调用升级服务")
    if not isinstance(request, dict):
        raise UpdateError("升级请求必须是 JSON 对象")
    action = request.get("action")
    if action == "status" and set(request) == {"action"}:
        return engine.status()
    if action == "check" and set(request) == {"action"}:
        return engine.check()
    if action == "start" and set(request) == {"action", "version", "request_id"}:
        return engine.start(request["version"], request["request_id"])
    raise UpdateError("升级服务仅接受状态、检查和指定版本安装请求")


def serve(engine):
    uid = pwd.getpwnam("vpn-traffic").pw_uid
    group = grp.getgrnam("vpn-traffic").gr_gid
    engine.resume()
    stopping = threading.Event()
    def watch():
        while not stopping.wait(10):
            try:
                with engine.control():
                    engine.resume()
            except Exception:
                pass
    class RPC(socketserver.StreamRequestHandler):
        def handle(self):
            self.request.settimeout(45)
            try:
                peer = struct.unpack("3i", self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))[1]
                if peer not in (0, uid):
                    raise UpdateError("无权调用升级服务")
                raw = self.rfile.readline(8193)
                if len(raw) > 8192 or not raw.endswith(b"\n"):
                    raise UpdateError("升级请求格式无效")
                result = dispatch(engine, json.loads(raw), peer, uid)
                response = {"ok": True, "result": result}
            except (UpdateError, ValueError, KeyError, TypeError) as error:
                response = {"ok": False, "error": str(error)[:400]}
            except Exception:
                response = {"ok": False, "error": "升级服务暂时不可用，请查看服务日志"}
            try:
                self.wfile.write(json.dumps(response, ensure_ascii=False).encode() + b"\n")
            except OSError:
                pass
    class Status(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            if self.path != "/status":
                self.send_error(404)
                return
            try:
                body, status = json.dumps(engine.status(), ensure_ascii=False).encode(), 200
            except Exception:
                body, status = b'{"error":"Update status temporarily unavailable"}', 503
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
    SOCKET.unlink(missing_ok=True)
    class Server(socketserver.ThreadingUnixStreamServer):
        daemon_threads = True
    with Server(str(SOCKET), RPC) as server, ThreadingHTTPServer(("127.0.0.1", 9082), Status) as http:
        os.chown(SOCKET, 0, group)
        SOCKET.chmod(0o660)
        http.daemon_threads = True
        threading.Thread(target=http.serve_forever, daemon=True).start()
        threading.Thread(target=watch, daemon=True).start()
        def stop(*_):
            threading.Thread(target=server.shutdown, daemon=True).start()
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        server.serve_forever(poll_interval=.5)
        stopping.set()
        http.shutdown()
    SOCKET.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["configure", "serve", "worker", "check", "status", "start", "seed", "recover"])
    parser.add_argument("--job")
    parser.add_argument("--version")
    parser.add_argument("--request-id")
    parser.add_argument("--package", type=Path)
    parser.add_argument("--sha256")
    parser.add_argument("--public-key", type=Path, default=Path("/usr/share/vpn-traffic/update-signing.pub"))
    parser.add_argument("--service-template", type=Path, default=Path("/usr/share/vpn-traffic/updater.service"))
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error("Upgrade management requires root")
    try:
        if args.action == "configure":
            configure(args.public_key, args.service_template)
            return
        engine = Engine()
        if args.action == "serve":
            serve(engine)
        elif args.action == "worker":
            engine.worker(args.job or "")
        elif args.action == "seed":
            seed_installed(engine, args.package, args.sha256 or "")
            print("Current installed package verified and cached")
        elif args.action == "recover":
            if not re.fullmatch(r"[a-f0-9]{32}", args.job or ""):
                raise UpdateError("无效的恢复任务标识")
            job = Job(STATE / "jobs" / args.job)
            if not job.read() or job.read()["state"] != "recovery_required":
                raise UpdateError("该任务不需要人工恢复")
            job.update(state="health_check", resume_attempts=0)
            engine.host.launch(job)
            print("Recovery job started")
        elif args.action == "start":
            print(json.dumps(engine.start(args.version, args.request_id or str(uuid.uuid4())), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(engine.check() if args.action == "check" else engine.status(), ensure_ascii=False, indent=2))
    except (UpdateError, OSError, ValueError) as error:
        parser.exit(1, str(error) + "\n")


if __name__ == "__main__":
    main()
