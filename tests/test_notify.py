"""Non-network smoke coverage for five-minute, change-only Brain message notifications."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from host.notify import scan
from council.runtime import outbox_bus as bus

class TestRelay(unittest.TestCase):
    def test_baseline_and_no_message_no_push(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            hits=[]
            def push(*a):hits.append(a)
            self.assertEqual(scan(root/"absent.sqlite",root/"brain.sqlite","dummy",push)["status"],"BASELINED")
            self.assertEqual(scan(root/"absent.sqlite",root/"brain.sqlite","dummy",push)["status"],"SOURCE_NOT_CONNECTED")
            self.assertFalse(hits)

    def test_real_owner_message_deduplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            outbox=root/"council"/"outbox"
            c=bus.open_bus(outbox)
            owner=bus.enroll(c,"Owner")
            director=bus.enroll(c,"Director")
            path=outbox/"outboxes.sqlite3"
            hits=[]
            def push(*a):hits.append(a)
            self.assertEqual(scan(path,root/"brain.sqlite","dummy",push)["status"],"BASELINED")
            bus.post(c,mission_id="MISSION-DEMO",sender="Director",recipients=["Owner"],
                     token=director,subject="Action required",body="Approval review",
                     kind="question")
            c.close()
            self.assertEqual(scan(path,root/"brain.sqlite","dummy",push)["status"],"MESSAGE_PUSHED")
            self.assertEqual(scan(path,root/"brain.sqlite","dummy",push)["status"],"NO_CHANGE")
            self.assertEqual(len(hits),1)

    def test_failed_push_retries_without_losing_message(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); outbox=root/"outbox"
            c=bus.open_bus(outbox)
            director=bus.enroll(c,"Director")
            path=outbox/"outboxes.sqlite3"
            scan(path,root/"ledger.sqlite","dummy",lambda *a:None)
            bus.post(c,mission_id="MISSION-DEMO",sender="Director",recipients=["Owner"],
                     token=director,subject="Review",body="Review needed",kind="question")
            c.close()
            def broken(*a):raise RuntimeError("provider down")
            with self.assertRaises(RuntimeError):
                scan(path,root/"ledger.sqlite","dummy",broken)
            self.assertEqual(scan(path,root/"ledger.sqlite","dummy",lambda *a:None)["status"],"MESSAGE_PUSHED")

if __name__=="__main__":unittest.main()
