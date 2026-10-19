"""Independent *process* watchdog for Brain's /healthz, not self-observation.

Deploy on a separate failure domain/service from Brain. The probe never mutates
Council tasks; without a configured authenticated alert route it records locally.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
import os
from contextlib import closing
from pathlib import Path
import sqlite3
import time
import urllib.request
import urllib.error


def utc():
    return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')


class Watchdog:
    def __init__(self, dbpath, limit=3):
        if limit < 1: raise ValueError('Invalid failure threshold')
        self.limit = limit
        self.dbpath = Path(dbpath)
        self.dbpath.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.dbpath)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK (id=1), misses INTEGER NOT NULL, incident_open INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS incidents (seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, observed_utc TEXT NOT NULL, detail TEXT NOT NULL)')
            db.execute('INSERT OR IGNORE INTO state(id,misses,incident_open) VALUES(1,0,0)')

    def observe(self, succeeded:bool, detail=''):
        with closing(sqlite3.connect(self.dbpath)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            misses,opened=db.execute('SELECT misses,incident_open FROM state WHERE id=1').fetchone()
            event=None
            if succeeded:
                if opened:
                    event='BRAIN_RECOVERED'
                misses,opened=0,0
            else:
                misses=min(misses+1,self.limit)
                if misses>=self.limit and not opened:
                    event='BRAIN_UNAVAILABLE'
                    opened=1
            db.execute('UPDATE state SET misses=?,incident_open=? WHERE id=1',(misses,opened))
            if event:
                db.execute('INSERT INTO incidents(kind,observed_utc,detail) VALUES(?,?,?)',(event,utc(),detail[:250]))
            return {'misses':misses,'incident_open':bool(opened),'transition':event}


def probe(url, timeout=5):
    if not url.startswith('http://') and not url.startswith('https://'):
        raise ValueError('Probe URL must be HTTP(S)')
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'Accept':'application/json'}),timeout=timeout) as r:
            o=json.load(r)
            return r.status==200 and o.get('service')=='brain' and o.get('scanner')=='scanning', 'HTTP '+str(r.status)
    except (urllib.error.URLError,TimeoutError,ValueError,OSError) as e:
        return False, type(e).__name__


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--target',default=os.getenv('BRAIN_TARGET_HEALTH_URL',''))
    p.add_argument('--db',default=os.getenv('WATCHDOG_LEDGER','/state/watchdog/observations.sqlite3'))
    p.add_argument('--interval',type=int,default=int(os.getenv('WATCHDOG_SCAN_INTERVAL','60')))
    p.add_argument('--threshold',type=int,default=int(os.getenv('WATCHDOG_FAILURE_THRESHOLD','3')))
    p.add_argument('--once',action='store_true')
    a=p.parse_args(argv)
    if a.interval<5 or a.interval>3600:raise SystemExit('Invalid watchdog interval')
    if not a.target.startswith(('http://','https://')):raise SystemExit('Configure independent Brain health target')
    w=Watchdog(a.db,a.threshold)
    while True:
        ok,detail=probe(a.target)
        result=w.observe(ok,detail)
        if result['transition']:
            print(json.dumps({'at':utc(),'event':result['transition'],'reason':detail,
                              'note':'Local incident log only; no notification route configured'}),flush=True)
        if a.once:break
        time.sleep(a.interval)
    return 0

if __name__=='__main__':raise SystemExit(main())
