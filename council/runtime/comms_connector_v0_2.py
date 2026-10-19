#!/usr/bin/env python3
"""Project OS Council local role communications bridge v0.2.2.

Actual authenticated SQLite inbox/outbox delivery and acknowledgement. No remote
plugin RPC, no LLM worker, no public host and no implicit owner approval.
Designed to use the existing Council outbox_bus.py + dispatcher_v0_2.py APIs.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

try:
    from . import dispatcher_v0_2 as dispatch
    from . import outbox_bus as bus
except ImportError:
    import dispatcher_v0_2 as dispatch
    import outbox_bus as bus

ROLES = ('Owner','Counterweight','Director','Creative Director','Researcher', 'Northstar','Checker','Executor')
STAGE_TARGETS = {
    'COUNTERBALANCE_PROMPT_GATE':['Director','Creative Director'],
    'DIRECTORS_SPEC_PACKET':['Researcher','Northstar','Checker'],
    'RESEARCHER_NORTHSTAR_EVIDENCE':['Checker'],
    'CHECKER_G0':['Executor','Director'],
    'EXECUTOR_BUILD':['Checker'],
    'CHECKER_G1':['Director','Creative Director'],
    'DIRECTORS_ALIGNMENT':['Owner'],
}

class CommunicationError(ValueError): pass

def sha(raw): return hashlib.sha256(raw).hexdigest()
def canon(obj): return json.dumps(obj,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()

def setup(root):
    root=Path(root).resolve(); root.mkdir(parents=True,exist_ok=True); os.chmod(root,0o700)
    secrets=root/'actor_tokens'; secrets.mkdir(exist_ok=True); os.chmod(secrets,0o700)
    conn=bus.open_bus(root/'outbox')
    try:
        created=[]
        for role in ROLES:
            file=secrets/(bus.slug(role)+'.token')
            registered=conn.execute('SELECT 1 FROM actors WHERE role=?',(role,)).fetchone() is not None
            if registered:
                if not file.is_file() or file.is_symlink(): raise CommunicationError('Missing original credential: '+role)
                if (file.stat().st_mode & 0o077):raise CommunicationError('Unsafe credential permissions: '+role)
                bus.authenticate(conn,role,file.read_text('utf-8').strip())
            else:
                if file.exists() or file.is_symlink(): raise CommunicationError('Unexpected existing token: '+role)
                token=bus.enroll(conn,role)
                with file.open('x',encoding='utf-8') as f: f.write(token+'\n')
                os.chmod(file,0o600); created.append(role)
        if not bus.verify(conn):raise CommunicationError('Event integrity failed')
        return {'transport':'LOCAL_AUTH_READY','actors':list(ROLES),'created':created,'connected_ai_plugins':False,'owner_release_authorised':False}
    finally:conn.close()

def token_for(root,role):
    if role not in ROLES:raise CommunicationError('Unknown role')
    p=Path(root)/'actor_tokens'/(bus.slug(role)+'.token')
    if not p.is_file() or p.is_symlink() or (p.stat().st_mode&0o077):raise CommunicationError('Role credential unavailable')
    return p.read_text('utf-8').strip()

def mission_path(root,mission_id):
    if not mission_id.startswith('MISSION-') or '/' in mission_id or '\\' in mission_id:raise CommunicationError('Unsafe mission id')
    p=(Path(root)/'missions'/mission_id).resolve()
    if p.parent != (Path(root)/'missions').resolve():raise CommunicationError('Mission outside authorised root')
    return p

def new_mission(root,request,project,baseline):
    root=Path(root).resolve();setup(root)
    mission=dispatch.start(root/'missions',request,project,baseline)
    state=dispatch.load(mission)
    conn=bus.open_bus(root/'outbox')
    try:
        receipt=bus.post(conn,mission_id=state['mission_id'],sender='Owner',token=token_for(root,'Owner'),
            recipients=['Counterweight'],kind='handoff',subject='ORIGINAL OWNER PROMPT — GATE FIRST',
            body=json.dumps({'original_prompt':request,'original_prompt_sha256':state['original_prompt_sha256'],
                'stage':'COUNTERBALANCE_PROMPT_GATE','no_director_preinterpretation':True},ensure_ascii=False),
            refs=[str(mission/'mission.json')],author_mode='local_authenticated_actor')
    finally:conn.close()
    return {'mission_id':state['mission_id'],'current_stage':'COUNTERBALANCE_PROMPT_GATE',
            'initial_message_id':receipt['message_id'],'recipient':'Counterweight','plugin_rpc':'NOT_CONNECTED'}

def ack(root,role,mission_id,message_id):
    root=Path(root).resolve();conn=bus.open_bus(root/'outbox')
    try:
        candidate=[m for m in bus.mailbox(conn,role,mission=mission_id) if m['id']==message_id]
        if not candidate:raise CommunicationError('Message not in target mission inbox')
        x=bus.receive(conn,role,token_for(root,role),message_id,ack=True)
        return {'message_id':message_id,'role':role,'acknowledged':True}
    finally:conn.close()

def propose_spec(root,mission_id,report):
    root=Path(root).resolve();state=dispatch.load(mission_path(root,mission_id))
    if state['status']!='ACTIVE' or dispatch.STAGES[state['stage_index']][0]!='DIRECTORS_SPEC_PACKET':
        raise CommunicationError('Director proposal not at SPEC_PACKET stage')
    if report.get('role')!='Director' or report.get('stage')!='DIRECTORS_SPEC_PACKET':
        raise CommunicationError('Wrong specification packet')
    h=sha(canon(report))
    conn=bus.open_bus(root/'outbox')
    try:
        receipt=bus.post(conn,mission_id=mission_id,sender='Director',token=token_for(root,'Director'),
            recipients=['Creative Director'],kind='question',subject='SPEC_PACKET CO-SIGN REQUEST',
            body=json.dumps({'spec_report_sha256':h,'request':'Verify creative scope and jointly endorse exact packet'},sort_keys=True),
            refs=[state['original_prompt_sha256']],author_mode='local_authenticated_actor')
        return {'spec_report_sha256':h,'message_id':receipt['message_id'],'awaiting':'Creative Director'}
    finally:conn.close()

def creative_cosign(root,mission_id,request_message_id,report):
    root=Path(root).resolve();h=sha(canon(report))
    conn=bus.open_bus(root/'outbox')
    try:
        target=[m for m in bus.mailbox(conn,'Creative Director',mission=mission_id) if m['id']==request_message_id]
        if not target:raise CommunicationError('SPEC_PACKET proposal missing from Creative Director inbox')
        request=target[0]
        if request['mission_id']!=mission_id or request['subject']!='SPEC_PACKET CO-SIGN REQUEST':
            raise CommunicationError('Invalid co-sign request')
        if json.loads(request['body']).get('spec_report_sha256')!=h:
            raise CommunicationError('Co-signed proposal differs from exact Director packet')
        bus.receive(conn,'Creative Director',token_for(root,'Creative Director'),request_message_id,ack=True)
        r=bus.post(conn,mission_id=mission_id,sender='Creative Director',recipients=['Director'],
            subject='SPEC_PACKET CO-SIGNED',kind='response',body=json.dumps({'spec_report_sha256':h,'review':'ACKNOWLEDGED','owner_release':False}),
            token=token_for(root,'Creative Director'),reply_to=request_message_id,author_mode='local_authenticated_actor')
        return {'spec_report_sha256':h,'message_id':r['message_id'],'creative_director_cosign':True}
    finally:conn.close()

def submit_report(root,mission_id,role,report):
    root=Path(root).resolve();mission=mission_path(root,mission_id)
    state=dispatch.load(mission)
    if state['status']!='ACTIVE':raise CommunicationError('Mission held')
    stage,expected=dispatch.STAGES[state['stage_index']]
    if role!=expected:raise CommunicationError(f'Incorrect actor for {stage}: expected {expected}')
    conn=bus.open_bus(root/'outbox')
    try:
        bus.authenticate(conn,role,token_for(root,role))
        if stage=='COUNTERBALANCE_PROMPT_GATE':
            inbox=[m for m in bus.mailbox(conn,'Counterweight',mission=mission_id)
                if m['subject']=='ORIGINAL OWNER PROMPT — GATE FIRST' and m['ack_at']]
            if not inbox:raise CommunicationError('Counterbalance must acknowledge unchanged original owner request first')
        if stage=='DIRECTORS_SPEC_PACKET':
            h=sha(canon(report))
            cosign=[m for m in bus.feed(conn,mission_id) if m['sender']=='Creative Director'
                    and m['subject']=='SPEC_PACKET CO-SIGNED' and json.loads(m['body']).get('spec_report_sha256')==h]
            if not cosign:raise CommunicationError('Creative Director has not co-signed this exact specification')
        after=dispatch.submit(mission,report)
        recipient=STAGE_TARGETS.get(stage,['Director'])
        if after['status']=='HELD':recipient=['Director','Owner']
        receipt=bus.post(conn,mission_id=mission_id,sender=role,token=token_for(root,role),
                recipients=[r for r in recipient if r!=role],kind='handoff' if after['status']=='ACTIVE' else 'status',
                subject=f'{stage}: {report["verdict"]}',
                body=json.dumps({'stage':stage,'verdict':report['verdict'],
                    'next_stage':dispatch.STAGES[after['stage_index']][0] if after['status']=='ACTIVE' else after['status'],
                    'report_sha256':sha(canon(report)),'owner_release_authorised':False},sort_keys=True),
                refs=[str(mission/'mission.json')],author_mode='local_authenticated_actor')
        return {'mission_id':mission_id,'stage':stage,'status':after['status'],
                'receipt':receipt['message_id'],'delivered_to':receipt['delivered_to'],'owner_release_authorised':False}
    finally:conn.close()

def status(root,mission_id):
    root=Path(root).resolve();s=dispatch.load(mission_path(root,mission_id));conn=bus.open_bus(root/'outbox')
    try:
        return {'mission_id':mission_id,'status':s['status'],
          'stage':dispatch.STAGES[s['stage_index']][0] if s['status']=='ACTIVE' else s['status'],
          'messages':len(bus.feed(conn,mission_id)),'transport_valid':bus.verify(conn),
          'events_valid':dispatch.verify_events(mission_path(root,mission_id)),
          'remote_plugin_calls':'NOT_CONNECTED','owner_release_authorised':False}
    finally:conn.close()

def cli():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',required=True)
    c=p.add_subparsers(dest='cmd',required=True)
    c.add_parser('prepare')
    a=c.add_parser('start');a.add_argument('--prompt',required=True);a.add_argument('--project',required=True);a.add_argument('--baseline',required=True)
    a=c.add_parser('status');a.add_argument('--mission',required=True)
    a=c.add_parser('ack');a.add_argument('--mission',required=True);a.add_argument('--role',required=True);a.add_argument('--message-id',required=True)
    a=c.add_parser('propose-spec');a.add_argument('--mission',required=True);a.add_argument('--report',required=True)
    a=c.add_parser('cosign-spec');a.add_argument('--mission',required=True);a.add_argument('--message-id',required=True);a.add_argument('--report',required=True)
    a=c.add_parser('submit');a.add_argument('--mission',required=True);a.add_argument('--role',required=True);a.add_argument('--report',required=True)
    args=p.parse_args();root=Path(args.root)
    try:
        if args.cmd=='prepare':out=setup(root)
        elif args.cmd=='start':out=new_mission(root,args.prompt,args.project,args.baseline)
        elif args.cmd=='status':out=status(root,args.mission)
        elif args.cmd=='ack':out=ack(root,args.role,args.mission,args.message_id)
        elif args.cmd=='propose-spec':out=propose_spec(root,args.mission,json.loads(Path(args.report).read_text('utf-8')))
        elif args.cmd=='cosign-spec':out=creative_cosign(root,args.mission,args.message_id,json.loads(Path(args.report).read_text('utf-8')))
        else:out=submit_report(root,args.mission,args.role,json.loads(Path(args.report).read_text('utf-8')))
        print(json.dumps(out,indent=2))
    except (ValueError,PermissionError,OSError) as e:p.exit(2,'BLOCKED: '+str(e)+'\n')

if __name__=='__main__':cli()
