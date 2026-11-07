"""Loopback-only read API; HTTPS authentication is enforced by Nginx."""
import csv
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import hmac
from pathlib import Path
import sqlite3
import secrets
from urllib.parse import parse_qs, urlsplit

import store


def csv_bytes(result):
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["小时区间(北京时间)" if result.get("granularity")=="hour" else "日期(北京时间)", "设备标识", "设备名称", "上传字节", "下载字节", "合计字节"])
    for row in result["rows"]:
        # Prefix spreadsheet formulas in labels imported from external configurations.
        text = lambda x: "'" + x if x.startswith(("=", "+", "-", "@", "\t", "\r", "\n")) else x
        values=(row["up"],row["down"],row["total"]) if row.get("observed",True) else ("","","")
        writer.writerow([row["day"], text(row["email"]), text(row["label"]), *values])
    return out.getvalue().encode("utf-8-sig")


def run(config):
    csrf_token = secrets.token_urlsafe(32)
    expected_origin = "https://" + config["server"] + ":8443"
    files = {"/": ("index.html", "text/html; charset=utf-8"),
             "/app.js": ("app.js", "text/javascript; charset=utf-8"),
             "/style.css": ("style.css", "text/css; charset=utf-8")}

    class Handler(BaseHTTPRequestHandler):
        server_version = "VPN-Traffic"

        def log_message(self, *_):
            pass

        def respond(self, status, body, mime="application/json; charset=utf-8", headers=None):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            request = urlsplit(self.path)
            query = parse_qs(request.query)
            try:
                if request.path in files:
                    filename, mime = files[request.path]
                    return self.respond(200, (Path(config["web_root"]) / filename).read_bytes(), mime)
                if request.path == "/api/summary":
                    result = store.summary(config["database"], config["server"])
                elif request.path == "/api/session":
                    result = {"token": csrf_token}
                elif request.path == "/api/system":
                    result=store.read_system(config["database"])
                elif request.path == "/api/settings":
                    with store.database(config["database"],readonly=True) as db:
                        result=store.schedule(db)
                elif request.path == "/api/updates/status":
                    from updater_client import call
                    result = call("status")
                elif request.path.startswith("/api/links/") and request.path.endswith("/sharing"):
                    from manager_client import call
                    result = call("sharing", id=request.path[len("/api/links/"):-len("/sharing")])
                elif request.path in ("/api/history", "/api/export.csv"):
                    today = datetime.now(store.TZ).date()
                    start = query.get("from", [(today - timedelta(days=6)).isoformat()])[0]
                    end = query.get("to", [today.isoformat()])[0]
                    device = query.get("device", [""])[0]
                    view=query.get("view",["daily"])[0]
                    if view not in ("daily","hourly"):raise ValueError("未知历史视图")
                    result = store.hourly_history(config["database"],device) if view=="hourly" else store.history(config["database"], start, end, device)
                    if request.path.endswith(".csv"):
                        return self.respond(200, csv_bytes(result), "text/csv; charset=utf-8",
                                            {"Content-Disposition": 'attachment; filename="vpn-traffic-' + ('last-24-hours' if view=='hourly' else start+'-'+end) + '.csv"'})
                elif request.path == "/api/health":
                    state = store.summary(config["database"], config["server"])
                    from manager_client import SOCKET
                    manager_ok = bool(state["manager_healthy"] and Path(SOCKET).is_socket())
                    result = {"ok": bool(state["healthy"] and manager_ok), "last_sample": state["last_sample"], "manager_ok": manager_ok}
                    return self.respond(200 if result["ok"] else 503, json.dumps(result).encode())
                else:
                    return self.respond(404, b'{"error":"Not found"}')
                self.respond(200, json.dumps(result, ensure_ascii=False).encode("utf-8"))
            except ValueError as error:
                self.respond(400, json.dumps({"error": str(error)}, ensure_ascii=False).encode("utf-8"))
            except (OSError, RuntimeError, sqlite3.Error):
                self.respond(503, json.dumps({"error": "统计数据暂时不可用，请稍后重试"}, ensure_ascii=False).encode("utf-8"))

        do_HEAD = do_GET

        def do_POST(self):
            if self.headers.get("Origin") != expected_origin or not hmac.compare_digest(self.headers.get("X-VPN-CSRF", "").encode(), csrf_token.encode()):
                return self.respond(403, b'{"error":"Invalid request origin or token"}')
            if self.headers.get_content_type() != "application/json":
                return self.respond(415, b'{"error":"JSON required"}')
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 16384:
                    return self.respond(413, b'{"error":"Invalid request size"}')
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict):
                    raise ValueError("请求格式不正确")
                route = urlsplit(self.path).path
                if route in ("/api/updates/check", "/api/updates/start"):
                    from updater_client import call
                    result = call("check") if route.endswith("/check") else call("start", version=payload.get("version"), request_id=payload.get("request_id"))
                    return self.respond(200, json.dumps(result, ensure_ascii=False).encode())
                from updater_client import maintenance
                if maintenance():
                    return self.respond(423, json.dumps({"error":"系统正在升级，连接管理暂时锁定"}, ensure_ascii=False).encode())
                from manager_client import call
                if route == "/api/settings":
                    result=call("schedule",reset_day=payload.get("reset_day"),reset_time=payload.get("reset_time"))
                elif route == "/api/links":
                    result = call("create", **{k:v for k,v in payload.items() if k in ("label","quota","reset_day","reset_time","enabled","request_id")})
                elif route.startswith("/api/links/"):
                    parts = route[len("/api/links/"):].split("/")
                    if len(parts) != 2 or parts[1] not in ("update", "reset", "rotate", "revoke", "bonus"):
                        return self.respond(404, b'{"error":"Not found"}')
                    result = call(parts[1], id=parts[0], **{k:v for k,v in payload.items() if k in ("label","quota","reset_day","reset_time","enabled","amount","request_id")})
                else:
                    return self.respond(404, b'{"error":"Not found"}')
                self.respond(200, json.dumps(result, ensure_ascii=False).encode())
            except (ValueError, TypeError) as error:
                self.respond(400, json.dumps({"error": str(error)}, ensure_ascii=False).encode())
            except (OSError, RuntimeError):
                self.respond(503, json.dumps({"error": "管理服务暂时不可用，请刷新列表确认状态"}, ensure_ascii=False).encode())

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        def get_request(self):
            sock, address = super().get_request()
            sock.settimeout(15)
            return sock, address

    address, port = config["web_listen"].rsplit(":", 1)
    if address != "127.0.0.1":
        raise RuntimeError("The web service must listen on loopback")
    Server((address, int(port)), Handler).serve_forever()
