from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
import store
from server import csv_bytes
from system_info import cpu_ticks,cpu_percent,memory_usage


class HourlyHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=Path(self.temp.name)/'db'
        store.initialize(self.db,[{'email':'one','label':'One'},{'email':'two','label':'Two'}])
        self.now=datetime(2026,11,7,15,30,tzinfo=store.TZ).timestamp()

    def tearDown(self):self.temp.cleanup()

    def sample(self,stamp,seconds,up,down,email='one'):
        with store.database(self.db) as db:
            db.execute('INSERT INTO samples(timestamp,email,up,down,seconds) VALUES (?,?,?,?,?)',(stamp,email,up,down,seconds))

    def test_cross_hour_apportionment_conserves_bytes(self):
        stamp=datetime(2026,11,7,15,0,15,tzinfo=store.TZ).timestamp()
        self.sample(stamp,30,61,121)
        result=store.hourly_history(self.db,'one',self.now)
        by_hour={r['bucket_start']:r for r in result['rows']}
        earlier=datetime(2026,11,7,14,tzinfo=store.TZ).timestamp()
        self.assertEqual((by_hour[earlier]['up'],by_hour[earlier+3600]['up']),(30,31))
        self.assertEqual((result['up'],result['down']),(61,121))
        self.assertEqual(len(result['rows']),25)

    def test_exact_24_hour_window_clips_first_interval(self):
        self.sample(self.now-86400+15,30,61,121)
        self.sample(self.now-86400-1,0,999,999)
        result=store.hourly_history(self.db,'one',self.now)
        self.assertEqual((result['up'],result['down']),(31,61))
        self.assertEqual(result['window_end']-result['window_start'],86400)

    def test_point_samples_and_identity_filter(self):
        self.sample(self.now-100,0,7,11)
        self.sample(self.now-100,0,13,17,'two')
        result=store.hourly_history(self.db,'two',self.now)
        self.assertEqual((result['up'],result['down']),(13,17))
        self.assertTrue(all(row['email']=='two' for row in result['rows']))
        self.assertEqual(store.hourly_history(self.db,'',self.now)['up'],20)
        with self.assertRaises(ValueError):store.hourly_history(self.db,"' OR 1=1",self.now)

    def test_unobserved_hours_and_csv_are_explicit(self):
        self.sample(self.now-100,30,0,0)
        result=store.hourly_history(self.db,'one',self.now)
        self.assertTrue(result['rows'][0]['observed'])
        self.assertFalse(result['rows'][-1]['observed'])
        text=csv_bytes(result).decode('utf-8-sig')
        self.assertIn('小时区间(北京时间)',text)
        self.assertIn('One,,,',text)

    def test_hourly_reads_do_not_change_quota_or_history(self):
        with store.database(self.db) as db:db.execute("UPDATE limits SET quota=1000,bonus=50,period_up=100,period_down=200 WHERE email='one'")
        self.sample(self.now-20,30,20,40)
        before=store.summary(self.db,'test',self.now)
        store.hourly_history(self.db,'one',self.now)
        self.assertEqual(store.summary(self.db,'test',self.now),before)

    def test_whole_hour_sampling_gaps_are_not_invented_zero_traffic(self):
        self.sample(self.now-4*3600,0,0,0)
        self.sample(self.now-100,0,0,0)
        result=store.hourly_history(self.db,'one',self.now)
        middle=int((self.now-2*3600)//3600)*3600
        row=next(r for r in result['rows'] if r['bucket_start']==middle)
        self.assertFalse(row['observed'])


class ResourceTests(unittest.TestCase):
    def test_cpu_excludes_guest_double_counting_and_includes_iowait_as_idle(self):
        prior=cpu_ticks('cpu  100 10 50 800 20 5 10 5 80 3\n')
        current=cpu_ticks('cpu  120 10 60 860 30 5 10 5 100 3\n')
        self.assertEqual(cpu_percent(prior,current),30.0)
        self.assertIsNone(cpu_percent(current,current))

    def test_memory_uses_available_memory(self):
        used,total,percentage=memory_usage('MemTotal: 1000 kB\nMemFree: 100 kB\nMemAvailable: 400 kB\nCached: 300 kB\n')
        self.assertEqual((used,total,percentage),(600*1024,1000*1024,60.0))

    def test_resource_values_expire_instead_of_showing_stale_zeroes(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'db';store.initialize(path,[])
            self.assertFalse(store.read_system(path,100)['available'])
            store.write_system(path,{'sampled_at':100,'cpu_percent':12.5,'memory_percent':60,'disk_percent':20})
            self.assertTrue(store.read_system(path,110)['available'])
            self.assertFalse(store.read_system(path,121)['available'])


if __name__=='__main__':unittest.main()
