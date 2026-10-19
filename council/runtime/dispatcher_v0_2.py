#!/usr/bin/env python3
"""Project OS v0.2.x offline mission router: accepts supplied role reports, never invokes AI.

For new missions only. Historical v0.1.x state-machine files remain available.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

SCHEMA = 'project-os-council-routing/0.2.0'
VERSION = '0.2.1'
STAGES = (
    ('COUNTERBALANCE_PROMPT_GATE','Counterweight'),
    ('DIRECTORS_SPEC_PACKET','Director'),
    ('RESEARCHER_NORTHSTAR_EVIDENCE','Researcher'),
    ('CHECKER_G0','Checker'),
    ('EXECUTOR_BUILD','Executor'),
    ('CHECKER_G1','Checker'),
    ('DIRECTORS_ALIGNMENT','Director'),
)
SUCCESS = {'COUNTERBALANCE_PROMPT_GATE':'VERIFIED', 'DIRECTORS_SPEC_PACKET':'COMPLETE',
           'RESEARCHER_NORTHSTAR_EVIDENCE':'COMPLETE','CHECKER_G0':'PASS',
           'EXECUTOR_BUILD':'READY_FOR_CHECK','CHECKER_G1':'PASS',
           'DIRECTORS_ALIGNMENT':'COMPLETE'}
ALLOWED = {
    'COUNTERBALANCE_PROMPT_GATE':{'VERIFIED','NEEDS_REVISION','BLOCKED'},
    'DIRECTORS_SPEC_PACKET':{'COMPLETE','BLOCKED'},
    'RESEARCHER_NORTHSTAR_EVIDENCE':{'COMPLETE','UNVERIFIED','BLOCKED'},
    'CHECKER_G0':{'PASS','FAIL','UNVERIFIED'},
    'EXECUTOR_BUILD':{'READY_FOR_CHECK','BLOCKED'},
    'CHECKER_G1':{'PASS','FAIL','UNVERIFIED'},
    'DIRECTORS_ALIGNMENT':{'COMPLETE','DRIFT_RISK','BLOCKED'},
}

def digest(v):
    return hashlib.sha256(v).hexdigest()

def canon(v):
    return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')

def timestamp():
    return datetime.now(timezone.utc).isoformat()

def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def write(path,v):
    p=Path(path);tmp=p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(v,indent=2,ensure_ascii=False)+'\n',encoding='utf-8');tmp.replace(p)

def event(mission, kind, value):
    path=Path(mission)/'events.jsonl'
    lines=path.read_text(encoding='utf-8').splitlines() if path.exists() else []
    prev=json.loads(lines[-1])['hash'] if lines else 'GENESIS'
    payload={'type':kind,'value':value,'timestamp':timestamp(),'prev':prev}
    with path.open('a',encoding='utf-8') as f:f.write(json.dumps({**payload,'hash':digest(canon(payload))},sort_keys=True)+'\n')

def verify_events(mission):
    try:
        prev='GENESIS'
        for line in (Path(mission)/'events.jsonl').read_text(encoding='utf-8').splitlines():
            x=json.loads(line);h=x.pop('hash')
            if x['prev']!=prev or digest(canon(x))!=h:return False
            prev=h
        return True
    except (OSError,ValueError,TypeError,KeyError):return False

def handoff(state):
    if state['status']!='ACTIVE':
        return ('OWNER GATE: Await exact-version and action-specific owner approval. No release authorised.'
                if state['status']=='AWAITING_OWNER' else 'HELD: '+str(state['hold_reason']))
    st=STAGES[state['stage_index']]
    return f"{state['mission_id']} / {st[0]} / actor {st[1]}\nPrompt SHA256: {state['original_prompt_sha256']}\nDo not skip this stage or claim an independent agent was called without evidence."

def save(mission,state):
    state['updated_utc']=timestamp()
    write(Path(mission)/'mission.json',state)
    (Path(mission)/'NEXT_HANDOFF.txt').write_text(handoff(state)+'\n',encoding='utf-8')

def start(root,owner_prompt,project,baseline_ref):
    if not all(isinstance(x,str) and x.strip() for x in (owner_prompt,project,baseline_ref)):
        raise ValueError('Owner prompt, project and baseline are required')
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=True)
    path=root/('MISSION-'+datetime.now(timezone.utc).strftime('%Y%m%d')+'-'+uuid4().hex[:10].upper())
    path.mkdir(mode=0o700)
    state={'schema':SCHEMA,'version':VERSION,'mission_id':path.name,'original_prompt':owner_prompt,
           'original_prompt_sha256':digest(owner_prompt.encode('utf-8')),'project':project,
           'baseline_ref':baseline_ref,'stage_index':0,'status':'ACTIVE','hold_reason':None,
           'reports':[],'spec_packet_sha256':None,'executor_report_sha256':None,
           'owner_approval_granted':False,'release_authorised':False,
           'execution_mode':'offline_manual_handoffs','created_utc':timestamp()}
    event(path,'OWNER_REQUEST',{'prompt_hash':state['original_prompt_sha256']});save(path,state)
    return path

def load(mission):
    s=read(Path(mission)/'mission.json')
    if s.get('schema')!=SCHEMA or not verify_events(mission):raise ValueError('Schema mismatch or tampered event history')
    return s

def checked_ref(x,mission):
    if not isinstance(x,dict) or 'path' not in x or 'sha256' not in x:raise ValueError('artifact requires path+sha256')
    p=Path(x['path']).resolve(strict=True)
    if not p.is_file():raise ValueError('Expected artifact file')
    # Trusted local sandbox, limit references to mission subtree.
    if not p.is_relative_to(Path(mission).resolve()):raise ValueError('Evidence outside mission directory')
    if digest(p.read_bytes())!=x['sha256']:raise ValueError('Artifact sha256 mismatch')
    return {'path':str(p),'sha256':x['sha256']}

def submit(mission,report):
    s=load(mission)
    if s['status']!='ACTIVE':raise ValueError('Mission is held or at owner gate')
    if not isinstance(report,dict):raise ValueError('Report must be object')
    stage,role=STAGES[s['stage_index']]
    if report.get('stage')!=stage or report.get('role')!=role:raise ValueError('Out-of-order stage or incorrect role')
    verdict=report.get('verdict')
    if verdict not in ALLOWED[stage]:raise ValueError('Invalid verdict')
    if not isinstance(report.get('summary'),str) or not report['summary'].strip():raise ValueError('Summary required')
    if report.get('owner_approval_granted') or report.get('release_authorised'):raise ValueError('Agent cannot approve release')
    if not isinstance(report.get('reviewer_mode'),str):raise ValueError('Explicit reviewer mode is required')
    if report['reviewer_mode']=='connected_agent_evidenced':raise ValueError('Independent agent cannot self-attest through report')
    refs=report.get('artifact_refs',[])
    if not isinstance(refs,list):raise ValueError('artifact_refs must be a list')
    verified=[checked_ref(x,mission) for x in refs]
    if stage=='COUNTERBALANCE_PROMPT_GATE':
        if report.get('original_prompt')!=s['original_prompt']:raise ValueError('Owner prompt changed')
        if report.get('original_prompt_sha256')!=s['original_prompt_sha256']:raise ValueError('Original prompt SHA256 mismatch')
        if verdict=='VERIFIED':
            if not all(isinstance(report.get(k),list) for k in ('scope_limits','assumptions','risk_findings','required_corrections','evidence_refs')):
                raise ValueError('Counterbalance findings missing')
            if report['required_corrections']:raise ValueError('Cannot verify unresolved required corrections')
            if not isinstance(report.get('handoff_text'),str) or not report['handoff_text'].strip():raise ValueError('Director handoff missing')
    elif stage=='DIRECTORS_SPEC_PACKET' and verdict=='COMPLETE':
        for k in ('counterbalance_gate_sha256','executor_contract','checker_contract','creative_direction','project'):
            if not report.get(k):raise ValueError('Incomplete Directors SPEC_PACKET: '+k)
        if report['counterbalance_gate_sha256']!=s['reports'][0]['sha256']:
            raise ValueError('SPEC_PACKET references wrong prompt gate')
        if report['project']!=s['project']:raise ValueError('Project scope drift')
        if s['baseline_ref']=='UNKNOWN':raise ValueError('Unknown source baseline cannot authorise build')
    elif stage=='CHECKER_G0' and verdict=='PASS':
        if report.get('reviewed_spec_sha256')!=s['spec_packet_sha256'] or not verified:
            raise ValueError('G0 PASS requires exact SPEC_PACKET and hashed review evidence')
    elif stage=='EXECUTOR_BUILD' and verdict=='READY_FOR_CHECK':
        if report.get('spec_packet_sha256')!=s['spec_packet_sha256'] or not verified:
            raise ValueError('Executor requires approved G0 spec and verifiable artifact')
    elif stage=='CHECKER_G1' and verdict=='PASS':
        if (report.get('reviewed_spec_sha256')!=s['spec_packet_sha256'] or
            report.get('reviewed_executor_sha256')!=s['executor_report_sha256'] or not verified):
            raise ValueError('G1 PASS requires exact version/spec and hashed check evidence')
    report_id=digest(canon(report))
    entry={'stage':stage,'role':role,'verdict':verdict,'sha256':report_id,
           'artifact_refs':verified,'reviewer_mode':report['reviewer_mode'],
           'agent_invocation_independently_verified':False,'summary':report['summary']}
    s['reports'].append(entry)
    event(mission,'ROLE_REPORT',{'stage':stage,'verdict':verdict,'hash':report_id})
    if verdict != SUCCESS[stage]:
        s['status']='HELD';s['hold_reason']=f'{stage} returned {verdict}. No bypass; revise mission with owner if needed.'
    else:
        if stage=='DIRECTORS_SPEC_PACKET':s['spec_packet_sha256']=report_id
        if stage=='EXECUTOR_BUILD':s['executor_report_sha256']=report_id
        if stage=='DIRECTORS_ALIGNMENT':s['status']='AWAITING_OWNER'
        else:s['stage_index']+=1
    save(mission,s)
    return s

def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='cmd',required=True)
    t=s.add_parser('start');t.add_argument('--root',default='./council-runs');t.add_argument('--instruction',required=True);t.add_argument('--project',required=True);t.add_argument('--baseline-ref',required=True)
    t=s.add_parser('submit');t.add_argument('--mission',required=True);t.add_argument('--report',required=True)
    for name in ('status','verify-events'):
        t=s.add_parser(name);t.add_argument('--mission',required=True)
    a=p.parse_args()
    if a.cmd=='start':
        m=start(a.root,a.instruction,a.project,a.baseline_ref);print(m);print(handoff(load(m)))
    elif a.cmd=='submit':
        st=submit(a.mission,read(a.report));print(json.dumps({'status':st['status'],'current_stage':st['stage_index']},indent=2));print(handoff(st))
    elif a.cmd=='status':print(handoff(load(a.mission)))
    else:print('VALID' if verify_events(a.mission) else 'INVALID')
if __name__=='__main__':main()
