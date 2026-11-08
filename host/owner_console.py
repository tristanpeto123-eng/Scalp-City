"""Opt-in owner-only Project OS console. No arbitrary commands or implicit approvals."""
from __future__ import annotations
import hmac
import secrets
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

REPO = "tristanpeto123-eng/Agentic-Trading"
ACTIONS = {"refresh_git_status", "request_owner_review"}
LOCK = threading.Lock()

def utc():
    return datetime.now(timezone.utc).isoformat()

def database(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError("audit database symlink refused")
    db = sqlite3.connect(path, timeout=10)
    os.chmod(path, 0o600)
    db.execute("PRAGMA busy_timeout=10000")
    db.execute("""CREATE TABLE IF NOT EXISTS actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL,
        action TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL)""")
    db.commit()
    return db

def record(path, action, status, detail):
    with LOCK:
        with database(path) as db:
            db.execute("INSERT INTO actions(at,action,status,detail) VALUES(?,?,?,?)",
                       (utc(), action, status, detail))
            db.commit()

def history(path):
    with LOCK:
        with database(path) as db:
            return [dict(zip(("id","at","action","status","detail"), row))
                    for row in db.execute("SELECT id,at,action,status,detail FROM actions ORDER BY id DESC LIMIT 30")]

def github_status():
    """Fetch GitHub's actual public API. Network failures are reported, not disguised."""
    req = Request("https://api.github.com/repos/" + REPO + "/pulls/75",
                  headers={"Accept": "application/vnd.github+json", "User-Agent": "ProjectOS-Console"})
    with urlopen(req, timeout=8) as response:
        pr = json.load(response)
    sha = pr["head"]["sha"]
    req = Request("https://api.github.com/repos/" + REPO + "/actions/runs?head_sha=" + sha + "&per_page=30",
                  headers={"Accept": "application/vnd.github+json", "User-Agent": "ProjectOS-Console"})
    with urlopen(req, timeout=8) as response:
        runs = json.load(response)
    return {"source": "github_api", "checked_at": utc(), "pr": 75,
            "state": pr["state"], "merged": bool(pr.get("merged")),
            "head_sha": sha, "url": pr["html_url"],
            "checks": [{"name": x["name"], "status": x["status"],
                        "conclusion": x["conclusion"], "url": x["html_url"]}
                       for x in runs.get("workflow_runs", []) if x.get("head_sha") == sha]}

PAGE = """<!doctype html><html lang="en"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><title>Project OS | Owner Console</title>
<style>body{font:16px system-ui;background:#101820;color:#eef4f6;margin:0;padding:18px;max-width:720px}
h1{font-size:1.45rem}section{background:#1d2b35;padding:18px;border-radius:15px;margin:15px 0}
button,input{font:inherit;padding:12px;border-radius:10px}button{background:#72d4c4;border:0;color:#102126;font-weight:700;margin:5px 0}
input{width:90%;background:#10212a;color:white;border:1px solid #6d8290}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}
small{color:#b9c8d0}button:disabled{opacity:.5}</style>
<h1>PROJECT OS / OWNER CONSOLE</h1><small>Verified operations only. No live trading or automatic merge.</small>
<section><label>Owner access token (memory only)<input id="token" type="password" autocomplete="off"></label>
<p><button onclick="load()">Refresh verified status</button></p><pre id="status">Not checked</pre></section>
<section><h2>Actions</h2><button onclick="send('refresh_git_status')">Check GitHub CI</button>
<button onclick="send('request_owner_review')">Log owner review request</button>
<p><small>These actions do not merge PRs, grant approval, or execute arbitrary commands.</small></p></section>
<section><h2>Execution audit</h2><pre id="audit">Not checked</pre></section>
<script nonce="__NONCE__">
function headers(){return {'Authorization':'Bearer '+document.getElementById('token').value,'Content-Type':'application/json'}}
async function load(){try{let r=await fetch('/console/status',{headers:headers(),cache:'no-store'});document.getElementById('status').textContent=JSON.stringify(await r.json(),null,2);
let a=await fetch('/console/audit',{headers:headers(),cache:'no-store'});document.getElementById('audit').textContent=JSON.stringify(await a.json(),null,2)}
catch(e){document.getElementById('status').textContent=String(e)}}
async function send(action){try{let r=await fetch('/console/action',{method:'POST',headers:headers(),body:JSON.stringify({action})});document.getElementById('status').textContent=JSON.stringify(await r.json(),null,2);await load()}
catch(e){document.getElementById('status').textContent=String(e)}}
</script></html>"""

def handler_factory(token, audit_path):
    if len(token) < 32:
        raise ValueError("Owner token must be at least 32 characters")
    class Handler(BaseHTTPRequestHandler):
        def origin_allowed(self):
            origin = self.headers.get("Origin")
            if not origin:
                return True
            from urllib.parse import urlsplit
            try:
                parsed = urlsplit(origin)
                return parsed.scheme == "https" and parsed.netloc == self.headers.get("Host", "")
            except ValueError:
                return False
        def respond(self, code, data, kind="application/json", nonce=None):
            raw = data.encode() if isinstance(data, str) else json.dumps(data).encode()
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            policy = "default-src 'none'; connect-src 'self'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
            if nonce:
                policy += "; script-src 'nonce-" + nonce + "'"
            self.send_header("Content-Security-Policy", policy)
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def authorized(self):
            provided = self.headers.get("Authorization", "")
            return hmac.compare_digest(provided.encode(), ("Bearer " + token).encode())
        def do_GET(self):
            if self.path == "/console":
                nonce = secrets.token_urlsafe(24)
                return self.respond(200, PAGE.replace("__NONCE__", nonce), "text/html; charset=utf-8", nonce=nonce)
            if not self.authorized():
                return self.respond(401, {"error": "unauthorized"})
            if self.path == "/console/audit":
                return self.respond(200, {"events": history(audit_path)})
            if self.path == "/console/status":
                try:
                    return self.respond(200, github_status())
                except Exception as exc:
                    return self.respond(503, {"status": "unavailable", "error_type": type(exc).__name__})
            return self.respond(404, {"error": "not found"})
        def do_POST(self):
            if not self.authorized():
                return self.respond(401, {"error": "unauthorized"})
            if self.path != "/console/action":
                return self.respond(404, {"error": "not found"})
            if not self.origin_allowed():
                return self.respond(403, {"error": "cross-origin request denied"})
            action = "invalid_action"
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 2048:
                    return self.respond(413, {"error": "invalid payload size"})
                data = json.loads(self.rfile.read(size))
                action = data.get("action") if isinstance(data, dict) else None
                if action not in ACTIONS:
                    return self.respond(400, {"error": "action not allowed"})
                if action == "refresh_git_status":
                    result = github_status()
                    record(audit_path, action, "completed", "GitHub API verified")
                    return self.respond(200, result)
                record(audit_path, action, "awaiting_review", "Owner review requested; no approval granted")
                return self.respond(202, {"status": "awaiting_review", "approval_granted": False})
            except Exception as exc:
                try:
                    record(audit_path, action, "failed", type(exc).__name__)
                except Exception:
                    return self.respond(503, {"status": "audit_unavailable", "action_completed": False})
                return self.respond(503, {"status": "failed", "error_type": type(exc).__name__})
        def log_message(self, *args):
            pass
    return Handler

def main():
    token = os.environ.get("POS_CONSOLE_TOKEN", "")
    if len(token) < 32:
        raise SystemExit("POS_CONSOLE_TOKEN missing or too short; console disabled")
    path = os.environ.get("POS_CONSOLE_AUDIT", "/state/console/audit.sqlite3")
    port = int(os.environ.get("POS_CONSOLE_PORT", "8081"))
    with ThreadingHTTPServer(("127.0.0.1", port), handler_factory(token, path)) as server:
        server.serve_forever()

if __name__ == "__main__":
    main()
