"""Single writer with a checkpoint socket for normal Xray shutdowns."""
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import time

import store


def generation():
    pid = subprocess.check_output(["systemctl", "show", "xray.service", "-p", "MainPID", "--value"], text=True, timeout=5).strip()
    if not pid.isdigit() or int(pid) < 1:
        raise RuntimeError("Xray is not running")
    proc = Path("/proc") / pid / "stat"
    start = proc.read_text().rsplit(")", 1)[1].split()[19]
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip() + ":" + pid + ":" + start


def read_counters(config):
    before = generation()
    result = subprocess.run([config["xray_binary"], "api", "statsquery", "--server=" + config["stats_address"], "-pattern", "user>>>"],
                            capture_output=True, text=True, timeout=8)
    if result.returncode:
        raise RuntimeError("Xray statistics API is unavailable")
    payload = json.loads(result.stdout)
    after = generation()
    if after != before:
        raise RuntimeError("Xray restarted during sampling; retrying on next interval")
    counters = {}
    for item in payload.get("stat", []):
        parts = item["name"].split(">>>")
        if len(parts) == 4 and parts[0] == "user" and parts[2] == "traffic" and parts[3] in ("uplink", "downlink"):
            counters.setdefault(parts[1], {})[parts[3]] = int(item.get("value", 0))
    return after, counters


def sample(config):
    after, counters = read_counters(config)
    store.record(config["database"], counters, after)


def checkpoint(config, quiet=False):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(8)
        client.connect(config["checkpoint_socket"])
        client.sendall(b"collect\n")
        reply = json.loads(client.recv(4096))
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error", "Checkpoint failed"))
    if not quiet:
        print("Traffic counters saved")


def run(config):
    from system_info import Sampler
    resources=Sampler()
    next_system=0
    running = True
    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    path = Path(config["checkpoint_socket"])
    path.unlink(missing_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
        path.chmod(0o600)
        server.listen(4)
        next_sample = 0
        def collect():
            try:
                sample(config)
                return {"ok": True}
            except Exception as error:
                message = str(error)
                store.record_error(config["database"], message)
                print("Collection failed: " + message, flush=True)
                return {"ok": False, "error": message}
        while running:
            if time.monotonic() >= next_system:
                try:
                    store.write_system(config["database"],resources.sample())
                except Exception as error:
                    print("Resource metrics unavailable: "+str(error),flush=True)
                next_system=time.monotonic()+5
            if time.monotonic() >= next_sample:
                collect()
                next_sample = time.monotonic() + config["interval"]
            if select.select([server], [], [], min(1, max(0, next_sample - time.monotonic())))[0]:
                with server.accept()[0] as client:
                    client.settimeout(1)
                    try:
                        request = client.recv(64)
                        reply = collect() if request == b"collect\n" else {"ok": False, "error": "Invalid request"}
                        client.sendall(json.dumps(reply).encode())
                    except (OSError, ValueError):
                        pass
        collect()
    path.unlink(missing_ok=True)
