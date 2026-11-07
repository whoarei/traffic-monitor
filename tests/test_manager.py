import importlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"app"))
import store
import manager


class ManagerTests(unittest.TestCase):
    def test_validation_and_escaped_names(self):
        self.assertEqual(manager.validate_label(' 测试 "手机" '),'测试 "手机"')
        for label in ["","x"*65,"bad\nheader"]:
            with self.assertRaises(ValueError):manager.validate_label(label)
        for quota in [-1,True,1.1,1024**5+1]:
            with self.assertRaises(ValueError):manager.validate_policy({"quota":quota})

    def test_effective_state(self):
        self.assertEqual(manager.desired({"enabled":1,"quota":100,"period_up":30,"period_down":70}),(False,"quota"))
        self.assertEqual(manager.desired({"enabled":0,"quota":0,"period_up":30,"period_down":70}),(False,"disabled"))
        self.assertEqual(manager.desired({"enabled":1,"quota":0,"period_up":30,"period_down":70}),(True,""))

    def test_live_counters_trigger_checkpoint_and_enforcement(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"db"
            store.initialize(path,[{"email":"one","label":"One"}])
            with store.database(path) as db:
                db.execute("UPDATE limits SET quota=100,period_up=20,period_down=20")
                db.execute("UPDATE devices SET generation='one',raw_up=20,raw_down=20")
            instance=manager.Manager({"database":str(path)})
            with patch.object(instance,"checkpoint") as checkpoint,patch.object(instance,"apply") as apply,patch.object(manager.collector,"read_counters",return_value=("one",{"one":{"uplink":60,"downlink":60}})):
                instance.enforce();checkpoint.assert_called_once();apply.assert_called_once()

    def test_reset_quota_resumes_previously_blocked_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"db"
            store.initialize(path,[{"email":"one","label":"One"}])
            with store.database(path) as db:
                db.execute("UPDATE limits SET quota=100,blocked='quota',period_up=0,period_down=0")
                db.execute("UPDATE devices SET active=0")
            instance=manager.Manager({"database":str(path)})
            with patch.object(instance,"apply") as apply:
                instance.enforce();apply.assert_called_once()


if __name__=="__main__":unittest.main()
