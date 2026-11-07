"""Create and sign update metadata for an already tested release package."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
from update_protocol import REPOSITORY, SERVICES, asset_url, package_name, verify_manifest, version_tuple


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--public-key", type=Path, default=ROOT / "packaging/update-signing.pub")
    args = parser.parse_args()
    version_tuple(args.version)
    public = subprocess.check_output(["openssl", "pkey", "-in", str(args.private_key), "-pubout"])
    if public.strip() != args.public_key.read_bytes().strip():
        raise SystemExit("Signing key does not match the pinned public key")
    package = args.directory / package_name(args.version)
    for field, expected in (("Package", "vpn-traffic"), ("Version", args.version), ("Architecture", "all")):
        if subprocess.check_output(["dpkg-deb", "--field", str(package), field], text=True).strip() != expected:
            raise SystemExit("Unexpected Debian package metadata: " + field)
    now = int(time.time())
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    start = changelog.find("## " + args.version + " ")
    if start < 0:
        raise SystemExit("Missing CHANGELOG entry for this version")
    notes = changelog[start:].split("\n## ", 1)[0].strip()
    data = {"format": 1, "repository": REPOSITORY, "channel": "stable", "version": args.version, "minimum_version": "1.3.0", "published_at": now, "expires_at": now + 365 * 86400,
            "package": {"name": package.name, "size": package.stat().st_size, "sha256": hashlib.sha256(package.read_bytes()).hexdigest(), "url": asset_url(args.version, package.name)},
            "database": {"min": 3, "max": 3, "target": 3, "rollback_compatible": True}, "restart_services": SERVICES, "notes": notes}
    raw = (json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    manifest, signature = args.directory / "update-manifest.json", args.directory / "update-manifest.sig"
    manifest.write_bytes(raw)
    subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(args.private_key), "-rawin", "-in", str(manifest), "-out", str(signature)], check=True)
    verify_manifest(raw, signature.read_bytes(), args.public_key, expected_version=args.version)
    checksums = args.directory / "SHA256SUMS"
    with checksums.open("a", encoding="utf-8") as output:
        for path in (manifest, signature):
            output.write(hashlib.sha256(path.read_bytes()).hexdigest() + "  " + path.name + "\n")
    print("Signed and verified update manifest for " + args.version)


if __name__ == "__main__":
    main()
