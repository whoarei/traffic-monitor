import json
import multiprocessing
from pathlib import Path
import socket
import sys
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))


def serve_test(port):
    import server
    def broker(action,**params):return {"action":action,"parameters":params}
    with patch("updater_client.call",side_effect=broker),patch("updater_client.maintenance",return_value=True):
        server.run({"server":"test.invalid","web_listen":"127.0.0.1:"+str(port),"web_root":"/unused","database":"/unused"})


class UpdateHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with socket.socket() as sock:sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
        cls.process=multiprocessing.get_context("fork").Process(target=serve_test,args=(port,),daemon=True);cls.process.start()
        cls.url="http://127.0.0.1:"+str(port)
        cls.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for _ in range(100):
            try:
                with cls.opener.open(cls.url+"/api/session",timeout=.5) as response:cls.token=json.load(response)["token"]
                break
            except OSError:time.sleep(.02)
        else:raise RuntimeError("Local test server did not start")
    @classmethod
    def tearDownClass(cls):cls.process.terminate();cls.process.join(timeout=5)
    def post(self,path,payload,token=True,origin="https://test.invalid:8443"):
        headers={"Content-Type":"application/json","Origin":origin}
        if token:headers["X-VPN-CSRF"]=self.token
        request=urllib.request.Request(self.url+path,data=json.dumps(payload).encode(),headers=headers)
        try:
            with self.opener.open(request) as response:return response.status,json.load(response)
        except urllib.error.HTTPError as error:return error.code,json.load(error)
    def test_update_checks_require_csrf(self):self.assertEqual(self.post("/api/updates/check",{},token=False)[0],403)
    def test_update_checks_require_same_origin(self):self.assertEqual(self.post("/api/updates/check",{},origin="https://other.invalid")[0],403)
    def test_authenticated_start_only_forwards_version_and_id(self):
        status,result=self.post("/api/updates/start",{"version":"1.5.0","request_id":"id","url":"https://evil.invalid","command":"id"})
        self.assertEqual(status,200);self.assertEqual(result["parameters"],{"version":"1.5.0","request_id":"id"})
    def test_connection_mutations_locked_during_update(self):self.assertEqual(self.post("/api/settings",{"reset_day":1,"reset_time":"00:00"})[0],423)


if __name__=="__main__":unittest.main()
