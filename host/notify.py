"""Five-minute, change-only outbound notification relay for real Council Outbox messages.

This is not ChatGPT chat injection and not an LLM worker. No sensitive message body
is sent to ntfy. The private, unguessable topic is supplied only as an env variable.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sqlite3
import time
from contextlib import closing, contextmanager
from urllib.request import Request, urlopen
from council.runtime import outbox_bus

DB_PATH = "/state/brain/notifications.sqlite3"
OUTBOX_PATH = "/state/council/outbox/outboxes.sqlite3"
LIMIT = 50


def connect_ledger(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS progress (id INTEGER PRIMARY KEY CHECK(id=1), last_event_seq INTEGER NOT NULL)")
    db.commit()
    return db


def read_source(path):
    path = Path(path)
    if not path.exists():
        return None
    # Never create, update or acknowledge Council messages.
    con = sqlite3.connect("file:" + str(path.resolve()) + "?mode=ro", uri=True, timeout=5)
    con.row_factory = sqlite3.Row
    return con


@contextmanager
def source_ctx(path):
    source = read_source(path)
    try:
        yield source
    finally:
        if source is not None:
            source.close()


def deliver_ntfy(topic, count, opener=urlopen):
    if not topic or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in topic):
        raise ValueError("Missing or invalid private notification topic")
    payload = {
        "topic": topic,
        "title": "Brain — Council message",
        "message": f"{count} new Council message{'s' if count != 1 else ''} for your attention. Open Brain to review.",
        "priority": 3,
        "tags": ["robot"],
        "click": "https://chatgpt.com/plugins/plugins_6ad65b442bd48191a3e6414844cdb11c",
    }
    req = Request("https://ntfy.sh/", data=json.dumps(payload).encode("utf-8"),
                  headers={"Content-Type":"application/json"},method="POST")
    with opener(req, timeout=8) as res:
        if not (200 <= res.status < 300):
            raise RuntimeError("Push provider rejected notification")
    return True


def scan(outbox, ledger, topic, push=deliver_ntfy):
    with closing(connect_ledger(ledger)) as state:
        row = state.execute("SELECT last_event_seq FROM progress WHERE id=1").fetchone()
        if row is None:
            # First launch after installation: preserve existing history without a burst
            # of stale notifications; when no source exists, new events start at seq 1.
            with source_ctx(outbox) as source:
                highwater = int(source.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]) if source else 0
            state.execute("INSERT INTO progress(id,last_event_seq) VALUES(1,?)", (highwater,))
            state.commit()
            return {"status":"BASELINED", "last_seq":highwater}
        cursor = int(row[0])
        with source_ctx(outbox) as source:
            if source is None:
                return {"status":"SOURCE_NOT_CONNECTED"}
            if not outbox_bus.verify(source):
                return {"status":"INTEGRITY_BLOCKED"}
            maximum = int(source.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0])
            # Filter only owner-directed messages or actionable supervisor defects.
            # A worker heartbeat or repeated status with no new message cannot trigger a push.
            rows = source.execute("""
                SELECT e.seq, m.id
                FROM events e JOIN messages m ON m.id=e.message_id
                WHERE e.seq > ? AND e.event='MESSAGE_SENT'
                  AND (
                    EXISTS (SELECT 1 FROM mailboxes b
                            WHERE b.message_id=m.id AND b.folder='inbox' AND b.role='Owner')
                    OR (m.sender='System' AND m.kind='defect'
                        AND EXISTS (SELECT 1 FROM mailboxes b
                                    WHERE b.message_id=m.id AND b.folder='inbox'
                                      AND b.role IN ('Director','Checker')))
                  )
                ORDER BY e.seq LIMIT ?
            """,(cursor,LIMIT)).fetchall()
        if not rows:
            if maximum != cursor:
                state.execute("UPDATE progress SET last_event_seq=? WHERE id=1",(maximum,))
                state.commit()
            return {"status":"NO_CHANGE"}
        # At-least-once delivery. Cursor advances only after provider confirms success.
        push(topic, len(rows))
        after = int(rows[-1]["seq"]) if len(rows)==LIMIT else maximum
        state.execute("UPDATE progress SET last_event_seq=? WHERE id=1",(after,))
        state.commit()
        return {"status":"MESSAGE_PUSHED", "count":len(rows),"last_seq":after}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--outbox", default=os.getenv("BRAIN_OUTBOX_DB",OUTBOX_PATH))
    parser.add_argument("--ledger", default=os.getenv("BRAIN_NOTIFY_LEDGER",DB_PATH))
    parser.add_argument("--interval",type=int,default=int(os.getenv("BRAIN_NOTIFY_INTERVAL","300")))
    parser.add_argument("--once", action="store_true")
    args=parser.parse_args(argv)
    if args.interval < 60 or args.interval>3600:raise SystemExit("Invalid notification interval")
    topic=os.getenv("BRAIN_NTFY_TOPIC","")
    if not topic: raise SystemExit("BRAIN_NTFY_TOPIC is required")
    print(json.dumps({"event":"BRAIN_MESSAGE_RELAY_STARTED","interval_seconds":args.interval,
                      "destination":"ntfy (generic-only)","chat_injection":False}),flush=True)
    while True:
        try:
            outcome=scan(args.outbox,args.ledger,topic)
            if outcome["status"] not in ("NO_CHANGE","SOURCE_NOT_CONNECTED"):
                print(json.dumps(outcome,sort_keys=True),flush=True)
        except (OSError, sqlite3.Error, ValueError, RuntimeError) as exc:
            print(json.dumps({"event":"BRAIN_MESSAGE_RELAY_ERROR","error_type":type(exc).__name__}),flush=True)
        if args.once: return 0
        time.sleep(args.interval)


if __name__=="__main__": raise SystemExit(main())
