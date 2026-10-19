#!/usr/bin/env python3
"""Project OS Outbox Bus v0.1.3 — local durable role messaging.

This is a transport, not an autonomous AI-agent runner. Agents with verified
separate runtimes can use mailbox credentials and POST via an approved adapter.
The CLI and SQLite storage work without network access or external services.
"""
import argparse
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

VERSION = "0.1.3"
ROLES = ("Owner", "Director", "Creative Director", "Researcher", "Checker", "Executor", "Northstar", "Counterweight", "Council")
SYSTEM = "System"
KINDS = ("question", "finding", "concept", "challenge", "handoff", "review", "defect", "response", "status", "notice")
MAX_BODY = 12000


def utc():
    return datetime.now(timezone.utc).isoformat()


def slug(role):
    return role.lower().replace(" ", "-")


def open_bus(root):
    root=Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    try: os.chmod(root,0o700)
    except OSError: pass
    conn=sqlite3.connect(root/"outboxes.sqlite3",timeout=10,isolation_level=None)
    conn.row_factory=sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript('''
CREATE TABLE IF NOT EXISTS actors(role TEXT PRIMARY KEY, token_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS messages(
 id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, mission_id TEXT NOT NULL,
 sent_at TEXT NOT NULL, sender TEXT NOT NULL, recipients_json TEXT NOT NULL,
 kind TEXT NOT NULL, subject TEXT NOT NULL, body TEXT NOT NULL,
 reply_to TEXT, refs_json TEXT NOT NULL,
 author_mode TEXT NOT NULL, tool_run_id TEXT,
 content_sha256 TEXT NOT NULL,
 approval_granted INTEGER NOT NULL DEFAULT 0 CHECK(approval_granted=0),
 release_authorised INTEGER NOT NULL DEFAULT 0 CHECK(release_authorised=0),
 FOREIGN KEY(reply_to) REFERENCES messages(id));
CREATE TABLE IF NOT EXISTS mailboxes(
 message_id TEXT NOT NULL, role TEXT NOT NULL,
 folder TEXT NOT NULL CHECK(folder IN ('inbox','outbox')),
 seen_at TEXT, ack_at TEXT,
 PRIMARY KEY(message_id,role,folder),
 FOREIGN KEY(message_id) REFERENCES messages(id));
CREATE INDEX IF NOT EXISTS idx_mailbox_role ON mailboxes(role,folder,message_id);
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,
 at TEXT NOT NULL, event TEXT NOT NULL, message_id TEXT NOT NULL,
 role TEXT NOT NULL, prev_sha256 TEXT NOT NULL, event_sha256 TEXT NOT NULL);
''')
    return conn


def enroll(conn,role):
    if role not in ROLES: raise ValueError("Unknown role")
    if conn.execute("SELECT 1 FROM actors WHERE role=?",(role,)).fetchone():
        raise ValueError("Already enrolled: cannot silently rotate a credential")
    token=secrets.token_urlsafe(32)
    conn.execute("INSERT INTO actors(role,token_sha256) VALUES (?,?)",(role,hashlib.sha256(token.encode()).hexdigest()))
    return token


def authenticate(conn,role,token):
    if not isinstance(token,str): raise PermissionError("Actor token missing")
    row=conn.execute("SELECT token_sha256 FROM actors WHERE role=?",(role,)).fetchone()
    if not row or not hmac.compare_digest(row[0],hashlib.sha256(token.encode()).hexdigest()):
        raise PermissionError("Actor token invalid")


def _json(v): return json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(",",":"))


def _event(conn,event,msg_id,role):
    prev=conn.execute("SELECT event_sha256 FROM events ORDER BY seq DESC LIMIT 1").fetchone()
    prev=prev[0] if prev else "GENESIS"
    at=utc(); raw=_json({"at":at,"event":event,"message_id":msg_id,"role":role,"prev_sha256":prev})
    digest=hashlib.sha256(raw.encode()).hexdigest()
    conn.execute("INSERT INTO events(at,event,message_id,role,prev_sha256,event_sha256) VALUES(?,?,?,?,?,?)",(at,event,msg_id,role,prev,digest))


def post(conn,*,mission_id,sender,recipients,subject,body,kind="status",thread_id=None,reply_to=None,
         refs=None,author_mode="local_authenticated_actor",token=None,tool_run_id=None,
         owner_approval_granted=False,release_authorised=False):
    if sender!=SYSTEM: authenticate(conn,sender,token)
    else:
        if author_mode!="system_event": raise PermissionError("System sender only for system events")
    if not isinstance(mission_id,str) or not mission_id.strip():raise ValueError("Mission ID required")
    if not isinstance(recipients,list) or not recipients or len(set(recipients))!=len(recipients):
        raise ValueError("Nonempty unique recipient list required")
    if any(r not in ROLES for r in recipients): raise ValueError("Unrecognised recipient")
    if sender in recipients: raise ValueError("Cannot send to own inbox")
    if kind not in KINDS:raise ValueError("Unknown message kind")
    if not isinstance(subject,str) or not subject.strip() or len(subject)>200:raise ValueError("Invalid subject")
    if not isinstance(body,str) or not body.strip() or len(body)>MAX_BODY:raise ValueError("Invalid body")
    if owner_approval_granted or release_authorised: raise ValueError("Only owner approval service may record owner approval; mail cannot")
    if author_mode not in ("local_authenticated_actor","system_event","same_assistant_workflow","connected_agent_evidenced"):
        raise ValueError("Invalid author mode")
    if author_mode=="connected_agent_evidenced":
        raise ValueError("No connected-agent identity attestor configured: cannot claim this mode")
    if sender==SYSTEM and author_mode!="system_event":raise ValueError("System message provenance mismatch")
    if sender!=SYSTEM and author_mode=="system_event":raise ValueError("Actor message provenance mismatch")
    refs=[] if refs is None else refs
    if not isinstance(refs,list) or any(not isinstance(x,str) for x in refs):raise ValueError("refs must be string array")
    msg_id="MSG-"+uuid4().hex[:16].upper()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if reply_to:
            prev=conn.execute("SELECT thread_id,mission_id,sender,recipients_json FROM messages WHERE id=?",(reply_to,)).fetchone()
            if not prev or prev["mission_id"]!=mission_id:raise ValueError("Invalid reply target/mission")
            participants=set(json.loads(prev["recipients_json"]))|{prev["sender"]}
            if sender not in participants or not set(recipients)<=participants|{SYSTEM}:
                raise PermissionError("Reply sender/recipients not part of thread")
            if thread_id and thread_id!=prev["thread_id"]:raise ValueError("Reply thread mismatch")
            thread_id=prev["thread_id"]
        thread_id=thread_id or "THREAD-"+uuid4().hex[:12].upper()
        sent=utc()
        raw={'id':msg_id,'thread_id':thread_id,'mission_id':mission_id,'sent_at':sent,
             'sender':sender,'recipients_json':_json(recipients),'kind':kind,
             'subject':subject,'body':body,'reply_to':reply_to,'refs_json':_json(refs),
             'author_mode':author_mode,'tool_run_id':tool_run_id}
        digest=hashlib.sha256(_json(raw).encode()).hexdigest()
        conn.execute("INSERT INTO messages(id,thread_id,mission_id,sent_at,sender,recipients_json,kind,subject,body,reply_to,refs_json,author_mode,tool_run_id,content_sha256) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
             (msg_id,thread_id,mission_id,sent,sender,_json(recipients),kind,subject,body,reply_to,_json(refs),author_mode,tool_run_id,digest))
        if sender!=SYSTEM:conn.execute("INSERT INTO mailboxes(message_id,role,folder) VALUES(?,?,'outbox')",(msg_id,sender))
        for rec in recipients:conn.execute("INSERT INTO mailboxes(message_id,role,folder) VALUES(?,?,'inbox')",(msg_id,rec))
        _event(conn,"MESSAGE_SENT",msg_id,sender)
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK");raise
    return {"message_id":msg_id,"thread_id":thread_id,"delivered_to":recipients,"owner_release_authorised":False}


def mailbox(conn,role,folder="inbox",unread=False,mission=None):
    if role not in ROLES or folder not in ("inbox","outbox"):raise ValueError("Unknown mailbox")
    query='''SELECT m.*,b.seen_at,b.ack_at FROM mailboxes b JOIN messages m ON b.message_id=m.id WHERE b.role=? AND b.folder=?'''
    args=[role,folder]
    if unread:query+=" AND b.seen_at IS NULL"
    if mission:query+=" AND m.mission_id=?";args.append(mission)
    query+=" ORDER BY m.sent_at,m.id"
    return [normalise(row) for row in conn.execute(query,args)]


def normalise(row):
    d=dict(row)
    d['recipients']=json.loads(d.pop('recipients_json'))
    d['refs']=json.loads(d.pop('refs_json'))
    d['owner_approval_granted']=False
    d.pop('approval_granted',None);d.pop('release_authorised',None)
    d['release_authorised']=False
    return d


def receive(conn,role,token,msg_id,ack=False):
    authenticate(conn,role,token)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row=conn.execute('''SELECT m.* FROM messages m JOIN mailboxes b ON m.id=b.message_id WHERE b.role=? AND b.folder='inbox' AND m.id=?''',(role,msg_id)).fetchone()
        if not row:raise PermissionError("Message not in role inbox")
        conn.execute("UPDATE mailboxes SET seen_at=COALESCE(seen_at,?),ack_at=CASE WHEN ? THEN COALESCE(ack_at,?) ELSE ack_at END WHERE role=? AND folder='inbox' AND message_id=?",(utc(),int(ack),utc(),role,msg_id))
        _event(conn,"MESSAGE_ACK" if ack else "MESSAGE_READ",msg_id,role)
        conn.execute("COMMIT")
    except Exception:conn.execute("ROLLBACK");raise
    return normalise(row)


def feed(conn,mission=None):
    q="SELECT * FROM messages";args=[]
    if mission:q+=" WHERE mission_id=?";args.append(mission)
    q+=" ORDER BY sent_at,id"
    return [normalise(r) for r in conn.execute(q,args)]


def verify(conn):
    prev="GENESIS"
    for row in conn.execute("SELECT * FROM events ORDER BY seq"):
        expected=hashlib.sha256(_json({"at":row['at'],"event":row['event'],"message_id":row['message_id'],"role":row['role'],"prev_sha256":prev}).encode()).hexdigest()
        if row["prev_sha256"]!=prev or expected!=row['event_sha256']:return False
        prev=row['event_sha256']
    n=conn.execute("SELECT count(*) FROM messages WHERE approval_granted!=0 OR release_authorised!=0").fetchone()[0]
    if n:return False
    for msg in conn.execute('SELECT * FROM messages'):
        m=dict(msg)
        claimed=m.pop('content_sha256')
        m.pop('approval_granted',None);m.pop('release_authorised',None)
        if hashlib.sha256(_json(m).encode()).hexdigest()!=claimed:return False
    return True


def format_feed(messages):
    return "\n".join(f"{m['sent_at']} | {m['sender']} → {', '.join(m['recipients'])} [{m['kind']}] {m['subject']}: {m['body']}" for m in messages)


def main():
    ap=argparse.ArgumentParser(description="Project OS role inbox/outbox transport, NOT standalone AI agent execution")
    ap.add_argument("--root",required=True,help="Private mission directory for durable SQLite database")
    sub=ap.add_subparsers(dest="cmd",required=True)
    p=sub.add_parser("enroll");p.add_argument("--role",choices=ROLES,required=True)
    p=sub.add_parser("send");p.add_argument("--from-role",dest="role",required=True,choices=ROLES);p.add_argument("--to",required=True,help="Comma-separated roles")
    p.add_argument("--mission",required=True);p.add_argument("--subject",required=True);p.add_argument("--body",required=True)
    p.add_argument("--kind",default="status",choices=KINDS);p.add_argument("--reply-to");p.add_argument("--ref",action="append",default=[])
    p=sub.add_parser("mailbox");p.add_argument("--role",choices=ROLES,required=True);p.add_argument("--folder",choices=['inbox','outbox'],default='inbox');p.add_argument("--unread",action='store_true');p.add_argument("--mission")
    p=sub.add_parser("receive");p.add_argument("--role",choices=ROLES,required=True);p.add_argument("--message-id",required=True);p.add_argument("--ack",action='store_true')
    p=sub.add_parser("feed");p.add_argument("--mission");p.add_argument("--follow",action='store_true');p.add_argument("--json",action='store_true')
    sub.add_parser("verify")
    args=ap.parse_args()
    con=open_bus(args.root)
    try:
        if args.cmd=="enroll":
            token=enroll(con,args.role)
            print(json.dumps({"role":args.role,"token_once":token,"note":"Keep private. Pass as POS_OUTBOX_TOKEN environment variable."}))
        elif args.cmd=="send":
            print(json.dumps(post(con,mission_id=args.mission,sender=args.role,recipients=[x.strip() for x in args.to.split(',')],subject=args.subject,body=args.body,kind=args.kind,reply_to=args.reply_to,refs=args.ref,token=os.getenv('POS_OUTBOX_TOKEN')),indent=2))
        elif args.cmd=="mailbox":print(json.dumps(mailbox(con,args.role,args.folder,args.unread,args.mission),indent=2))
        elif args.cmd=="receive":print(json.dumps(receive(con,args.role,os.getenv('POS_OUTBOX_TOKEN'),args.message_id,args.ack),indent=2))
        elif args.cmd=="verify":print(json.dumps({"event_chain_valid":verify(con),"messages":con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]}))
        elif args.cmd=="feed":
            seen=set()
            while True:
                for m in feed(con,args.mission):
                    if m['id'] in seen:continue
                    seen.add(m['id']);print(json.dumps(m) if args.json else format_feed([m]),flush=True)
                if not args.follow:break
                time.sleep(1.0)
    except (ValueError,PermissionError,sqlite3.Error) as e:
        print(json.dumps({"error":str(e)}),file=sys.stderr);sys.exit(1)
    finally:con.close()
if __name__=='__main__':main()
