from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
import manager
import store


class GlobalAllowanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Path(self.temp.name)/"db"
        self.devices=[{"email":"one","label":"One"},{"email":"two","label":"Two"}]
        store.initialize(self.db,self.devices)
        self.now=datetime(2026,11,7,12,tzinfo=store.TZ).timestamp()

    def tearDown(self):self.temp.cleanup()

    def sample(self,up,down,seconds=0):
        store.record(self.db,{"one":{"uplink":up,"downlink":down}},"first",self.now+seconds)

    def test_migration_preserves_quotas_usage_and_adopts_one_shared_schedule(self):
        self.sample(100,200)
        with store.database(self.db) as db:
            db.execute("ALTER TABLE limits RENAME TO new_limits")
            db.execute("""CREATE TABLE limits(email TEXT PRIMARY KEY,quota INTEGER,reset_day INTEGER,reset_time TEXT,cycle_start REAL,next_reset REAL,period_up INTEGER,period_down INTEGER,enabled INTEGER,blocked TEXT)""")
            start,end=store.reset_bounds(15,"09:30",self.now)
            db.execute("INSERT INTO limits SELECT email,300,15,'09:30',?,?,period_up,period_down,enabled,blocked FROM new_limits",(start,end))
            db.execute("DROP TABLE new_limits")
            db.execute("DROP TABLE reset_schedule")
            db.execute("PRAGMA user_version=2")
        store.initialize(self.db,self.devices)
        state=store.summary(self.db,"test",self.now)
        self.assertEqual((state["schedule"]["reset_day"],state["schedule"]["reset_time"]),(15,"09:30"))
        one=next(d for d in state["devices"] if d["id"]=="one")
        self.assertEqual((one["quota"],one["total"],one["period_used"]),(300,300,300))
        with store.database(self.db,readonly=True) as db:
            self.assertNotIn("reset_day",{row[1] for row in db.execute("PRAGMA table_info(limits)")})
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0],3)

    def test_changing_system_schedule_preserves_every_account_usage(self):
        self.sample(100,200)
        store.update_schedule(self.db,20,"08:45",self.now)
        one=store.summary(self.db,"test",self.now)["devices"][0]
        self.assertEqual(one["period_used"],300)
        self.assertNotIn("reset_day",one)
        store.initialize(self.db,self.devices+[{"email":"three","label":"Three"}])
        state=store.summary(self.db,"test",self.now)
        self.assertEqual(state["schedule"]["reset_day"],20)
        self.assertNotIn("reset_time",state["devices"][0])

    def test_temporary_grant_restores_allowance_without_clearing_usage(self):
        self.sample(100,250)
        with store.database(self.db) as db:db.execute("UPDATE limits SET quota=300 WHERE email='one'")
        result=store.grant_bonus(self.db,"one",100,"operation-000000001")
        self.assertEqual((result["bonus"],result["available"]),(150,100))
        one=store.summary(self.db,"test",self.now)["devices"][0]
        self.assertEqual((one["quota"],one["quota_effective"],one["period_used"],one["total"]),(300,450,350,350))
        self.assertEqual(manager.desired({"enabled":1,"quota":300,"bonus":150,"period_up":100,"period_down":250}),(True,""))

    def test_grant_retry_does_not_add_twice(self):
        with store.database(self.db) as db:db.execute("UPDATE limits SET quota=300 WHERE email='one'")
        store.grant_bonus(self.db,"one",100,"operation-000000001")
        store.grant_bonus(self.db,"one",100,"operation-000000001")
        self.assertEqual(store.summary(self.db,"test",self.now)["devices"][0]["bonus"],100)
        with self.assertRaises(ValueError):store.grant_bonus(self.db,"one",200,"operation-000000001")

    def test_global_reset_expires_bonus_for_all_including_inactive(self):
        self.sample(100,200)
        with store.database(self.db) as db:
            db.execute("UPDATE limits SET quota=500,bonus=100,period_up=100,period_down=200")
            db.execute("UPDATE devices SET active=0 WHERE email='two'")
        future=datetime(2026,12,1,0,0,15,tzinfo=store.TZ).timestamp()
        store.record(self.db,{},"first",future)
        state=store.summary(self.db,"test",future)
        self.assertTrue(all(d["bonus"]==0 and d["period_used"]==0 and d["quota"]==500 for d in state["devices"]))
        self.assertEqual(state["devices"][0]["total"],300)

    def test_grant_does_not_override_manual_disable(self):
        with store.database(self.db) as db:db.execute("UPDATE limits SET quota=300,enabled=0 WHERE email='one'")
        result=store.grant_bonus(self.db,"one",100)
        self.assertFalse(result["enabled"])
        self.assertEqual(manager.desired({"enabled":0,"quota":300,"bonus":100,"period_up":0,"period_down":0}),(False,"disabled"))

    def test_invalid_and_unlimited_grants_are_rejected(self):
        with self.assertRaises(ValueError):store.grant_bonus(self.db,"one",100)
        for value in [0,-1,True,1.5,1024**5+1]:
            with self.assertRaises(ValueError):store.grant_bonus(self.db,"one",value)

    def test_individual_schedule_fields_are_rejected(self):
        with self.assertRaises(ValueError):manager.validate_policy({"quota":100,"reset_day":15})
        with self.assertRaises(ValueError):manager.validate_policy({"quota":100,"reset_time":"08:00"})


if __name__=="__main__":unittest.main()
