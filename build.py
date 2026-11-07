"""Build an architecture-independent .deb on Debian/Ubuntu; includes no credentials."""
import argparse
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile

VERSION = "1.3.0"
ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vpn-traffic-build-") as temp:
        stage = Path(temp) / "vpn-traffic"
        def put(relative, source=None, content=None, mode=0o644):
            target = stage / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes() if source else content.encode())
            target.chmod(mode)
        for path in sorted((ROOT / "app").glob("*.py")):
            put("usr/lib/vpn-traffic/" + path.name, source=path)
        for path in sorted((ROOT / "web").iterdir()):
            if path.is_file():
                put("usr/share/vpn-traffic/web/" + path.name, source=path)
        for name in ("vpn-traffic-collector.service", "vpn-traffic-manager.service", "vpn-traffic-web.service"):
            put("usr/lib/systemd/system/" + name, source=ROOT / "packaging" / name)
        put("usr/share/vpn-traffic/nginx.conf", source=ROOT / "packaging/nginx.conf")
        put("usr/share/doc/vpn-traffic/README.md", source=ROOT / "README.md")
        put("usr/bin/vpn-traffic", content='#!/bin/sh\nexec /usr/bin/python3 /usr/lib/vpn-traffic/main.py "$@"\n', mode=0o755)
        for name in ("postinst", "prerm", "postrm"):
            put("DEBIAN/" + name, source=ROOT / "packaging" / name, mode=0o755)
        size = sum(p.stat().st_size for p in stage.rglob("*") if p.is_file()) // 1024 + 1
        put("DEBIAN/control", content=f'''Package: vpn-traffic
Version: {VERSION}
Section: net
Priority: optional
Architecture: all
Maintainer: Local Administrator <root@localhost>
Depends: python3 (>= 3.10), nginx, openssl, adduser, systemd, tzdata
Installed-Size: {size}
Description: Private per-identity Xray traffic history and dashboard
 Collects upload/download counters every 30 seconds, persists history in
 SQLite, and serves a password-protected HTTPS management dashboard.
 Creates links and enforces monthly quotas with a system-wide reset schedule.
 Supports temporary allowances that expire at the next system reset.
 Integrates with the existing Xray and HTTPS subscription deployment.
''')
        checksums=[]
        for path in sorted(stage.rglob("*")):
            if path.is_file() and "DEBIAN" not in path.relative_to(stage).parts:
                checksums.append(hashlib.md5(path.read_bytes()).hexdigest() + "  " + path.relative_to(stage).as_posix())
        put("DEBIAN/md5sums", content="\n".join(checksums)+"\n")
        for directory in [stage] + [p for p in stage.rglob("*") if p.is_dir()]:
            directory.chmod(0o755)
        output = args.output.resolve() / f"vpn-traffic_{VERSION}_all.deb"
        subprocess.run(["dpkg-deb", "--root-owner-group", "--build", str(stage), str(output)], check=True)
        digest=hashlib.sha256(output.read_bytes()).hexdigest()
        output.with_suffix(".deb.sha256").write_text(digest+"  "+output.name+"\n")
        print(output)


if __name__ == "__main__":
    main()
