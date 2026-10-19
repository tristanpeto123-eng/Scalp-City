import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from engine.brain import (classify,evaluate_claim,dependencies_cycles,verify_events,canon,snapshot,save_snapshot,latest)
import hashlib

NOW=datetime(2026,10,19,18,0,tzinfo=timezone.utc)
BASE={'mission_id':'MISSION-X','status':'ACTIVE','stage_index':4,'reports':[], 'project':'example'}
RUN={'stage':'EXECUTOR_BUILD','status':'RUNNING','heartbeat':NOW.timestamp(),
     'started':(NOW-timedelta(minutes=20)).timestamp(),'run_id':'r2'}

class TestBrain(unittest.TestCase):
    def test_b01_alive_stall(self):
        o=classify(BASE,RUN,NOW,{'heartbeat_seconds':60,'max_progress_gap_seconds':600})
        self.assertEqual((o['liveness'],o['progress']),('ALIVE','SUSPECTED_STALL'))
    def test_b02_long_job_checkpoint(self):
        r=dict(RUN,last_qualifying_checkpoint=(NOW-timedelta(seconds=20)).timestamp())
        o=classify(BASE,r,NOW,{'max_progress_gap_seconds':1800})
        self.assertEqual(o['progress'],'ADVANCING')
    def test_b03_exited_worker(self):
        r=dict(RUN,status='FAILED',exit_code=1)
        o=classify(BASE,r,NOW,{})
        self.assertEqual((o['liveness'],o['progress']),('STOPPED','CONFIRMED_STALL'))
        self.assertEqual(o['cause_status'],'UNKNOWN')
    def test_b04_missing_telemetry(self):
        o=classify(BASE,None,NOW,{'heartbeat_seconds':60})
        self.assertEqual((o['execution'],o['progress']),('UNKNOWN','UNKNOWN'))
    def test_b05_owner_wait(self):
        o=classify(dict(BASE,status='AWAITING_OWNER'),RUN,NOW,{})
        self.assertEqual((o['execution'],o['progress']),('AWAITING_OWNER','WAITING_AS_EXPECTED'))
    def test_b07_cycle(self):
        self.assertEqual(len(dependencies_cycles({'A':['B'],'B':['A']})),1)
        self.assertFalse(dependencies_cycles({'A':['B'],'B':[]}))
    def test_b08_conflicted_test(self):
        claim={'task_id':'A','run_id':'R','version':'v2','environment':'stage'}
        ev=dict(claim,inspected=True,direct_conflict=True,relevant=True,current=True)
        self.assertEqual(evaluate_claim(claim,ev),'CONTRADICTED')
    def test_b09_no_log(self):
        self.assertEqual(evaluate_claim({'task_id':'A','run_id':'R'},None),'UNSUPPORTED')
    def test_b10_wrong_version(self):
        c={'task_id':'A','run_id':'R','version':'v2'}
        e={'task_id':'A','run_id':'R','version':'v1','inspected':True,'relevant':True,'current':True,'supports_exact_claim':True}
        self.assertEqual(evaluate_claim(c,e),'UNSUPPORTED')
    def test_b11_preview_not_deployment(self):
        c={'task_id':'A','run_id':'R'};e=dict(c,inspected=True,relevant=True,current=True,supports_exact_claim=False)
        self.assertEqual(evaluate_claim(c,e),'UNSUPPORTED')
    def test_b13_replay_hash_check(self):
        x={'type':'OWNER_REQUEST','value':{'prompt_hash':'x'},'timestamp':'2026-10-19T01:00:00Z','prev':'GENESIS'}
        x['hash']=hashlib.sha256(canon(x)).hexdigest()
        self.assertTrue(verify_events([json.dumps(x)]))
        x['value']['prompt_hash']='changed'
        self.assertFalse(verify_events([json.dumps(x)]))
    def test_b18_missing_inventory_preserved(self):
        with tempfile.TemporaryDirectory() as d:
            r=snapshot(Path(d),now=NOW)
            self.assertEqual(r['coverage'],'PARTIAL')
            self.assertFalse(r['live_worker_connections_verified'])
            self.assertEqual(r['sources'][1]['status'],'UNAVAILABLE')
    def test_b19_restart_heartbeat_only_not_recovery(self):
        o=classify(BASE,RUN,NOW,{'heartbeat_seconds':60,'max_progress_gap_seconds':600})
        self.assertNotEqual(o['progress'],'ADVANCING')
    def test_b22_no_independent_reviewer(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(snapshot(Path(d),now=NOW)['live_worker_connections_verified'])
    def test_b23_hash_not_claim_proof(self):
        c={'task_id':'A','run_id':'R'};e=dict(c,inspected=False,relevant=True,current=True,supports_exact_claim=True)
        self.assertEqual(evaluate_claim(c,e),'UNSUPPORTED')
    def test_ledger_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            r=snapshot(Path(d),now=NOW);db=Path(d).parent/'brain-test-temp.sqlite'
            try:
                save_snapshot(db,r)
                self.assertEqual(latest(db)['scan_id'],r['scan_id'])
            finally:
                for suffix in ('','-wal','-shm'):
                    (Path(str(db)+suffix)).unlink(missing_ok=True)
    def test_read_only_local_adapter_real_shapes(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)/'council';p=root/'missions'/'MISSION-AB';p.mkdir(parents=True)
            x={'type':'OWNER_REQUEST','value':{'prompt_hash':'abc'},'timestamp':'2026-10-19T01:00:00Z','prev':'GENESIS'}
            x['hash']=hashlib.sha256(canon(x)).hexdigest()
            (p/'events.jsonl').write_text(json.dumps(x)+'\n')
            state=dict(BASE,schema='project-os-council-routing/0.2.0',mission_id='MISSION-AB',original_prompt_sha256='abc',baseline_ref='b',reports=[])
            (p/'mission.json').write_text(json.dumps(state))
            supervisor=root/'supervisor';supervisor.mkdir()
            con=sqlite3.connect(supervisor/'state.sqlite3')
            con.execute('CREATE TABLE runs(mission_id TEXT,stage TEXT,status TEXT,heartbeat REAL,started REAL,exit_code INTEGER,run_id TEXT)')
            con.execute('CREATE TABLE incidents(mission_id TEXT,stage TEXT,kind TEXT)')
            con.execute('INSERT INTO runs VALUES(?,?,?,?,?,?,?)',('MISSION-AB','EXECUTOR_BUILD','RUNNING',NOW.timestamp(),NOW.timestamp(),None,'run-1'))
            con.commit();con.close()
            result=snapshot(root,now=NOW,config={'heartbeat_seconds':60})
            self.assertEqual(len(result['tasks']),1)
            self.assertEqual(result['tasks'][0]['execution'],'RUNNING')
            self.assertEqual(result['tasks'][0]['liveness'],'ALIVE')
            self.assertFalse(result['live_worker_connections_verified'])
    def test_missing_source_must_be_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            o=snapshot(Path(d)/'missing',now=NOW)
            self.assertEqual(o['coverage'],'UNKNOWN')
            self.assertEqual(o['sources'][0]['status'],'UNAVAILABLE')

if __name__=='__main__':unittest.main()
