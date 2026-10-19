#!/usr/bin/env python3
"""BRAIN-P001 local, explicitly authorised read-only source snapshotter.

Not a daemon, hosted integration, independent reviewer, dispatcher, or runtime watchdog.
Only writes to a caller-supplied private Brain ledger (never its input sources).
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import uuid

SCHEMA = 'brain.snapshot/0.1.0'
STAGES = ('COUNTERBALANCE_PROMPT_GATE','DIRECTORS_SPEC_PACKET',
          'RESEARCHER_NORTHSTAR_EVIDENCE','CHECKER_G0','EXECUTOR_BUILD',
          'CHECKER_G1','DIRECTORS_ALIGNMENT')
ACTORS = ('Counterweight','Director','Researcher','Checker','Executor','Checker','Director')


def utcnow():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00','Z')


def parse_time(value):
    if isinstance(value, (int,float)):
        return datetime.fromtimestamp(value, timezone.utc)
    if isinstance(value,str) and value:
        try:
            dt=datetime.fromisoformat(value.replace('Z','+00:00'))
            return dt.astimezone(timezone.utc) if dt.tzinfo else None
        except ValueError:
            return None
    return None


def canon(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')


def verify_events(lines):
    prev='GENESIS'
    for line in lines:
        event=json.loads(line)
        if not isinstance(event,dict) or 'hash' not in event: return False
        received=event.pop('hash')
        if event.get('prev') != prev or hashlib.sha256(canon(event)).hexdigest()!=received:
            return False
        prev=received
    return True


def reports_match_event_journal(state, lines):
    """Verify each reported stage handoff has a same-sha journal entry.

    This establishes journal consistency, NOT correctness of the review content.
    """
    events=[json.loads(line) for line in lines]
    tokens={(e.get('value',{}).get('stage'), e.get('value',{}).get('verdict'),
             e.get('value',{}).get('hash'))
            for e in events if e.get('type')=='ROLE_REPORT'}
    return all((r.get('stage'),r.get('verdict'),r.get('sha256')) in tokens
               for r in (state.get('reports') or []))


def dependencies_cycles(graph):
    visited,active,cycles=set(),[],set()
    def visit(v):
        if v in active:
            cyc=active[active.index(v):]+[v]
            cycles.add(tuple(cyc))
            return
        if v in visited:return
        active.append(v)
        for nxt in graph.get(v,[]):
            if nxt in graph:visit(nxt)
        active.pop(); visited.add(v)
    for v in graph:visit(v)
    return [list(c) for c in sorted(cycles)]


def classify(state, run=None, now=None, config=None):
    """Classifies source assertions without claiming real-time process probes.

    run is the host supervisor's *record*, not a directly observed OS process.
    No source configured heartbeat -> liveness stays UNKNOWN.
    """
    now=now or utcnow(); config=config or {}
    result={'execution':'UNKNOWN','liveness':'UNKNOWN','progress':'UNKNOWN',
            'claim_integrity':'UNASSESSED','finding':None,'cause_status':'UNKNOWN',
            'last_verified_progress':None,'next_actor':'Director','next_action':'Inspect current source receipts'}
    status=state.get('status')
    if status=='AWAITING_OWNER':
        result.update(execution='AWAITING_OWNER',progress='WAITING_AS_EXPECTED',
                      next_actor='Owner',next_action='Decide exact candidate/version and release action')
    elif status=='HELD':
        result.update(execution='PAUSED',progress='WAITING_AS_EXPECTED',
                      next_action='Review gate hold: '+str(state.get('hold_reason') or 'unspecified'))
    elif status=='ACTIVE':
        result.update(next_action='Check stage handoff and worker evidence')
    elif status in ('CANCELLED','FAILED'):
        result.update(execution=status,liveness='STOPPED',next_action='Confirm closure/owner decision')
    reports=state.get('reports') or []
    if reports:
        result['last_verified_progress']='Journal-linked stage handoff (content not independently reviewed): '+str(reports[-1].get('stage','UNKNOWN'))
        if status=='ACTIVE' and not run:
            result['progress']='UNKNOWN'  # Prior stage completion is not proof current stage is advancing.
    if not run or status!='ACTIVE':
        return result
    run_status=run.get('status')
    if run.get('stage')!=STAGES[state.get('stage_index',0)]:
        result.update(finding='Run/stage mismatch; inspect current attempt',next_action='Reconcile supervisor run against current mission stage')
        return result
    if run_status=='PENDING':
        result.update(execution='QUEUED',next_action='Check capacity and declared start SLA')
    elif run_status=='RUNNING':
        result['execution']='RUNNING'  # from journal, not live process attestation
        hb=parse_time(run.get('heartbeat'))
        expected=config.get('heartbeat_seconds')
        if expected and hb:
            result['liveness']='ALIVE' if timedelta(0)<=now-hb<=timedelta(seconds=3*expected) else 'STALE'
            if result['liveness']=='STALE':
                result.update(finding='Stale recorded heartbeat: requires process/telemetry recheck',
                              next_actor='Host operator',next_action='Read direct process/worker receipt; do not conclude freeze')
        # No accepted checkpoint for the active stage can yet be confirmed from this record.
        # Do not impose generic stage timer for training/rendering.
        gap=config.get('max_progress_gap_seconds')
        accepted=parse_time(run.get('last_qualifying_checkpoint'))
        started=parse_time(run.get('started'))
        anchor=accepted or started
        if gap and anchor and now-anchor > timedelta(seconds=gap):
            result.update(progress='SUSPECTED_STALL',finding='Declared stage progress gap exceeded; confirm with fresh evidence',
                          next_action='Inspect latest checkpoint/tool receipt for current run')
        elif accepted:
            result.update(progress='ADVANCING',last_verified_progress='Qualified checkpoint: '+iso(accepted))
    elif run_status in ('FAILED','AWAITING_REPORT','STALLED','NEEDS_RECONCILIATION'):
        result['execution']='FAILED' if run_status=='FAILED' else 'UNKNOWN'
        result['liveness']='STOPPED' if run_status in ('FAILED','AWAITING_REPORT') else 'UNKNOWN'
        if run_status in ('FAILED','AWAITING_REPORT') and run.get('exit_code') is not None:
            result.update(progress='CONFIRMED_STALL',finding='Current supervisor run exited without accepted stage successor',
                          next_actor='Host operator',next_action='Inspect exit logs, reconciliation and handoff receipt')
        else:
            result.update(progress='SUSPECTED_STALL',finding='Host journal flags '+run_status+'; independent execution check needed',
                          next_actor='Host operator',next_action='Reconcile process ownership and current stage')
    elif run_status=='NEEDS_WORKER':
        result.update(execution='QUEUED',finding='No stage worker configured',
                      next_actor='Director',next_action='Assign authorised worker adapter; do not mark running')
    return result


def evaluate_claim(claim, evidence):
    """Evidence is an explicit, scoped claim-specific verification assessment.

    Do not equate existence or hash identity with factual support.
    """
    if not evidence or evidence.get('task_id')!=claim.get('task_id') or evidence.get('run_id')!=claim.get('run_id'):
        return 'UNSUPPORTED'
    for field in ('version','environment'):
        if claim.get(field) and evidence.get(field)!=claim.get(field):return 'UNSUPPORTED'
    if evidence.get('direct_conflict') and evidence.get('inspected'):
        return 'CONTRADICTED'
    if evidence.get('inspected') and evidence.get('relevant') and evidence.get('current') and evidence.get('supports_exact_claim'):
        return 'SUPPORTED'
    return 'UNSUPPORTED'


def readonly_db(path):
    return sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)


def read_supervisor(root):
    src=root/'supervisor'/'state.sqlite3'
    if not src.is_file():return None,'Supervisor journal is not connected'
    con=readonly_db(src);con.row_factory=sqlite3.Row
    try:
        runs={r['mission_id']:dict(r) for r in con.execute('SELECT * FROM runs')}
        incidents=[dict(r) for r in con.execute('SELECT * FROM incidents')]
        return {'runs':runs,'incidents':incidents},None
    finally:con.close()


def collect_missions(root):
    folder=root/'missions' if (root/'missions').is_dir() else root
    paths=sorted(p for p in folder.glob('MISSION-*') if p.is_dir() and not p.is_symlink())
    entries=[]
    for p in paths:
        try:
            state=json.loads((p/'mission.json').read_text(encoding='utf8'))
            lines=(p/'events.jsonl').read_text(encoding='utf8').splitlines()
            if state.get('schema')!='project-os-council-routing/0.2.0' or not verify_events(lines):
                raise ValueError('mission schema or event hash chain invalid')
            if state.get('mission_id')!=p.name:raise ValueError('mission path/ID mismatch')
            if not reports_match_event_journal(state,lines):
                raise ValueError('mission reports do not match hashed handoff journal')
            entries.append({'source':str(p),'state':state,'events_count':len(lines),'error':None})
        except (OSError,ValueError,KeyError,TypeError) as exc:
            entries.append({'source':str(p),'state':None,'error':str(exc)})
    return entries


def read_archivist(dbpath, projects):
    con=readonly_db(dbpath);con.row_factory=sqlite3.Row
    try:
        matches=[]
        for project in sorted(set(projects)):
            # Only ordinary, council-visible events, and only projects already selected in Council.
            rows=con.execute("SELECT seq, event_type, occurred_at, recorded_at, payload FROM events WHERE project=? AND visibility='council' AND sensitivity='ordinary' ORDER BY seq DESC LIMIT 200",(project,))
            for row in rows:
                obj=json.loads(row['payload'])
                matches.append({'project':project,'seq':row['seq'],'event_type':row['event_type'],
                                'source_ref':obj.get('source_ref'),'verified_status':obj.get('verified_status'),
                                'occurred_at':row['occurred_at'],'recorded_at':row['recorded_at']})
        return matches
    finally:con.close()


def collect_registers(directory):
    """Read only explicitly configured master-register files, never recurse through arbitrary files.

    A master-register file is a source assertion, NOT an independently verified worker receipt.
    """
    folder=Path(directory).resolve()
    if not folder.is_dir():
        raise FileNotFoundError('Project master-register directory not accessible')
    records=[];errors=[]
    # Each file is one declared current register; group/project identity comes from its contents.
    for entry in sorted(folder.iterdir()):
        if entry.is_symlink() or not entry.is_file() or entry.suffix!='.json': continue
        try:
            body=json.loads(entry.read_text(encoding='utf-8'))
            if body.get('schema')!='project-os-register/0.1.1':
                raise ValueError('Unsupported register schema')
            project=body.get('project')
            if not isinstance(project,str) or not project:
                raise ValueError('Missing project identity')
            if not isinstance(body.get('tasks'),list):
                raise ValueError('Missing task array')
            seen=set()
            for row in body['tasks']:
                if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not row['id']:
                    raise ValueError('Invalid task ID')
                tid=project+'::'+row['id']
                if tid in seen: raise ValueError('Repeated ID within register')
                seen.add(tid)
                raw=row.get('state')
                wait=raw=='AWAITING_OWNER'
                records.append({'task_id':tid,'source_ref':str(entry),'project':project,
                       'execution':'AWAITING_OWNER' if wait else 'UNKNOWN',
                       'liveness':'UNKNOWN','progress':'WAITING_AS_EXPECTED' if wait else 'UNKNOWN',
                       'claim_integrity':'UNASSESSED','source_claimed_state':raw,
                       'owner':row.get('assignee'),'title':row.get('title'),
                       'dependencies':[project+'::'+dep for dep in row.get('dependencies',[])],
                       'last_verified_progress':None, 'finding':None,
                       'next_actor':'Director','next_action':'Reconcile task state against current worker and review receipts'})
        except (OSError,ValueError,TypeError) as exc:
            errors.append({'source_ref':str(entry),'reason':str(exc)})
    return records, errors


def snapshot(council_root, archivist_db=None, config=None, now=None, registers_root=None):
    now=now or utcnow(); sources=[];tasks=[];errors=[];config=config or {}
    def source(name,scope,status,error=None,records=None):
        sources.append({'source_id':name,'scope':scope,'status':status,'observed_at':iso(now),
                        'last_successful_read':iso(now) if status=='READ' else None,
                        'declared_global_completeness':False,'records':records,'error':error})
    root=Path(council_root).resolve()
    if not root.is_dir():
        source('council',str(root),'UNAVAILABLE','Council root does not exist')
        errors.append('Council inventory unavailable')
        missions=[];supervisor=None
    else:
        try:
            missions=collect_missions(root)
            source('council',str(root),'READ',records=len(missions))
        except (OSError,ValueError) as e:
            source('council',str(root),'ERROR',str(e));errors.append(str(e));missions=[]
        try:
            supervisor,err=read_supervisor(root)
            source('supervisor',str(root/'supervisor'/'state.sqlite3'),'READ' if not err else 'UNAVAILABLE',err,
                   len(supervisor['runs']) if supervisor else None)
        except (OSError,sqlite3.Error,ValueError) as e:
            source('supervisor',str(root/'supervisor'/'state.sqlite3'),'ERROR',str(e));errors.append(str(e));supervisor=None
    if registers_root is None:
        source('task-registers','not configured','UNAVAILABLE','No authorised master-register folder supplied')
    else:
        try:
            registered,register_errors=collect_registers(registers_root)
            tasks.extend(registered)
            source('task-registers',str(registers_root),'READ' if not register_errors else 'ERROR',
                   'Invalid register(s)' if register_errors else None,records=len(registered))
            for err in register_errors:
                errors.append(err['source_ref']+': '+err['reason'])
        except (OSError,ValueError) as exc:
            source('task-registers',str(registers_root),'ERROR',str(exc));errors.append(str(exc))
    projects=[t.get('project') for t in tasks if t.get('project')]
    for item in missions:
        state=item.get('state')
        if state is None:
            tasks.append({'task_id':Path(item['source']).name,'source_ref':item['source'],'project':None,
                          'execution':'UNKNOWN','liveness':'UNKNOWN','progress':'UNKNOWN','claim_integrity':'UNASSESSED',
                          'finding':'Source integrity unavailable: '+item['error'],'next_actor':'Host operator',
                          'next_action':'Restore readable, verified mission record', 'last_verified_progress':None})
            errors.append(item['source']+': '+item['error']);continue
        projects.append(state.get('project'))
        index=state.get('stage_index',0)
        if not isinstance(index,int) or index<0 or index>=len(STAGES):
            errors.append(item['source']+': invalid stage index');continue
        run=(supervisor or {}).get('runs',{}).get(state['mission_id'])
        t=classify(state,run,now,config)
        t.update(task_id=state['mission_id'],project=state.get('project'),mission_id=state['mission_id'],
                 parent_id=None,source_ref=item['source']+'/mission.json',
                 prompt_sha256=state.get('original_prompt_sha256'),baseline_ref=state.get('baseline_ref'),
                 stage=STAGES[index],owner=ACTORS[index] if state.get('status')=='ACTIVE' else t['next_actor'],
                 run_id=run.get('run_id') if run else None,
                 run_status_reported=run.get('status') if run else None,
                 recorded_owner_release=state.get('release_authorised') is True,
                 proof_of_independent_agent=False)
        tasks.append(t)
    if archivist_db is None:
        source('archivist','not configured','UNAVAILABLE','No scoped Archivist store configured')
    else:
        try:
            ev=read_archivist(archivist_db,[p for p in projects if p])
            source('archivist',str(archivist_db),'READ',records=len(ev))
        except (OSError,sqlite3.Error,ValueError,KeyError) as e:
            source('archivist',str(archivist_db),'ERROR',str(e));errors.append(str(e))
    count={}
    for t in tasks:
        for key in (t['execution'],t['liveness'],t['progress'],t['claim_integrity']):
            count[key]=count.get(key,0)+1
    # There is no declared complete inventory of all projects/chat tasks.
    coverage='PARTIAL' if any(s['status']=='READ' for s in sources) else 'UNKNOWN'
    return {'schema':SCHEMA,'scan_id':str(uuid.uuid4()),'mode':'SNAPSHOT','at':iso(now),
            'coverage':coverage,'coverage_note':'Global task inventory is not established; only named local source scope was inspected',
            'sources':sources,'tasks':tasks,'counts':count,'errors':errors,
            'live_worker_connections_verified':False,'persistent_monitoring_verified':False,
            'owner_approval_inferred':False}


def save_snapshot(dbpath,report):
    dest=Path(dbpath).resolve()
    dest.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(dest)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS scans(scan_id TEXT PRIMARY KEY, at TEXT NOT NULL, mode TEXT NOT NULL, coverage TEXT NOT NULL, payload TEXT NOT NULL)')
        with db:
            db.execute('INSERT INTO scans VALUES(?,?,?,?,?)',(report['scan_id'],report['at'],report['mode'],report['coverage'],json.dumps(report,sort_keys=True)))
    finally:db.close()


def latest(dbpath):
    con=readonly_db(dbpath)
    try:
        row=con.execute('SELECT payload FROM scans ORDER BY rowid DESC LIMIT 1').fetchone()
        return json.loads(row[0]) if row else None
    finally:con.close()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='cmd',required=True)
    scan=sub.add_parser('scan');scan.add_argument('--council-root',required=True)
    scan.add_argument('--archivist-db');scan.add_argument('--registers-root');scan.add_argument('--db',required=True)
    scan.add_argument('--heartbeat-seconds',type=float)
    scan.add_argument('--max-progress-gap-seconds',type=float)
    report=sub.add_parser('report');report.add_argument('--db',required=True)
    args=p.parse_args(argv)
    try:
        if args.cmd=='scan':
            # Refuse collocating Brain's ledger inside monitored directories.
            council=Path(args.council_root).resolve();out=Path(args.db).resolve()
            if out==council or council in out.parents:
                raise ValueError('Brain diagnostic ledger must be outside Council source root')
            if args.archivist_db and out==Path(args.archivist_db).resolve():
                raise ValueError('Brain diagnostic ledger must not replace Archivist source')
            cfg={}
            for field in ('heartbeat_seconds','max_progress_gap_seconds'):
                v=getattr(args,field)
                if v is not None:
                    if not 1 <= v <= 86400*30:raise ValueError('Invalid configured deadline')
                    cfg[field]=v
            data=snapshot(council,args.archivist_db,cfg,registers_root=args.registers_root)
            save_snapshot(args.db,data)
        else:
            data=latest(args.db)
            if data is None:raise ValueError('No scans in Brain ledger')
        print(json.dumps(data,indent=2))
    except (ValueError,OSError,sqlite3.Error,json.JSONDecodeError) as e:
        print(json.dumps({'status':'BRAIN_DEGRADED','reason':str(e)}),file=sys.stderr);return 2
    return 0

if __name__=='__main__':
    raise SystemExit(main())
