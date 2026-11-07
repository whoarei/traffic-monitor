#!/usr/bin/python3
import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3
import sys

import store


def load_config():
    return json.loads(Path(os.environ.get("VPN_TRAFFIC_CONFIG", "/etc/vpn-traffic/config.json")).read_text())


def main():
    parser = argparse.ArgumentParser(description="VPN per-identity traffic accounting and private dashboard")
    parser.add_argument("command", choices=["status", "show", "history", "export", "collector", "serve", "manager", "checkpoint", "configure", "disable", "backup", "web-password", "schedule-update", "link-create", "link-update", "link-reset", "link-sharing", "link-revoke", "link-rotate", "link-bonus"], nargs="?", default="show")
    parser.add_argument("--from", dest="start")
    parser.add_argument("--to", dest="end")
    parser.add_argument("--device", default="")
    parser.add_argument("--output")
    parser.add_argument("--reset", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--label")
    parser.add_argument("--quota-gib")
    parser.add_argument("--amount-gib")
    parser.add_argument("--reset-day", type=int)
    parser.add_argument("--reset-time")
    enabled = parser.add_mutually_exclusive_group()
    enabled.add_argument("--enable", dest="enabled", action="store_true")
    enabled.add_argument("--disable-link", dest="enabled", action="store_false")
    parser.set_defaults(enabled=None)
    args = parser.parse_args()
    if args.command == "schedule-update":
        if os.geteuid() != 0:parser.error("System settings require root")
        from manager_client import call
        print(json.dumps(call("schedule",reset_day=args.reset_day,reset_time=args.reset_time),ensure_ascii=False,indent=2))
        return
    if args.command.startswith("link-"):
        if os.geteuid() != 0:
            parser.error("Link management requires root")
        from manager_client import call
        from decimal import Decimal
        import uuid
        values = {"id": args.device}
        if args.label is not None: values["label"] = args.label
        if args.quota_gib is not None: values["quota"] = int(Decimal(args.quota_gib) * 1024**3)
        if args.reset_day is not None or args.reset_time is not None:parser.error("Use schedule-update to change the system-wide reset schedule")
        if args.amount_gib is not None:values["amount"] = int(Decimal(args.amount_gib) * 1024**3)
        if args.enabled is not None: values["enabled"] = args.enabled
        values["request_id"] = str(uuid.uuid4())
        print(json.dumps(call(args.command[len("link-"):], **values), ensure_ascii=False, indent=2))
        return
    if args.command in ("configure", "disable", "web-password"):
        if os.geteuid() != 0:
            parser.error("This command requires root")
        import integration
        if args.command == "configure":
            integration.configure()
        elif args.command == "disable":
            integration.disable()
        else:
            print(json.dumps(integration.credentials(reset=args.reset), ensure_ascii=False, indent=2))
        return
    config = load_config()
    if args.command == "collector":
        import collector
        collector.run(config)
    elif args.command == "serve":
        import server
        server.run(config)
    elif args.command == "manager":
        if os.geteuid() != 0:
            parser.error("The manager requires root")
        import manager
        manager.run(config)
    elif args.command == "checkpoint":
        import collector
        if args.quiet and not Path(config["checkpoint_socket"]).exists():
            return
        collector.checkpoint(config, args.quiet)
    elif args.command == "backup":
        if os.geteuid() != 0:
            parser.error("Backup requires root")
        target = Path(args.output or "/var/backups/vpn-traffic/traffic.sqlite3")
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with store.database(config["database"], readonly=True) as source, sqlite3.connect(target) as destination:
            source.backup(destination)
        target.chmod(0o600)
        print(target)
    elif args.command in ("history", "export"):
        today = datetime.now(store.TZ).date()
        result = store.history(config["database"], args.start or (today - timedelta(days=6)).isoformat(), args.end or today.isoformat(), args.device)
        if args.command == "export":
            from server import csv_bytes
            body = csv_bytes(result)
            if args.output:
                target = Path(args.output)
                target.write_bytes(body)
                target.chmod(0o600)
            else:
                sys.stdout.buffer.write(body)
        else:
            print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        state = store.summary(config["database"], config["server"])
        if args.json or args.command == "status":
            print(json.dumps(state, indent=2, ensure_ascii=False))
        else:
            print("DEVICE                UPLOAD          DOWNLOAD          TOTAL       TODAY       MONTH")
            for device in state["devices"]:
                fmt = lambda value: f'{value / 1024**2:.2f} MiB'
                print(f'{device["id"]:20} {fmt(device["up"]):>12} {fmt(device["down"]):>16} {fmt(device["total"]):>14} {fmt(device["today_up"]+device["today_down"]):>12} {fmt(device["month_up"]+device["month_down"]):>12}')
            print("Collector: " + ("healthy" if state["healthy"] else "unavailable / stale"))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as error:
        print("vpn-traffic: " + str(error), file=sys.stderr)
        sys.exit(1)
