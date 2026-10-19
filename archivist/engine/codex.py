"""Local Project OS Archivist codex. No automatic chat, network or external source access.

Run as `python -m engine.codex --help` from the plugin root.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import uuid

EVENT_TYPES = {"owner_request", "decision", "requirement", "project_change", "artifact", "test_result", "approval", "blocker", "task", "status", "timeline_signal"}
ASSERTIONS = {"quoted", "self_reported", "observed", "inferred"}
VERIFICATIONS = {"unverified", "source_attested", "independently_verified", "contradicted"}
PRECISIONS = {"exact", "minute", "date", "sequence", "unknown"}
VISIBILITIES = {"council", "owner_only"}
SENSITIVITIES = {"ordinary", "personal_analytics", "sensitive"}
ROLES = {"Owner", "Counterweight", "Counterbalance", "Director", "Creative Director", "Researcher", "Overseer", "Northstar", "Checker", "Executor"}
ALLOWED_BY_ROLE = {
    "Counterweight": EVENT_TYPES - {"timeline_signal"},
    "Director": EVENT_TYPES - {"timeline_signal"},
    "Creative Director": EVENT_TYPES - {"timeline_signal"},
    "Researcher": EVENT_TYPES - {"timeline_signal"},
    "Northstar": EVENT_TYPES - {"timeline_signal"},
    "Checker": EVENT_TYPES - {"timeline_signal"},
    "Executor": {"owner_request", "requirement", "project_change", "artifact", "test_result", "task", "blocker", "status", "decision", "approval"},
}
ALLOWED_BY_ROLE["Counterbalance"] = ALLOWED_BY_ROLE["Counterweight"]
ALLOWED_BY_ROLE["Overseer"] = ALLOWED_BY_ROLE["Researcher"]


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical(d):
    return json.dumps(d, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def iso_utc(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("occurred_at must be ISO 8601 string or null")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("occurred_at must include timezone offset")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def validate(event):
    required = ["source_key", "project", "event_type", "actor", "source_kind", "source_ref", "summary", "assertion", "verified_status", "time_precision", "visibility", "sensitivity", "consent_ref", "evidence"]
    missing = [x for x in required if x not in event]
    if missing:
        raise ValueError("missing fields: " + ", ".join(missing))
    for k in ("source_key", "project", "actor", "source_kind", "source_ref", "summary", "consent_ref"):
        if not isinstance(event[k], str) or not event[k].strip():
            raise ValueError(f"{k} must be a nonblank string")
    for k, choices in (("event_type", EVENT_TYPES), ("assertion", ASSERTIONS), ("verified_status", VERIFICATIONS), ("time_precision", PRECISIONS), ("visibility", VISIBILITIES), ("sensitivity", SENSITIVITIES)):
        if event[k] not in choices:
            raise ValueError(f"invalid {k}: {event[k]}")
    if event["sensitivity"] == "sensitive":
        raise ValueError("sensitive-category indexing blocked in v0.1.0; do not store unredacted private records")
    if event["sensitivity"] == "personal_analytics" and (event["visibility"] != "owner_only" or not event["consent_ref"].startswith("owner_opt_in:")):
        raise ValueError("personal analytics require owner_only and an explicit owner_opt_in consent reference")
    if event["event_type"] == "timeline_signal" and event["sensitivity"] != "personal_analytics":
        raise ValueError("timeline_signal must use owner_only personal_analytics")
    if event["assertion"] == "inferred":
        if not isinstance(event.get("confidence"), (int, float)) or not 0 <= event["confidence"] <= 1:
            raise ValueError("inferences require numeric confidence 0..1")
        if not event.get("alternative_explanations"):
            raise ValueError("inferences require alternative_explanations")
    if event.get("occurred_at") is None and event["time_precision"] not in ("unknown", "sequence"):
        raise ValueError("null occurred_at requires unknown/sequence time_precision")
    if event.get("occurred_at") is not None:
        iso_utc(event["occurred_at"])
    if not isinstance(event["evidence"], list) or not event["evidence"]:
        raise ValueError("evidence must be a non-empty list")
    for e in event["evidence"]:
        if not isinstance(e, dict) or not isinstance(e.get("source_ref"), str) or not e["source_ref"]:
            raise ValueError("every evidence item needs a source_ref")
    if event["event_type"] == "approval" and event.get("verified_status") != "independently_verified":
        # Text in chat is *not* a host-authenticated exact-version release authorization.
        event = dict(event, approval_is_authority=False)
    return event


def connect(path):
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL UNIQUE, source_key TEXT NOT NULL UNIQUE, project TEXT NOT NULL, event_type TEXT NOT NULL, occurred_at TEXT, time_precision TEXT NOT NULL, recorded_at TEXT NOT NULL, visibility TEXT NOT NULL, sensitivity TEXT NOT NULL, payload TEXT NOT NULL, previous_hash TEXT NOT NULL, row_hash TEXT NOT NULL)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_projects ON events(project, seq)")
    db.commit()
    return db


def ingest(db, event):
    event = validate(event)
    normalized = dict(event)
    normalized["occurred_at"] = iso_utc(event.get("occurred_at"))
    normalized.setdefault("mission_id", None)
    normalized.setdefault("original_timezone", None)
    normalized.setdefault("confidence", None)
    normalized.setdefault("alternative_explanations", [])
    normalized.setdefault("approval_is_authority", False)  # even independently verified event is evidence, not an authorization token
    normalized["event_id"] = "arc-" + str(uuid.uuid4())
    normalized["recorded_at"] = now()
    with db:
        last = db.execute("SELECT row_hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        prev = last["row_hash"] if last else "GENESIS"
        payload = canonical(normalized)
        row_hash = hashlib.sha256((prev + "\n" + payload).encode()).hexdigest()
        db.execute("INSERT INTO events(event_id,source_key,project,event_type,occurred_at,time_precision,recorded_at,visibility,sensitivity,payload,previous_hash,row_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (normalized["event_id"], normalized["source_key"], normalized["project"], normalized["event_type"], normalized["occurred_at"], normalized["time_precision"], normalized["recorded_at"], normalized["visibility"], normalized["sensitivity"], payload, prev, row_hash))
    return {"event_id": normalized["event_id"], "row_hash": row_hash, "recorded_at": normalized["recorded_at"]}


def records(db, project, limit=200, owner=False, query=None):
    if not project:
        raise ValueError("project scope required")
    clauses = ["project=?"]
    args = [project]
    if not owner:
        clauses.extend(["visibility='council'", "sensitivity='ordinary'"])
    if query:
        clauses.append("payload LIKE ?")
        args.append("%" + query + "%")
    sql = "SELECT seq,payload,row_hash FROM events WHERE " + " AND ".join(clauses) + " ORDER BY seq DESC LIMIT ?"
    args.append(min(max(limit, 1), 1000))
    rows = db.execute(sql, args).fetchall()
    return [dict(json.loads(row["payload"]), chain_hash=row["row_hash"]) for row in rows]


def timeline(db, project, limit=100, owner=False):
    entries = records(db, project, limit, owner)
    # Accurate ordering primarily by available occurred_at; unknown times preserve recorded order with explicit null.
    return {"project": project, "coverage": "Only explicitly ingested events", "items": list(reversed(entries))}


def handoff(db, project, role, mission_id, limit=50):
    if role not in ROLES:
        raise ValueError(f"unknown role: {role}")
    if role == "Owner":
        raise ValueError("owner analytics inspection is a separate authenticated host responsibility; cannot be issued through role-argument handoff")
    if not mission_id:
        raise ValueError("mission_id required")
    allowed = ALLOWED_BY_ROLE[role]
    selected = [e for e in records(db, project, min(limit * 3, 500)) if e["event_type"] in allowed]
    selected = selected[:limit]
    return {
        "type": "CONTEXT_PACKET", "schema_version": "0.1.0", "requested_by": role,
        "mission_id": mission_id, "project": project, "generated_at_utc": now(),
        "scope": "Ordinary-source events for named project; this CLI does not authenticate the requesting agent",
        "record_count": len(selected),
        "records": [{k: v for k, v in e.items() if k in ("event_id", "project", "event_type", "actor", "summary", "source_ref", "occurred_at", "time_precision", "recorded_at", "assertion", "verified_status", "evidence", "mission_id", "chain_hash", "approval_is_authority")} for e in selected],
        "excluded_sensitive_categories": ["sensitive", "personal_analytics", "owner_only"],
        "owner_release_approval_inferred": False,
        "blocked_from_execution_until_council_gates": True
    }


def verify(db):
    prev = "GENESIS"
    failures = []
    total = 0
    for row in db.execute("SELECT * FROM events ORDER BY seq ASC"):
        total += 1
        expected = hashlib.sha256((prev + "\n" + row["payload"]).encode()).hexdigest()
        if row["previous_hash"] != prev or row["row_hash"] != expected:
            failures.append(row["seq"])
        prev = row["row_hash"]
    return {"status": "PASS" if not failures else "FAIL", "rows": total, "failed_sequences": failures, "warning": "Local hash chains cannot prove tamper resistance against a privileged database editor."}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="archivist", description="Project OS local Codex. No background chat access.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for cmd in ("init", "ingest", "timeline", "search", "handoff", "verify"):
        p = sub.add_parser(cmd)
        p.add_argument("--db", default="archivist.sqlite")
        if cmd == "ingest": p.add_argument("--file", required=True)
        if cmd in ("timeline", "search", "handoff"):
            p.add_argument("--project", required=True)
            p.add_argument("--limit", type=int, default=100 if cmd != "handoff" else 50)
        if cmd == "search": p.add_argument("--query", required=True)
        if cmd == "handoff":
            p.add_argument("--role", required=True, choices=sorted(ROLES - {"Owner"}))
            p.add_argument("--mission-id", required=True)
    args = parser.parse_args(argv)
    db = connect(Path(args.db))
    try:
        if args.cmd == "init": output = {"status": "INITIALISED", "db": args.db}
        elif args.cmd == "ingest": output = ingest(db, json.loads(Path(args.file).read_text(encoding="utf8")))
        elif args.cmd == "timeline": output = timeline(db, args.project, args.limit)
        elif args.cmd == "search": output = {"project": args.project, "query": args.query, "records": records(db, args.project, args.limit, query=args.query)}
        elif args.cmd == "handoff": output = handoff(db, args.project, args.role, args.mission_id, args.limit)
        else: output = verify(db)
        print(json.dumps(output, indent=2, ensure_ascii=False))
    except (ValueError, sqlite3.IntegrityError) as ex:
        print(json.dumps({"status": "BLOCKED", "reason": str(ex)}), file=sys.stderr)
        return 2
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
