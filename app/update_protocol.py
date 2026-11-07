"""Signed, bounded update metadata and HTTPS downloads. No privileged operations."""
import hashlib
import json
from pathlib import Path
import re
import ssl
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

REPOSITORY = "whoarei/traffic-monitor"
API = "https://api.github.com/repos/" + REPOSITORY
MANIFEST_NAME = "update-manifest.json"
SIGNATURE_NAME = "update-manifest.sig"
MAX_PACKAGE = 64 * 1024 * 1024
SERVICES = ["vpn-traffic-web.service", "vpn-traffic-manager.service", "vpn-traffic-collector.service"]


class UpdateError(RuntimeError):
    pass


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", value):
        raise UpdateError("版本号必须为主版本.次版本.修订版本")
    result = tuple(map(int, value.split(".")))
    if max(result) > 999999:
        raise UpdateError("版本号过大")
    return result


def package_name(version):
    version_tuple(version)
    return "vpn-traffic_" + version + "_all.deb"


def asset_url(version, name):
    version_tuple(version)
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", name):
        raise UpdateError("无效的发布附件名称")
    return "https://github.com/" + REPOSITORY + "/releases/download/v" + version + "/" + name


def remote_url(value):
    try:
        parsed = urllib.parse.urlsplit(value)
        port = parsed.port
    except (ValueError, TypeError):
        raise UpdateError("无效的更新下载地址") from None
    host = parsed.hostname or ""
    allowed = host in ("api.github.com", "github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com", "github-releases.githubusercontent.com")
    if parsed.scheme != "https" or not allowed or parsed.username or parsed.password or port not in (None, 443) or parsed.fragment:
        raise UpdateError("更新文件只能从受信任的 GitHub HTTPS 地址下载")
    return value


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        remote_url(newurl)
        return super().redirect_request(request, response, code, message, headers, newurl)


class ReleaseSource:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), SafeRedirect(), urllib.request.HTTPSHandler(context=ssl.create_default_context()))

    def open(self, url):
        remote_url(url)
        request = urllib.request.Request(url, headers={"User-Agent": "vpn-traffic-updater/1", "Accept": "application/vnd.github+json" if url.startswith(API) else "application/octet-stream"})
        try:
            return self.opener.open(request, timeout=25)
        except urllib.error.HTTPError as error:
            raise UpdateError("GitHub 下载失败，HTTP " + str(error.code)) from None
        except (OSError, urllib.error.URLError):
            raise UpdateError("无法连接 GitHub，请检查网络后重试") from None

    def read(self, url, limit):
        with self.open(url) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise UpdateError("更新元数据超过大小限制")
        return data

    def latest(self):
        try:
            release = json.loads(self.read(API + "/releases/latest", 1024 * 1024))
            tag = release["tag_name"]
            if release.get("draft") or release.get("prerelease") or not tag.startswith("v"):
                raise ValueError()
            version_tuple(tag[1:])
            return tag[1:]
        except (ValueError, KeyError, TypeError):
            raise UpdateError("GitHub 稳定版本信息无效") from None

    def download(self, url, target, size, digest):
        target = Path(target)
        pending = target.with_suffix(".partial")
        count, checksum = 0, hashlib.sha256()
        try:
            with self.open(url) as response, pending.open("wb") as destination:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    count += len(chunk)
                    if count > size:
                        raise UpdateError("下载文件超过清单声明的大小")
                    checksum.update(chunk)
                    destination.write(chunk)
            if count != size or checksum.hexdigest() != digest:
                raise UpdateError("安装包大小或 SHA256 校验失败")
            pending.chmod(0o600)
            pending.replace(target)
        finally:
            pending.unlink(missing_ok=True)


def verify_signature(raw, signature, public_key):
    if not 0 < len(raw) <= 65536 or len(signature) != 64:
        raise UpdateError("签名清单或签名长度无效")
    with tempfile.TemporaryDirectory(prefix="vpn-update-signature-") as folder:
        message, sig = Path(folder) / "manifest", Path(folder) / "signature"
        message.write_bytes(raw)
        sig.write_bytes(signature)
        result = subprocess.run(["openssl", "pkeyutl", "-verify", "-pubin", "-inkey", str(public_key), "-rawin", "-in", str(message), "-sigfile", str(sig)], capture_output=True, timeout=10)
    if result.returncode:
        raise UpdateError("更新清单签名无效，未执行安装")


def verify_manifest(raw, signature, public_key, now=None, expected_version=None, last_seen=0):
    verify_signature(raw, signature, public_key)
    try:
        data = json.loads(raw)
        required = {"format", "repository", "channel", "version", "minimum_version", "published_at", "expires_at", "package", "database", "restart_services", "notes"}
        if not isinstance(data, dict) or set(data) != required or type(data["format"]) is not int or data["format"] != 1 or data["repository"] != REPOSITORY or data["channel"] != "stable":
            raise ValueError()
        version = data["version"]
        if version_tuple(data["minimum_version"]) > version_tuple(version) or expected_version is not None and version != expected_version:
            raise ValueError()
        now = time.time() if now is None else now
        issued, expires = data["published_at"], data["expires_at"]
        if type(issued) is not int or type(expires) is not int or issued > now + 600 or issued < last_seen or not issued < expires <= issued + 366 * 86400 or expires <= now:
            raise UpdateError("更新清单已过期、时间异常或早于已信任的清单")
        package = data["package"]
        if set(package) != {"name", "size", "sha256", "url"} or package["name"] != package_name(version) or type(package["size"]) is not int or not 0 < package["size"] <= MAX_PACKAGE or not re.fullmatch(r"[a-f0-9]{64}", package["sha256"]) or package["url"] != asset_url(version, package["name"]):
            raise ValueError()
        schema = data["database"]
        if set(schema) != {"min", "max", "target", "rollback_compatible"} or any(type(schema[k]) is not int or not 0 <= schema[k] <= 1000 for k in ("min", "max", "target")) or not schema["min"] <= schema["target"] <= schema["max"] or type(schema["rollback_compatible"]) is not bool:
            raise ValueError()
        if data["restart_services"] != SERVICES or not isinstance(data["notes"], str) or len(data["notes"]) > 12000:
            raise ValueError()
        return data
    except UpdateError:
        raise
    except (ValueError, KeyError, TypeError, AttributeError):
        raise UpdateError("签名清单格式或兼容性字段无效") from None
