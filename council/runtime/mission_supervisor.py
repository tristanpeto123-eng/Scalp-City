#!/usr/bin/env python3
"""Council host-side persistent mission supervisor and watchdog (v0.2.6).

Not a ChatGPT background job and not an agent-model adapter. Runs on an owner-
authorised trusted host. Launches only explicitly configured local stage commands;
will never infer success, skip Checker gates, or grant owner approval.
"""
import argparse
import json
import os
import secrets
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import dispatcher_v0_2 as dispatch
    from . import outbox_bus as bus
    from . import comms_connector_v0_2 as comms
except ImportError:
    import dispatcher_v0_2 as dispatch
    import outbox_bus as bus
    import comms_connector_v0_2 as comms

DEFAULT_HEARTBEAT_SECONDS = 90
DEFAULT_STAGE_SECONDS = 1800
STAGES = {stage: role for stage, role in dispatch.STAGES}


def now():
    return time.time()


def utc(t=None):
    return datetime.fromtimestamp(now() if t is None else t, timezone.utc).isoformat()


def validate_root(root):
    root = Path(root).resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise ValueError('Expected trusted local directory')
    return root


def database(root):
    root = validate_root(root)
    private = root / 'supervisor'
    if private.is_symlink():
        raise ValueError('Refusing symlink supervisor directory')
    private.mkdir(mode=0o700, exist_ok=True)
    os.chmod(private, 0o700)
    path = private / 'state.sqlite3'
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA busy_timeout=10000')
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
    CREATE TABLE IF NOT EXISTS runs(
      mission_id TEXT PRIMARY KEY, stage_index INTEGER NOT NULL,
      stage TEXT NOT NULL, role TEXT NOT NULL, run_id TEXT NOT NULL,
      attempt INTEGER NOT NULL, status TEXT NOT NULL,
      started REAL NOT NULL, heartbeat REAL NOT NULL, pid INTEGER,
      exit_code INTEGER, reason TEXT, changed REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS incidents(
      id INTEGER PRIMARY KEY AUTOINCREMENT, mission_id TEXT NOT NULL,
      run_id TEXT NOT NULL, stage TEXT NOT NULL, kind TEXT NOT NULL,
      reason TEXT NOT NULL, at REAL NOT NULL, sent INTEGER NOT NULL DEFAULT 0,
      UNIQUE(run_id,kind));
    ''')
    if path.is_symlink():
        db.close()
        raise ValueError('Refusing symlink supervisor database')
    os.chmod(path, 0o600)
    return db


def load_config(path):
    obj = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(obj, dict) or set(obj) - {'stages', 'heartbeat_seconds', 'stage_seconds', 'poll_seconds'}:
        raise ValueError('Invalid supervisor configuration')
    cfg = obj.get('stages', {})
    if not isinstance(cfg, dict) or set(cfg) - set(STAGES):
        raise ValueError('Unknown stage in supervisor configuration')
    for name, item in cfg.items():
        if not isinstance(item, dict) or set(item) - {'argv', 'idempotent', 'max_retries', 'heartbeat_seconds', 'stage_seconds'}:
            raise ValueError('Invalid worker definition: ' + name)
        argv = item.get('argv')
        if not isinstance(argv, list) or not argv or any(not isinstance(v, str) or not v or '\x00' in v for v in argv):
            raise ValueError('Each stage requires a nonempty trusted argv list')
        if not Path(argv[0]).is_absolute():
            raise ValueError('Stage executable must be an absolute path')
        if not isinstance(item.get('idempotent', False), bool):
            raise ValueError('idempotent must be boolean')
        max_retries = item.get('max_retries', 0)
        if type(max_retries) is not int or max_retries < 0 or max_retries > 5:
            raise ValueError('max_retries must be integer 0..5')
        if max_retries and not item.get('idempotent', False):
            raise ValueError('Only explicitly idempotent workers may retry')
        for key in ('heartbeat_seconds', 'stage_seconds'):
            if key in item and (type(item[key]) not in (int, float) or not 1 <= item[key] <= 86400):
                raise ValueError('Unsafe ' + key)
    for key in ('heartbeat_seconds', 'stage_seconds', 'poll_seconds'):
        if key in obj and (type(obj[key]) not in (int,float) or not 1 <= obj[key] <= 86400):
            raise ValueError('Unsafe ' + key)
    return obj


def require_mission(root, mission_id):
    if not isinstance(mission_id, str) or not mission_id.startswith('MISSION-') or '/' in mission_id or '\\' in mission_id:
        raise ValueError('Invalid mission id')
    path = comms.mission_path(root, mission_id)
    if not path.is_dir() or path.is_symlink():
        raise ValueError('Missing mission')
    state = dispatch.load(path)  # verifies dispatcher event hash chain
    if not state.get('owner_approval_granted') is False or not state.get('release_authorised') is False:
        raise ValueError('Owner gate invariant violated')
    return state


def incident(db, root, run_id, mission, stage, kind, reason, recipients=('Checker', 'Director')):
    db.execute('INSERT OR IGNORE INTO incidents(mission_id,run_id,stage,kind,reason,at) VALUES(?,?,?,?,?,?)',
               (mission,run_id,stage,kind,reason,now()))
    db.commit()
    row = db.execute('SELECT id,sent FROM incidents WHERE run_id=? AND kind=?',(run_id,kind)).fetchone()
    if row['sent']:
        return
    con = bus.open_bus(root / 'outbox')
    try:
        if not bus.verify(con):
            raise ValueError('Outbox integrity check failed; cannot send notice')
        body = json.dumps({'mission_id': mission,'stage':stage,'code':kind,'detail':reason,
                           'reviewer_mode':'supervisor_system_event',
                           'checker_verdict':'NOT_RUN','owner_release_authorised':False},sort_keys=True)
        bus.post(con, mission_id=mission, sender='System', recipients=list(recipients),
                 kind='defect', subject='WATCHDOG: '+kind, body=body,
                 author_mode='system_event')
    finally:
        con.close()
    db.execute('UPDATE incidents SET sent=1 WHERE id=?',(row['id'],))
    db.commit()


def record(db, mission, stage_index, run_id, attempt, status, reason=None, pid=None, exit_code=None):
    stage, role = dispatch.STAGES[stage_index]
    t = now()
    db.execute('''INSERT INTO runs(mission_id,stage_index,stage,role,run_id,attempt,status,started,heartbeat,pid,exit_code,reason,changed)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(mission_id) DO UPDATE SET
    stage_index=excluded.stage_index,stage=excluded.stage,role=excluded.role,run_id=excluded.run_id,
    attempt=excluded.attempt,status=excluded.status,started=excluded.started,heartbeat=excluded.heartbeat,
    pid=excluded.pid,exit_code=excluded.exit_code,reason=excluded.reason,changed=excluded.changed''',
    (mission,stage_index,stage,role,run_id,attempt,status,t,t,pid,exit_code,reason,t))
    db.commit()
    return db.execute('SELECT * FROM runs WHERE mission_id=?',(mission,)).fetchone()


def update(db, mission, status, reason=None, exit_code=None):
    db.execute('UPDATE runs SET status=?,reason=?,exit_code=?,changed=? WHERE mission_id=?',
               (status,reason,exit_code,now(),mission))
    db.commit()


def start_adapter(root, row, cfg, processes):
    spec = cfg['stages'][row['stage']]
    # Safe inherited environment: never leak unrelated app/API/cloud secrets to a worker.
    inherited = ('PATH', 'LANG', 'LC_ALL', 'TZ', 'HOME', 'TMPDIR', 'PYTHONIOENCODING')
    env = {k:os.environ[k] for k in inherited if k in os.environ}
    env.update({'POS_ROOT':str(root),'POS_MISSION_ID':row['mission_id'],
                'POS_STAGE':row['stage'],'POS_ROLE':row['role'],
                'POS_RUN_ID':row['run_id'],'POS_SUPERVISOR':str(Path(__file__).resolve())})
    # No shell interpolation; worker is a trusted executable selected in config.
    stdout_dir = root / 'supervisor' / 'logs'
    stdout_dir.mkdir(mode=0o700, exist_ok=True)
    os.chmod(stdout_dir,0o700)
    file = stdout_dir/(row['run_id']+'.log')
    with file.open('xb') as log:
        os.chmod(file,0o600)
        proc = subprocess.Popen(spec['argv'], cwd=root, env=env, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    processes[row['run_id']] = proc
    return proc.pid


def scan(root, cfg, processes=None, *, launch=False):
    """One polling cycle. Mutates supervisor journal; never bypasses mission router."""
    root = validate_root(root)
    processes = processes if processes is not None else {}
    db = database(root)
    results = []
    try:
        if launch:
            con = bus.open_bus(root / 'outbox')
            try:
                if not bus.verify(con):
                    raise ValueError('Outbox integrity invalid: refusing worker launch')
            finally:
                con.close()
        missions = root / 'missions'
        if not missions.exists():
            return []
        for path in sorted(missions.iterdir()):
            if not path.is_dir() or path.is_symlink() or not path.name.startswith('MISSION-'):
                continue
            mission = path.name
            try:
                state = require_mission(root, mission)
            except (ValueError,OSError) as e:
                # Evidence integrity failure must never be 'repaired' automatically.
                results.append({'mission':mission,'status':'INTEGRITY_BLOCKED','reason':str(e)})
                continue
            prev = db.execute('SELECT * FROM runs WHERE mission_id=?',(mission,)).fetchone()
            if state['status'] != 'ACTIVE':
                if prev and prev['status'] not in ('TERMINAL','NEEDS_RECONCILIATION'):
                    update(db,mission,'TERMINAL',state['status'])
                results.append({'mission':mission,'status':state['status']})
                continue
            index = state['stage_index']
            stage, role = dispatch.STAGES[index]
            if not prev or prev['stage_index'] != index:
                if prev and prev['status']=='RUNNING' and prev['run_id'] in processes:
                    # Old stage advanced; never force-kill its reporter mid-flight.
                    proc = processes.pop(prev['run_id'])
                    if proc.poll() is None:
                        try: os.killpg(proc.pid,signal.SIGTERM)
                        except ProcessLookupError: pass
                row = record(db,mission,index,secrets.token_hex(12),1,'PENDING')
            else:
                row = prev
            spec = cfg.get('stages',{}).get(stage)
            if spec is None:
                if row['status']=='PENDING':
                    update(db,mission,'NEEDS_WORKER','No authorised stage worker configured')
                    incident(db,root,row['run_id'],mission,stage,'WORKER_NOT_CONFIGURED',
                             'Requires an explicit trusted stage-worker configuration')
                results.append({'mission':mission,'stage':stage,'status':'NEEDS_WORKER'})
                continue
            if row['status'] in ('NEEDS_RECONCILIATION','STALLED','FAILED','AWAITING_REPORT'):
                results.append({'mission':mission,'stage':stage,'status':row['status']})
                continue
            if row['status']=='RUNNING':
                if not launch:
                    results.append({'mission':mission,'stage':stage,'status':'RUNNING',
                                    'observation':'non-launching scan; process ownership not assessed'})
                    continue
                proc = processes.get(row['run_id'])
                if proc is None:
                    # After supervisor crash/restart PID reuse is possible; do not kill or replay.
                    update(db,mission,'NEEDS_RECONCILIATION','Supervisor lost child-process ownership')
                    incident(db,root,row['run_id'],mission,stage,'ORPHANED_RUN',
                             'Manual process reconciliation required before any retry')
                    results.append({'mission':mission,'stage':stage,'status':'NEEDS_RECONCILIATION'})
                    continue
                code = proc.poll()
                if code is not None:
                    processes.pop(row['run_id'],None)
                    update(db,mission,'AWAITING_REPORT' if code==0 else 'FAILED',
                           'Worker exited; check stage handoff receipt',code)
                    incident(db,root,row['run_id'],mission,stage,'EXIT_WITHOUT_HANDOFF',
                             f'Worker exited {code}, no accepted stage progression')
                    results.append({'mission':mission,'stage':stage,'status':'AWAITING_REPORT' if code==0 else 'FAILED'})
                    continue
                hb = spec.get('heartbeat_seconds',cfg.get('heartbeat_seconds',DEFAULT_HEARTBEAT_SECONDS))
                limit = spec.get('stage_seconds',cfg.get('stage_seconds',DEFAULT_STAGE_SECONDS))
                if now()-row['heartbeat']>hb or now()-row['started']>limit:
                    try: os.killpg(proc.pid,signal.SIGTERM)
                    except ProcessLookupError: pass
                    processes.pop(row['run_id'],None)
                    reason='heartbeat timeout' if now()-row['heartbeat']>hb else 'stage duration limit'
                    update(db,mission,'STALLED',reason)
                    incident(db,root,row['run_id'],mission,stage,'STAGE_STALLED',reason)
                    # Safe retries require explicit idempotence and a separate manual reconciliation.
                    results.append({'mission':mission,'stage':stage,'status':'STALLED'})
                    continue
                results.append({'mission':mission,'stage':stage,'status':'RUNNING','attempt':row['attempt']})
                continue
            if row['status'] in ('PENDING','NEEDS_WORKER') and launch:
                try:
                    pid = start_adapter(root,row,cfg,processes)
                    db.execute('UPDATE runs SET status=?,pid=?,changed=? WHERE mission_id=?',('RUNNING',pid,now(),mission))
                    db.commit()
                    results.append({'mission':mission,'stage':stage,'status':'RUNNING','pid':pid})
                except (OSError,ValueError) as e:
                    update(db,mission,'FAILED','Adapter launch failure: '+str(e))
                    incident(db,root,row['run_id'],mission,stage,'LAUNCH_FAILED',str(e))
                    results.append({'mission':mission,'stage':stage,'status':'FAILED'})
            else:
                results.append({'mission':mission,'stage':stage,'status':row['status']})
        return results
    finally:
        db.close()


def heartbeat(root, mission, run_id):
    root = validate_root(root)
    require_mission(root,mission)
    db = database(root)
    try:
        row = db.execute('SELECT * FROM runs WHERE mission_id=?',(mission,)).fetchone()
        if not row or row['run_id']!=run_id or row['status']!='RUNNING':
            raise ValueError('Unknown/inactive run id')
        db.execute('UPDATE runs SET heartbeat=? WHERE mission_id=?',(now(),mission));db.commit()
        return {'mission':mission,'run_id':run_id,'heartbeat':utc(),'status':'RUNNING'}
    finally: db.close()


def retry(root, mission, *, acknowledged=False):
    """Owner/operator explicitly reconciles prior run; retry only idempotent adapter."""
    if not acknowledged:raise ValueError('Operator reconciliation acknowledgment required')
    root=validate_root(root)
    state=require_mission(root,mission)
    if state['status']!='ACTIVE':raise ValueError('Mission no longer active')
    db=database(root)
    try:
        row=db.execute('SELECT * FROM runs WHERE mission_id=?',(mission,)).fetchone()
        if not row or row['stage_index']!=state['stage_index'] or row['status'] not in ('STALLED','FAILED','NEEDS_RECONCILIATION','AWAITING_REPORT'):
            raise ValueError('No eligible failed run to reconcile')
        return row['stage'],row['attempt']
    finally:db.close()


def confirmed_retry(root, cfg, mission, *, acknowledged=False):
    stage,attempt=retry(root,mission,acknowledged=acknowledged)
    spec=cfg.get('stages',{}).get(stage,{})
    if not spec.get('idempotent') or attempt>spec.get('max_retries',0):
        raise ValueError('No permitted retry: explicit idempotence and remaining budget required')
    db=database(root)
    try:
        state=require_mission(root,mission)
        row=record(db,mission,state['stage_index'],secrets.token_hex(12),attempt+1,'PENDING')
        incident(db,validate_root(root),row['run_id'],mission,stage,'MANUAL_RETRY_QUEUED',
                 'Operator reconciled previous process and explicitly requested safe retry')
        return {'mission':mission,'stage':stage,'status':'PENDING','attempt':attempt+1}
    finally: db.close()


def status(root):
    db=database(root)
    try:
        rows=[dict(row) for row in db.execute('SELECT * FROM runs ORDER BY mission_id')]
        problems=[dict(row) for row in db.execute('SELECT mission_id,stage,kind,reason,at,sent FROM incidents ORDER BY id')]
        return {'execution_host':'LOCAL_PROCESS_ONLY','independent_ai_workers':'NOT_CONNECTED',
                'owner_approval_service':'NOT_CONFIGURED','runs':rows,'incidents':problems}
    finally:db.close()


def cli():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root',required=True)
    sp=ap.add_subparsers(dest='action',required=True)
    a=sp.add_parser('serve');a.add_argument('--config',required=True)
    a=sp.add_parser('tick');a.add_argument('--config',required=True)
    a=sp.add_parser('heartbeat');a.add_argument('--mission',required=True);a.add_argument('--run-id',required=True)
    a=sp.add_parser('retry');a.add_argument('--config',required=True);a.add_argument('--mission',required=True)
    a.add_argument('--reconciled',action='store_true')
    sp.add_parser('status')
    args=ap.parse_args()
    try:
        root=validate_root(args.root)
        if args.action=='heartbeat': result=heartbeat(root,args.mission,args.run_id)
        elif args.action=='retry':result=confirmed_retry(root,load_config(args.config),args.mission,acknowledged=args.reconciled)
        elif args.action=='status':result=status(root)
        else:
            cfg=load_config(args.config)
            if args.action=='tick':result=scan(root,cfg,launch=False)
            else:
                # Only one host-side supervisor may own the child process table.
                import fcntl
                lock_path=root/'supervisor.lock'
                with lock_path.open('a+') as lock:
                    os.chmod(lock_path,0o600)
                    try: fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                    except BlockingIOError:raise ValueError('A supervisor is already running for this root')
                    children={}
                    print('Council local supervisor started; model workers not connected',flush=True)
                    while True:
                        result=scan(root,cfg,children,launch=True)
                        print(json.dumps({'at':utc(),'missions':result}),flush=True)
                        time.sleep(cfg.get('poll_seconds',5))
        print(json.dumps(result,indent=2))
    except (OSError,ValueError,sqlite3.Error) as e:
        ap.exit(2,'BLOCKED: '+str(e)+'\n')

if __name__=='__main__':cli()
