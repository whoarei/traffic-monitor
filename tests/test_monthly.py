from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
import store


class MonthlyTests(unittest.TestCase):
    def test_reset_at_custom_beijing_time(self):
        now=datetime(2026,11,7,7,59,tzinfo=store.TZ).timestamp()
        start,end=store.reset_bounds(7,"08:00",now)
        self.assertEqual(datetime.fromtimestamp(start,store.TZ).isoformat(),"2026-10-07T08:00:00+08:00")
        self.assertEqual(datetime.fromtimestamp(end,store.TZ).isoformat(),"2026-11-07T08:00:00+08:00")
        start,end=store.reset_bounds(7,"08:00",now+60)
        self.assertEqual(datetime.fromtimestamp(end,store.TZ).isoformat(),"2026-12-07T08:00:00+08:00")

    def test_day_31_clamps_but_preserves_monthly_anchor(self):
        now=datetime(2027,2,28,23,59,tzinfo=store.TZ).timestamp()
        start,end=store.reset_bounds(31,"09:30",now)
        self.assertEqual(datetime.fromtimestamp(start,store.TZ).day,28)
        self.assertEqual(datetime.fromtimestamp(end,store.TZ).day,31)
        self.assertEqual(datetime.fromtimestamp(end,store.TZ).month,3)

    def test_invalid_schedules(self):
        for day,clock in [(0,"00:00"),(32,"00:00"),(1,"24:00"),(1,"12:61"),(1,"1:00"),(1,"bad")]:
            with self.assertRaises(ValueError):store.reset_bounds(day,clock)

    def test_quota_period_rollover_retains_history(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"db"
            store.initialize(path,[{"email":"one","label":"One"}])
            before=datetime(2026,11,7,7,59,45,tzinfo=store.TZ).timestamp()
            start,end=store.reset_bounds(7,"08:00",before)
            with store.database(path) as db:
                db.execute("UPDATE limits SET quota=1000")
                db.execute("UPDATE reset_schedule SET reset_day=7,reset_time='08:00',cycle_start=?,next_reset=?",(start,end))
            store.record(path,{"one":{"uplink":100,"downlink":200}},"first",before)
            store.record(path,{"one":{"uplink":160,"downlink":320}},"first",before+30)
            state=store.summary(path,"test",before+30)["devices"][0]
            self.assertEqual(state["total"],480)
            self.assertEqual((state["period_up"],state["period_down"]),(30,60))
            self.assertEqual(datetime.fromtimestamp(store.summary(path,"test",before+30)["schedule"]["next_reset"],store.TZ).month,12)

    def test_manual_reset_preserves_totals_and_next_schedule(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"db"
            store.initialize(path,[{"email":"one","label":"One"}])
            now=datetime(2026,11,7,12,tzinfo=store.TZ).timestamp()
            store.record(path,{"one":{"uplink":100,"downlink":200}},"first",now)
            next_reset=store.summary(path,"test",now)["schedule"]["next_reset"]
            with store.database(path) as db:db.execute("UPDATE limits SET period_up=0,period_down=0")
            store.record(path,{"one":{"uplink":110,"downlink":220}},"first",now+30)
            state=store.summary(path,"test",now+30)["devices"][0]
            self.assertEqual((state["total"],state["period_used"]),(330,30))
            self.assertEqual(store.summary(path,"test",now+30)["schedule"]["next_reset"],next_reset)

    def test_monthly_quota_survives_xray_and_application_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"db"
            store.initialize(path,[{"email":"one","label":"One"}])
            now=datetime(2026,11,7,12,tzinfo=store.TZ).timestamp()
            store.record(path,{"one":{"uplink":100,"downlink":200}},"first",now)
            with store.database(path) as db:db.execute("UPDATE limits SET quota=500")
            store.update_schedule(path,15,"09:30",now)
            store.initialize(path,[{"email":"one","label":"One"}])
            store.record(path,{"one":{"uplink":20,"downlink":30}},"second",now+30)
            state=store.summary(path,"test",now+30)["devices"][0]
            self.assertEqual((state["quota"],state["period_used"]),(500,350))
            plan=store.summary(path,"test",now+30)["schedule"]
            self.assertEqual((plan["reset_day"],plan["reset_time"]),(15,"09:30"))


if __name__=="__main__":unittest.main()
