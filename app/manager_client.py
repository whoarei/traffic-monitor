import json
import socket
import time

SOCKET = "/run/vpn-traffic-manager/control.sock"


def call(action, **parameters):
    payload = json.dumps({"action": action, **parameters}, ensure_ascii=False).encode() + b"\n"
    deadline = time.monotonic() + 5
    while True:
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(45)
        try:
            client.connect(SOCKET)
            break
        except (FileNotFoundError, ConnectionRefusedError):
            client.close()
            if time.monotonic() >= deadline:
                raise
            time.sleep(.1)
    with client:
        client.settimeout(45)
        client.sendall(payload)
        data = bytearray()
        while b"\n" not in data and len(data) <= 131072:
            part = client.recv(8192)
            if not part:
                break
            data.extend(part)
    result = json.loads(data)
    if not result.get("ok"):
        raise ValueError(result.get("error", "管理操作失败"))
    return result["result"]
