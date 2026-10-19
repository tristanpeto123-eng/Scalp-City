"""Standalone source and runtime integration tests. Not real host commissioning."""
from datetime import datetime,timezone
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import urllib.error
import urllib.request
import unittest
from engine.brain import collect_registers, snapshot
from engine.brain_service import Config, ScanState, handler_factory
from engine.watchdog import Watchdog, probe

class HostTests(unittest.TestCase):
    def test_register_preserves_project_ids_and_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'registers';p.mkdir()
            for project in ('gain-focus','scalp-city'):
                (p/(project+'.json')).write_text(json.dumps({'schema':'project-os-register/0.1.1','project':project,
                          'tasks':[{'id':'t1','title':'Do task','state':'IN_PROGRESS','dependencies':[],'assignee':'Executor'}]}))
            tasks,errors=collect_registers(p)
            self.assertEqual(len(tasks),2)
            self.assertEqual(len({t['task_id'] for t in tasks}),2)
            self.assertFalse(errors)
            self.assertTrue(all(t['progress']=='UNKNOWN' for t in tasks))

    def test_register_requires_explicit_location(self):
        with tempfile.TemporaryDirectory() as d:
            o=snapshot(Path(d))
            self.assertEqual(next(x for x in o['sources'] if x['source_id']=='task-registers')['status'],'UNAVAILABLE')

    def test_register_approved_wait_not_technical_freeze(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'reg';p.mkdir(); (p/'a.json').write_text(json.dumps({'schema':'project-os-register/0.1.1','project':'A',
              'tasks':[{'id':'x','state':'AWAITING_OWNER','dependencies':[]}]}))
            tasks,errors=collect_registers(p)
            self.assertEqual(tasks[0]['execution'],'AWAITING_OWNER')
            self.assertEqual(tasks[0]['progress'],'WAITING_AS_EXPECTED')

    def test_invalid_register_does_not_silently_disappear(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'reg';p.mkdir();(p/'broken.json').write_text('{invalid')
            o=snapshot(Path(d),registers_root=p)
            self.assertEqual(next(x for x in o['sources'] if x['source_id']=='task-registers')['status'],'ERROR')
            self.assertTrue(o['errors'])

    def test_durable_health_and_denied_status(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);c=root/'council';c.mkdir()
            config=Config(c,root/'brain.sqlite',interval=5,stale_after=20,port=0)
            state=ScanState(config)
            self.assertEqual(state.health()[1],503)
            self.assertTrue(state.once())
            self.assertEqual(state.health()[1],200)
            server=ThreadingHTTPServer(('127.0.0.1',0),handler_factory(state))
            t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
            try:
                base=f'http://127.0.0.1:{server.server_port}'
                self.assertTrue(probe(base+'/healthz')[0])
                with self.assertRaises(urllib.error.HTTPError) as ex:
                    urllib.request.urlopen(base+'/status')
                self.assertEqual(ex.exception.code,404)
            finally:server.shutdown();server.server_close();t.join()

    def test_health_rejects_unreadable_source(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            s=ScanState(Config(root/'absent',root/'brain.sqlite',interval=5,stale_after=20))
            self.assertTrue(s.once())
            self.assertEqual(s.health()[1],503)

    def test_token_validation_and_status_lock(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);c=root/'council';c.mkdir()
            with self.assertRaises(ValueError):Config(c,root/'ledger',status_token='weak').validate()
            s=ScanState(Config(c,root/'ledger',interval=5,stale_after=20,status_token='strong-token-longer-than-twenty-four'))
            s.once();server=ThreadingHTTPServer(('127.0.0.1',0),handler_factory(s))
            t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
            try:
                base=f'http://127.0.0.1:{server.server_port}'
                with self.assertRaises(urllib.error.HTTPError) as ex:urllib.request.urlopen(base+'/status')
                self.assertEqual(ex.exception.code,401)
                req=urllib.request.Request(base+'/status',headers={'Authorization':'Bearer strong-token-longer-than-twenty-four'})
                with urllib.request.urlopen(req) as r:self.assertEqual(json.load(r)['latest_scan']['schema'],'brain.snapshot/0.1.0')
            finally:server.shutdown();server.server_close();t.join()

    def test_watchdog_dedup_persistence_and_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'watchdog.sqlite';w=Watchdog(path,limit=3)
            self.assertIsNone(w.observe(False)['transition'])
            self.assertIsNone(w.observe(False)['transition'])
            self.assertEqual(w.observe(False)['transition'],'BRAIN_UNAVAILABLE')
            self.assertIsNone(Watchdog(path,3).observe(False)['transition'])
            self.assertEqual(Watchdog(path,3).observe(True)['transition'],'BRAIN_RECOVERED')
            self.assertIsNone(Watchdog(path,3).observe(True)['transition'])

    def test_service_must_not_write_inside_source(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'council';root.mkdir()
            with self.assertRaises(ValueError):Config(root,root/'brain.sqlite').validate()

if __name__=='__main__':unittest.main()
