"""Restricted client for the independent upgrade broker."""
import json
from pathlib import Path
import socket

SOCKET = "/run/vpn-traffic-updater/control.sock"


def maintenance():
    return Path("/run/vpn-traffic-updater/maintenance").exists()


def call(action, **parameters):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(85)
        client.connect(SOCKET)
        client.sendall(json.dumps({"action": action, **parameters}).encode() + b"\n")
        chunks = bytearray()
        while b"\n" not in chunks and len(chunks) <= 1024 * 1024:
            block = client.recv(16384)
            if not block:
                break
            chunks.extend(block)
    if len(chunks) > 1024 * 1024:
        raise RuntimeError("升级服务响应过大")
    result = json.loads(chunks)
    if not result.get("ok"):
        raise ValueError(result.get("error", "升级请求失败"))
    return result["result"]
