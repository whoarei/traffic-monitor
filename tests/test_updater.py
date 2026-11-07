"""Upgrade fault injection using real signatures/SQLite and isolated fake APT hosts."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import update_protocol as protocol
import updater


class Source:
    def __init__(self, raw, signature):
        self.raw, self.signature, self.fail = raw, signature, False
        self.package = b"tested-new-package"
    def latest(self): return "1.5.0"
    def read(self, url, limit): return self.signature if url.endswith(".sig") else self.raw
    def download(self, url, target, size, digest):
        if self.fail: raise protocol.UpdateError("Simulated interrupted download")
        Path(target).write_bytes(self.package)


class FakeHost:
    def __init__(self, root):
        self.version, self.calls, self.bad_target, self.bad_old = "1.4.0", [], False, False
        self.fail_install, self.target_schema, self.alive = False, 3, False
        self.db = root / "traffic.sqlite3"
        with sqlite3.connect(self.db) as db:
            db.executescript("PRAGMA user_version=3; CREATE TABLE usage(value INTEGER); INSERT INTO usage VALUES(100);")
    def installed(self): return self.version
    def schema(self):
        with sqlite3.connect(self.db) as db: return db.execute("PRAGMA user_version").fetchone()[0]
    def used(self):
        with sqlite3.connect(self.db) as db: return db.execute("SELECT value FROM usage").fetchone()[0]
    def preflight(self, manifest):
        if not manifest["database"]["min"] <= self.schema() <= manifest["database"]["max"]:
            raise protocol.UpdateError("Incompatible database")
        return self.schema()
    def validate_package(self, path, version):
        expected = b"tested-new-package" if version == "1.5.0" else b"trusted-old-package"
        if Path(path).read_bytes() != expected: raise protocol.UpdateError("Wrong package metadata")
    def launch(self, job): self.calls.append(("launch", job.folder.name))
    def worker_running(self, job): return self.alive
    def quiesce(self): self.calls.append(("pause",))
    def prepare_recovery(self, logger): self.calls.append(("prepare_recovery",))
    def backup(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db) as source, sqlite3.connect(folder / "traffic.sqlite3") as target: source.backup(target)
        self.calls.append(("backup",))
    def restore_configs(self, folder):
        assert (folder / "traffic.sqlite3").exists()
        self.calls.append(("restore_configs",))
    def install(self, path, logger):
        self.version = "1.5.0" if "1.5.0" in Path(path).name else "1.4.0"
        self.calls.append(("install", self.version))
        if self.version == "1.5.0":
            with sqlite3.connect(self.db) as db:
                db.execute("UPDATE usage SET value=value+7")
                db.execute("PRAGMA user_version=" + str(self.target_schema))
            if self.fail_install: raise protocol.UpdateError("Simulated APT failure")
    def resume_services(self): self.calls.append(("resume",))
    def health(self, version): return self.version == version and not (self.bad_target if version == "1.5.0" else self.bad_old)
    def repair(self): self.calls.append(("repair",))
    def restart_broker(self): self.calls.append(("restart_broker",))


class UpdaterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = tempfile.TemporaryDirectory()
        cls.private, cls.public = Path(cls.keys.name) / "private.pem", Path(cls.keys.name) / "public.pub"
        subprocess.run(["openssl", "genpkey", "-algorithm", "ED25519", "-out", str(cls.private)], check=True, capture_output=True)
        subprocess.run(["openssl", "pkey", "-in", str(cls.private), "-pubout", "-out", str(cls.public)], check=True, capture_output=True)
    @classmethod
    def tearDownClass(cls): cls.keys.cleanup()
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.host = FakeHost(self.root)
        now = int(time.time())
        self.manifest = {"format":1,"repository":protocol.REPOSITORY,"channel":"stable","version":"1.5.0","minimum_version":"1.3.0","published_at":now,"expires_at":now+86400,"package":{"name":protocol.package_name("1.5.0"),"size":len(b"tested-new-package"),"sha256":hashlib.sha256(b"tested-new-package").hexdigest(),"url":protocol.asset_url("1.5.0",protocol.package_name("1.5.0"))},"database":{"min":3,"max":3,"target":3,"rollback_compatible":True},"restart_services":protocol.SERVICES,"notes":"Test release"}
        self.source = Source(*self.sign(self.manifest))
        self.engine = updater.Engine(self.root / "state", self.root / "run", self.public, self.host, self.source)
        old = self.root / "old.deb";old.write_bytes(b"trusted-old-package")
        self.engine.pin_package(old,"1.4.0")
    def tearDown(self): self.temp.cleanup()
    def sign(self, manifest):
        message, signature = self.root / "signed.json", self.root / "signed.sig"
        raw=json.dumps(manifest,sort_keys=True).encode();message.write_bytes(raw)
        subprocess.run(["openssl","pkeyutl","-sign","-rawin","-inkey",str(self.private),"-in",str(message),"-out",str(signature)],check=True,capture_output=True)
        return raw,signature.read_bytes()
    def set_manifest(self, **fields):
        self.manifest.update(fields);self.source.raw,self.source.signature=self.sign(self.manifest)
    def start(self):
        self.engine.check();result=self.engine.start("1.5.0",str(uuid.uuid4()));return updater.Job(self.engine.state / "jobs" / result["job"]["id"])

    def test_valid_signed_release_available(self):
        self.assertTrue(self.engine.check()["available"])
    def test_tampered_signed_metadata_rejected(self):
        self.source.raw=self.source.raw.replace(b"Test release",b"Bad release")
        with self.assertRaises(protocol.UpdateError):self.engine.check()
        self.assertFalse(self.engine.status()["available"])
    def test_signature_wrong_length_rejected(self):
        self.source.signature=b"bad"
        with self.assertRaises(protocol.UpdateError):self.engine.check()
    def test_expired_manifest_rejected(self):
        self.set_manifest(published_at=int(time.time())-100,expires_at=int(time.time())-1)
        with self.assertRaises(protocol.UpdateError):self.engine.check()
    def test_future_manifest_rejected(self):
        now=int(time.time());self.set_manifest(published_at=now+3600,expires_at=now+7200)
        with self.assertRaises(protocol.UpdateError):self.engine.check()
    def test_signed_replay_rejected(self):
        updater.write_json(self.engine.state / "trusted.json",{"published_at":self.manifest["published_at"]+1})
        with self.assertRaises(protocol.UpdateError):self.engine.check()
    def test_signed_arbitrary_package_url_rejected(self):
        self.manifest["package"]["url"]="https://example.invalid/payload.deb";self.set_manifest()
        with self.assertRaises(protocol.UpdateError):self.engine.check()
    def test_wrong_repository_or_package_name_rejected(self):
        for field,value in [("repository","other/project"),("channel","beta")]:
            bad=copy.deepcopy(self.manifest);bad[field]=value;raw,sig=self.sign(bad)
            with self.assertRaises(protocol.UpdateError):protocol.verify_manifest(raw,sig,self.public)
    def test_incompatible_schema_blocks_before_job(self):
        with sqlite3.connect(self.host.db) as db:db.execute("PRAGMA user_version=4")
        with self.assertRaises(protocol.UpdateError):self.engine.check()
        self.assertEqual(self.host.calls,[])
    def test_signed_older_version_never_installs(self):
        self.host.version="1.6.0";self.assertFalse(self.engine.check()["available"])
        with self.assertRaises(protocol.UpdateError):self.engine.start("1.5.0",str(uuid.uuid4()))
    def test_duplicate_request_is_idempotent(self):
        self.engine.check();request=str(uuid.uuid4());first=self.engine.start("1.5.0",request);second=self.engine.start("1.5.0",request)
        self.assertEqual(first["job"]["id"],second["job"]["id"])
        self.assertEqual(sum(call[0]=="launch" for call in self.host.calls),1)
        with self.assertRaises(protocol.UpdateError):self.engine.start("1.6.0",request)
    def test_simultaneous_process_clients_only_queue_one_job(self):
        self.engine.check();other=updater.Engine(self.engine.state,self.engine.run,self.public,self.host,self.source)
        results=[]
        def start(engine):
            try:engine.start("1.5.0",str(uuid.uuid4()));results.append("started")
            except protocol.UpdateError:results.append("busy")
        threads=[threading.Thread(target=start,args=(e,)) for e in (self.engine,other)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertCountEqual(results,["started","busy"])
    def test_untrusted_old_cache_blocks_install(self):
        self.engine.check();(self.engine.state / "cache" / protocol.package_name("1.4.0")).write_bytes(b"changed")
        with self.assertRaises(protocol.UpdateError):self.engine.start("1.5.0",str(uuid.uuid4()))
        self.assertEqual(self.host.calls,[])
    def test_invalid_version_and_job_paths_rejected(self):
        for value in ("../1.5.0","1.5.0;echo BAD","v1.5.0"):
            with self.assertRaises(protocol.UpdateError):self.engine.start(value,str(uuid.uuid4()))
        with self.assertRaises(protocol.UpdateError):self.engine.worker("../escape")
    def test_download_failure_does_not_stop_services(self):
        job=self.start();self.source.fail=True;self.engine.worker(job.folder.name)
        self.assertEqual(job.read()["state"],"failed");self.assertEqual(self.host.installed(),"1.4.0")
        self.assertFalse(any(call[0] in ("pause","install","resume") for call in self.host.calls))
        self.assertFalse(self.engine.status()["maintenance"])
    def test_successful_upgrade_preserves_database(self):
        job=self.start();self.engine.worker(job.folder.name)
        self.assertEqual(job.read()["state"],"succeeded");self.assertEqual(self.host.used(),107)
        self.assertTrue((job.folder / "backup/traffic.sqlite3").exists());self.assertFalse(self.engine.status()["maintenance"])
        self.assertEqual(self.engine.cached("1.5.0").read_bytes(),b"tested-new-package")
    def test_health_failure_rolls_back_code_without_rewinding_usage(self):
        job=self.start();self.host.bad_target=True;self.engine.worker(job.folder.name)
        self.assertEqual(job.read()["state"],"rolled_back");self.assertEqual(self.host.installed(),"1.4.0");self.assertEqual(self.host.used(),107)
        self.assertIn(("restore_configs",),self.host.calls);self.assertFalse(self.engine.status()["maintenance"])
    def test_install_failure_rolls_back(self):
        job=self.start();self.host.fail_install=True;self.engine.worker(job.folder.name)
        self.assertEqual(job.read()["state"],"rolled_back");self.assertEqual(self.host.used(),107)
    def test_schema_change_requires_manual_recovery_and_keeps_data(self):
        job=self.start();self.host.target_schema=4;self.engine.worker(job.folder.name)
        self.assertEqual(job.read()["state"],"recovery_required");self.assertEqual(self.host.schema(),4);self.assertEqual(self.host.used(),107)
        self.assertNotIn(("restore_configs",),self.host.calls);self.assertTrue(self.engine.status()["maintenance"])
    def test_failed_rollback_keeps_maintenance_lock(self):
        job=self.start();self.host.bad_target=True;self.host.bad_old=True;self.engine.worker(job.folder.name)
        self.assertEqual(job.read()["state"],"recovery_required");self.assertTrue(self.engine.status()["maintenance"])
    def test_interrupted_installed_job_finishes_without_second_install(self):
        job=self.start();self.host.backup(job.folder / "backup");package=job.folder / protocol.package_name("1.5.0");package.write_bytes(b"tested-new-package")
        self.host.install(package,job.log);job.update(installation_started=True,schema_before=3,state="installing")
        self.engine.worker(job.folder.name)
        self.assertEqual(job.read()["state"],"succeeded");self.assertEqual(self.host.used(),107)
        self.assertEqual(self.host.calls.count(("install","1.5.0")),1)
    def test_interrupted_failed_job_recovers_previous_version(self):
        job=self.start();self.host.backup(job.folder / "backup");package=job.folder / protocol.package_name("1.5.0");package.write_bytes(b"tested-new-package")
        self.host.install(package,job.log);self.host.bad_target=True;job.update(installation_started=True,schema_before=3,state="installing")
        self.engine.worker(job.folder.name)
        self.assertEqual(job.read()["state"],"rolled_back");self.assertEqual(self.host.used(),107)
    def test_lost_worker_is_resumed_from_persistent_state(self):
        job=self.start();before=len(self.host.calls);self.engine.resume()
        self.assertGreater(len(self.host.calls),before);self.assertEqual(job.read()["resume_attempts"],1)
    def test_repeated_interruptions_after_pausing_keep_maintenance_for_recovery(self):
        job=self.start();job.update(services_paused=True,resume_attempts=3,state="backing_up")
        self.engine.resume()
        self.assertEqual(job.read()["state"],"recovery_required")
        self.assertTrue((self.engine.run / "maintenance").exists())
    def test_terminal_job_is_not_reexecuted(self):
        job=self.start();self.engine.worker(job.folder.name);before=len(self.host.calls);self.engine.worker(job.folder.name);self.engine.resume()
        self.assertEqual(len(self.host.calls),before)
    def test_download_hash_and_size_are_checked_before_rename(self):
        source=protocol.ReleaseSource();target=self.root / "download.deb"
        for data,size,digest in [(b"BAD",3,hashlib.sha256(b"OK!").hexdigest()),(b"TOO-LONG",2,hashlib.sha256(b"TO").hexdigest())]:
            with patch.object(source,"open",return_value=io.BytesIO(data)):
                with self.assertRaises(protocol.UpdateError):source.download("https://github.com/test",target,size,digest)
            self.assertFalse(target.exists());self.assertFalse(target.with_suffix(".partial").exists())
        with patch.object(source,"open",return_value=io.BytesIO(b"OK")):source.download("https://github.com/test",target,2,hashlib.sha256(b"OK").hexdigest())
        self.assertEqual(target.read_bytes(),b"OK")
    def test_http_and_external_redirect_targets_rejected(self):
        for url in ("http://github.com/file","https://127.0.0.1/private","https://github.com.evil.invalid/file","https://user:password@github.com/file","https://github.com:8443/file"):
            with self.assertRaises(protocol.UpdateError):protocol.remote_url(url)
    def test_management_mutations_locked_during_maintenance(self):
        import manager
        with patch("updater_client.maintenance",return_value=True):
            with self.assertRaisesRegex(ValueError,"升级"):manager.Manager({}).handle({"action":"create"})

    def test_rpc_rejects_other_uids_and_arbitrary_commands(self):
        for request,peer in [({"action":"status"},99999),({"action":"exec","command":"id"},0),({"action":"start","version":"1.5.0","request_id":str(uuid.uuid4()),"url":"https://evil.invalid"},0),([],0)]:
            with self.assertRaises(protocol.UpdateError):updater.dispatch(self.engine,request,peer,1234)
        self.assertEqual(updater.dispatch(self.engine,{"action":"status"},1234,1234)["installed_version"],"1.4.0")
    def test_expiry_is_checked_again_when_starting(self):
        self.engine.check()
        with patch("update_protocol.time.time",return_value=self.manifest["expires_at"]+1):
            self.assertFalse(self.engine.status()["available"])
            with self.assertRaises(protocol.UpdateError):self.engine.start("1.5.0",str(uuid.uuid4()))
    def test_inflight_recovery_uses_original_trust_time(self):
        job=self.start();self.host.backup(job.folder / "backup");package=job.folder / protocol.package_name("1.5.0");package.write_bytes(b"tested-new-package")
        self.host.install(package,job.log);job.update(installation_started=True,schema_before=3,state="installing")
        with patch("update_protocol.time.time",return_value=self.manifest["expires_at"]+1):self.engine.worker(job.folder.name)
        self.assertEqual(job.read()["state"],"succeeded");self.assertEqual(self.host.used(),107)
    def test_tampered_inflight_task_keeps_maintenance(self):
        job=self.start();job.update(installation_started=True,schema_before=3,state="installing");(job.folder / protocol.SIGNATURE_NAME).write_bytes(b"x"*64)
        self.engine.worker(job.folder.name)
        self.assertEqual(job.read()["state"],"recovery_required");self.assertTrue(self.engine.status()["maintenance"])
    def test_boolean_or_unknown_manifest_fields_rejected(self):
        for key,value in (("format",True),("unrecognized_critical_requirement",True)):
            data=copy.deepcopy(self.manifest);data[key]=value;raw,sig=self.sign(data)
            with self.assertRaises(protocol.UpdateError):protocol.verify_manifest(raw,sig,self.public)

    def test_corrupted_stored_package_after_interruption_rolls_back(self):
        job=self.start();self.host.backup(job.folder / "backup");package=job.folder / protocol.package_name("1.5.0");package.write_bytes(b"tested-new-package")
        self.host.install(package,job.log);package.write_bytes(b"corrupted");job.update(installation_started=True,schema_before=3,state="installing")
        self.engine.worker(job.folder.name)
        self.assertEqual(job.read()["state"],"rolled_back");self.assertEqual(self.host.used(),107)

    def test_configured_package_is_not_configured_twice_during_recovery(self):
        with patch("updater.command",return_value="install ok installed") as run:
            updater.Host().repair()
        self.assertEqual(run.call_count,1)
    def test_half_configured_package_is_repaired(self):
        with patch("updater.command",side_effect=["install ok unpacked",""]) as run:
            updater.Host().repair()
        self.assertEqual(run.call_args_list[-1].args[0],["dpkg","--configure","vpn-traffic"])


if __name__=="__main__":unittest.main()
