"""Offline contract tests: no fake execution, auth, approval and audit."""
import json
import sqlite3
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch
from host.owner_console import handler_factory, history, supervisor_status

TOKEN = "test-owner-token-" + "x" * 40

class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.audit = str(Path(self.temp.name) / "audit.sqlite3")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_factory(TOKEN, self.audit))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = "http://127.0.0.1:%d" % self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.temp.cleanup()

    def request(self, path, method="GET", action=None, auth=True):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = "Bearer " + TOKEN
        data = json.dumps({"action": action}).encode() if action is not None else None
        req = Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with urlopen(req, timeout=3) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def test_auth_required(self):
        self.assertEqual(self.request("/console/audit", auth=False)[0], 401)
        self.assertEqual(self.request("/console/action", "POST", "request_owner_review", False)[0], 401)
        self.assertEqual(history(self.audit), [])

    def test_council_unavailable_is_explicit(self):
        result = supervisor_status(Path(self.temp.name) / "missing")
        self.assertFalse(result["available"])
        self.assertEqual(result["runs"], [])

    def test_council_reads_real_sqlite_without_mutating(self):
        root = Path(self.temp.name) / "council"
        folder = root / "supervisor"
        folder.mkdir(parents=True)
        dbpath = folder / "state.sqlite3"
        with sqlite3.connect(dbpath) as db:
            db.execute("CREATE TABLE runs (mission_id TEXT,stage TEXT,role TEXT,run_id TEXT,attempt INT,status TEXT,started REAL,heartbeat REAL,exit_code INT,reason TEXT,changed REAL)")
            db.execute("CREATE TABLE incidents (mission_id TEXT,run_id TEXT,stage TEXT,kind TEXT,reason TEXT,at REAL,sent INT)")
            db.execute("INSERT INTO runs VALUES ('MISSION-1','checker','Checker','run-1',1,'blocked',1,2,NULL,'owner approval required',3)")
        result = supervisor_status(root)
        self.assertTrue(result["available"])
        self.assertEqual(result["runs"][0]["status"], "blocked")
        self.assertEqual(result["approval_gate"], "not_evaluated_read_only")
        self.assertEqual(result["release_gate"], "not_evaluated_read_only")

    def test_cross_origin_action_rejected(self):
        headers = {"Authorization": "Bearer " + TOKEN,
                   "Content-Type": "application/json",
                   "Origin": "https://attacker.example"}
        req = Request(self.base + "/console/action",
                      data=b'{"action":"request_owner_review"}',
                      method="POST", headers=headers)
        with self.assertRaises(HTTPError) as caught:
            urlopen(req, timeout=3)
        self.assertEqual(caught.exception.code, 403)
        self.assertEqual(history(self.audit), [])

    def test_audit_file_private(self):
        self.request("/console/action", "POST", "request_owner_review")
        self.assertEqual(Path(self.audit).stat().st_mode & 0o777, 0o600)

    def test_unknown_action_cannot_execute(self):
        self.assertEqual(self.request("/console/action", "POST", "merge_pr")[0], 400)
        self.assertEqual(history(self.audit), [])

    def test_approval_is_never_granted(self):
        status, result = self.request("/console/action", "POST", "request_owner_review")
        self.assertEqual(status, 202)
        self.assertFalse(result["approval_granted"])
        self.assertEqual(history(self.audit)[0]["status"], "awaiting_review")

    @patch("host.owner_console.github_status", return_value={"source":"github_api","checks":[]})
    def test_real_refresh_is_audited(self, github):
        status, result = self.request("/console/action", "POST", "refresh_git_status")
        self.assertEqual(status, 200)
        github.assert_called_once()
        self.assertEqual(result["source"], "github_api")
        self.assertEqual(history(self.audit)[0]["status"], "completed")

    @patch("host.owner_console.github_status", side_effect=OSError("offline"))
    def test_network_failure_not_success(self, github):
        status, result = self.request("/console/action", "POST", "refresh_git_status")
        self.assertEqual(status, 503)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(history(self.audit)[0]["status"], "failed")

if __name__ == "__main__":
    unittest.main()
