from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))
import store
from server import csv_bytes


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "traffic.sqlite3"
        store.initialize(self.db, [{"email": "android", "label": "Android"}, {"email": "windows", "label": "Windows"}])
        self.start = datetime(2026, 11, 7, 12, 0, tzinfo=store.TZ).timestamp()

    def tearDown(self):
        self.temp.cleanup()

    def sample(self, up, down, seconds=0, gen="one"):
        store.record(self.db, {"android": {"uplink": up, "downlink": down}}, gen, self.start + seconds)

    def device(self, seconds=0):
        return next(d for d in store.summary(self.db, "test", self.start+seconds)["devices"] if d["id"] == "android")

    def test_duplicate_poll_and_collector_restart_do_not_double_count(self):
        self.sample(100, 200)
        self.sample(100, 200, 30)
        store.initialize(self.db, [{"email": "android", "label": "New label"}])
        self.sample(130, 260, 60)
        device = self.device(60)
        self.assertEqual((device["up"],device["down"]),(130,260))
        self.assertEqual((device["up_rate"],device["down_rate"]),(1,2))
        self.assertEqual(device["label"], "New label")

    def test_xray_restart_retains_totals(self):
        self.sample(100, 200)
        self.sample(10, 20, 30, gen="two")
        self.sample(30, 50, 60, gen="two")
        self.assertEqual((self.device(60)["up"],self.device(60)["down"]),(130,250))
        with store.database(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='xray_restart'").fetchone()[0],1)

    def test_counter_reset_is_not_negative(self):
        self.sample(100, 200)
        self.sample(5, 10, 30)
        self.assertEqual((self.device(30)["up"], self.device(30)["down"]),(105,210))

    def test_missing_counter_is_not_counted_again(self):
        self.sample(100,200)
        store.record(self.db, {}, "one", self.start+30)
        self.sample(110,220,60)
        self.assertEqual(self.device(60)["total"],330)

    def test_midnight_split_uses_beijing_time_and_conserves_bytes(self):
        self.start = datetime(2026,11,7,23,59,45,tzinfo=store.TZ).timestamp()
        self.sample(0,0)
        self.sample(61,121,30)
        result=store.history(self.db,"2026-11-07","2026-11-08","android")
        rows={r["day"]:r for r in result["rows"]}
        self.assertEqual((rows["2026-11-07"]["up"],rows["2026-11-08"]["up"]),(30,31))
        self.assertEqual((result["up"],result["down"]),(61,121))

    def test_device_filter_and_invalid_ranges(self):
        self.sample(10,20)
        store.record(self.db,{"android":{"uplink":10,"downlink":20},"windows":{"uplink":30,"downlink":40}},"one",self.start+30)
        result=store.history(self.db,"2026-11-07","2026-11-07","windows")
        self.assertEqual((result["up"],result["down"]),(30,40))
        for start,end in [("bad","2026-11-07"),("2026-11-08","2026-11-07"),("2000-01-01","2026-11-07")]:
            with self.assertRaises(ValueError):store.history(self.db,start,end)
        with self.assertRaises(ValueError):store.history(self.db,"2026-11-07","2026-11-07","' OR 1=1 --")

    def test_readonly_queries_and_online_backup(self):
        self.sample(999,1999)
        copy=Path(self.temp.name)/"copy.db"
        with store.database(self.db,readonly=True) as db:
            with self.assertRaises(sqlite3.OperationalError):db.execute("DELETE FROM devices")
            with sqlite3.connect(copy) as dest:db.backup(dest)
        self.assertEqual(store.summary(copy,"test",self.start)["devices"][0]["total"],2998)

    def test_clock_regression_does_not_duplicate_data(self):
        self.sample(10,20,30)
        with self.assertRaises(RuntimeError):self.sample(30,40,0)
        self.assertEqual(self.device(30)["total"],30)

    def test_error_and_stale_states_are_visible(self):
        self.sample(10,20)
        self.assertTrue(store.summary(self.db,"test",self.start)["healthy"])
        self.assertFalse(store.summary(self.db,"test",self.start+100)["healthy"])
        store.record_error(self.db,"Unavailable")
        self.assertEqual(store.summary(self.db,"test",self.start)["error"],"Unavailable")
        self.sample(15,25,30)
        self.assertTrue(store.summary(self.db,"test",self.start+30)["healthy"])

    def test_csv_neutralizes_formula_labels(self):
        payload=csv_bytes({"rows":[{"day":"2026-11-07","email":"=x","label":"+SUM(1)","up":1,"down":2,"total":3}]})
        self.assertIn("'=x,'+SUM(1)",payload.decode("utf-8-sig"))


if __name__=="__main__":unittest.main()
