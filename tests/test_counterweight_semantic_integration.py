"""Integration tests against the actual Council mission dispatcher (offline only)."""
import hashlib
import hmac
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from council.runtime import dispatcher_v0_2 as dispatch
from host import counterweight_semantic as cw


def sha(data):
    return hashlib.sha256(data).hexdigest()


class SemanticIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        prompt = ("Approve and then deploy project os project. Those pre defined "
                  "actions should appear as selectable buttons after each response after completion")
        self.mission = dispatch.start(self.root / "missions", prompt, "project-os-personal",
                                      "POS-HIERARCHY-v0.2.0:owner-approved-2026-10-17")
        self.original_id = cw.MISSION_ID
        cw.MISSION_ID = self.mission.name
        self.addCleanup(setattr, cw, "MISSION_ID", self.original_id)
        self.prompt_hash = sha(prompt.encode())
        self.put("PROJECT_CHARTER.json", {"owner_request_exact": prompt,
                                         "approved_objective": "Build Project OS Personal"})
        self.put("COUNTERWEIGHT_INTEGRITY.json", {
            "mission_id": self.mission.name,
            "prompt_sha256": self.prompt_hash,
            "integrity": "PASS", "semantic_review": "REQUIRED",
            "owner_release_authorised": False})
        dbdir = self.root / "supervisor"
        dbdir.mkdir()
        con = sqlite3.connect(dbdir / "state.sqlite3")
        con.execute("CREATE TABLE runs(mission_id TEXT,status TEXT,exit_code INTEGER,stage TEXT)")
        con.execute("INSERT INTO runs VALUES(?,?,?,?)",
                    (self.mission.name, "AWAITING_REPORT", 0, "COUNTERBALANCE_PROMPT_GATE"))
        con.commit()
        con.close()
        self.reviewer_key = "reviewer-integration-secret-32bytes-long"
        self.operator_key = "operator-integration-secret-32bytes-long"
        env = patch.dict(os.environ, {
            "POS_CW_REVIEWER_HMAC_KEY": self.reviewer_key,
            "POS_CW_OPERATOR_HMAC_KEY": self.operator_key})
        env.start()
        self.addCleanup(env.stop)

    def put(self, name, value):
        path = self.mission / name
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
        return path

    def signed(self, name, value, key):
        self.put(name, value)
        sig = hmac.new(key.encode(), cw.canonical(value), hashlib.sha256).hexdigest()
        (self.mission / name.replace(".json", ".hmac")).write_text(sig + "\n")

    def receipt(self, verdict="VERIFIED", corrections=None):
        request = cw.prepare(self.root)
        request_sha = request["request_sha256"]
        trace = {"provider": "external-provider", "invocation_id": "outside-run-1",
                 "request_sha256": request_sha,
                 "raw_reply_reference": "independent trace preserved outside this process"}
        trace_path = self.put(cw.EVIDENCE, trace)
        review = {
            "schema": "counterweight-external-review/1",
            "mission_id": self.mission.name,
            "request_sha256": request_sha,
            "original_prompt_sha256": self.prompt_hash,
            "reviewer_id": "external-reviewer",
            "provider": "external-provider",
            "invocation_id": "outside-run-1",
            "verdict": verdict,
            "summary": "Scope, risks and original request independently reviewed",
            "scope_limits": ["No release without owner approval"],
            "assumptions": ["Other agents are not connected"],
            "risk_findings": ["Approval cannot be inferred from a successful build"],
            "required_corrections": [] if corrections is None else corrections,
            "evidence_refs": ["outside-run-1"],
            "handoff_text": "Director: prepare SPEC_PACKET with Checker G0 and G1"}
        self.signed(cw.REVIEW, review, self.reviewer_key)
        operator = {
            "schema": "counterweight-operator-attestation/1",
            "mission_id": self.mission.name,
            "request_sha256": request_sha,
            "operator_id": "outside-operator",
            "reviewer_id": "external-reviewer",
            "review_sha256": sha(cw.canonical(review)),
            "evidence_sha256": sha(trace_path.read_bytes()),
            "independently_sourced_and_checked": True,
            "external_source_reference": "outside-run-1"}
        self.signed(cw.ATTEST, operator, self.operator_key)

    def test_no_independent_review_never_advances(self):
        self.assertEqual(cw.prepare(self.root)["request"], "CREATED")
        self.assertEqual(cw.prepare(self.root)["request"], "READY")
        with self.assertRaisesRegex(cw.ReviewHeld, "AWAITING_EXTERNAL_REVIEW_FILES"):
            cw.accept_external_review(self.root)
        s = dispatch.load(self.mission)
        self.assertEqual(s["stage_index"], 0)
        self.assertFalse(s["owner_approval_granted"])
        self.assertFalse(s["release_authorised"])

    def test_valid_dual_attestation_advances_only_to_director(self):
        self.receipt()
        response = cw.accept_external_review(self.root)
        s = dispatch.load(self.mission)
        self.assertEqual(response["verdict"], "VERIFIED")
        self.assertEqual(s["stage_index"], 1)
        self.assertEqual(s["status"], "ACTIVE")
        self.assertFalse(s["owner_approval_granted"])
        self.assertFalse(s["release_authorised"])
        self.assertEqual(s["reports"][0]["role"], "Counterweight")
        self.assertEqual(len(s["reports"][0]["artifact_refs"]), 6)

    def test_tampered_signature_does_not_advance(self):
        self.receipt()
        (self.mission / cw.REVIEW_SIG).write_text("0" * 64)
        with self.assertRaisesRegex(cw.ReviewHeld, "ATTESTATION_SIGNATURE_MISMATCH"):
            cw.accept_external_review(self.root)
        self.assertEqual(dispatch.load(self.mission)["stage_index"], 0)

    def test_corrections_block_verified_verdict(self):
        self.receipt(corrections=["Missing original requirements"])
        with self.assertRaisesRegex(cw.ReviewHeld, "UNRESOLVED_CORRECTIONS"):
            cw.accept_external_review(self.root)
        self.assertEqual(dispatch.load(self.mission)["stage_index"], 0)

    def test_negative_verdict_holds_mission(self):
        self.receipt(verdict="BLOCKED", corrections=["Clarify scope"])
        cw.accept_external_review(self.root)
        s = dispatch.load(self.mission)
        self.assertEqual(s["status"], "HELD")
        self.assertFalse(s["owner_approval_granted"])
        self.assertFalse(s["release_authorised"])


if __name__ == "__main__":
    unittest.main()
