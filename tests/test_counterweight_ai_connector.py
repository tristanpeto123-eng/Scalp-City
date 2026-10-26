"""Mock-provider integration: actual Council dispatcher, no network or live secrets."""
import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import patch
from pathlib import Path
from test_counterweight_semantic_integration import SemanticIntegrationTests
from host import counterweight_semantic as gate
from host import counterweight_ai_connector as ai
from council.runtime import dispatcher_v0_2 as dispatch

FINDINGS = {
    "verdict": "NEEDS_REVISION",
    "summary": "Clarify whether approval covers external model processing",
    "scope_limits": ["No release without explicit owner action"],
    "assumptions": ["No model or signing credentials presently configured"],
    "risk_findings": ["Independent signed operator verification remains necessary"],
    "required_corrections": ["Independent operator must verify provider trace"],
    "evidence_refs": ["original_prompt_sha256", "charter_file_sha256"],
    "handoff_text": "Director must not bypass Checker or owner."
}

def mock_response(findings=None, *, status="completed", model="test-model"):
    return {"id": "resp_mock_integration_123456", "model": model, "status": status,
            "output": [{"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": json.dumps(findings or FINDINGS)}]}]}


class CounterweightAIIntegrationTests(SemanticIntegrationTests):
    def setUp(self):
        super().setUp()
        provider = patch.dict(os.environ, {
            "POS_CW_AI_ENABLE": "1", "POS_CW_AI_MODEL": "test-model",
            "POS_CW_AI_API_KEY": "test-only-not-a-real-provider-key-123"})
        provider.start()
        self.addCleanup(provider.stop)

    def test_disabled_never_calls_model(self):
        with patch.dict(os.environ, {"POS_CW_AI_ENABLE": "0"}):
            result = ai.run_once(self.root, lambda *_: self.fail("provider was called"))
        self.assertEqual(result["status"], "DISABLED_PENDING_EXPLICIT_CONFIGURATION")
        self.assertFalse((self.mission / ai.CLAIM).exists())
        self.assertEqual(dispatch.load(self.mission)["stage_index"], 0)

    def test_missing_credential_never_calls_model(self):
        with patch.dict(os.environ, {"POS_CW_AI_API_KEY": ""}):
            with self.assertRaisesRegex(gate.ReviewHeld, "API_CREDENTIAL_NOT_CONFIGURED"):
                ai.run_once(self.root, lambda *_: self.fail("provider was called"))
        self.assertFalse((self.mission / ai.CLAIM).exists())

    def test_valid_response_transferred_unsigned_and_gate_held(self):
        def fake_provider(payload, key):
            self.assertFalse(payload["store"])
            self.assertEqual(payload["model"], "test-model")
            self.assertNotIn(key, json.dumps(payload))
            return mock_response()
        result = ai.run_once(self.root, fake_provider)
        self.assertEqual(result["status"], "UNSIGNED_EVIDENCE_TRANSFERRED")
        self.assertTrue((self.mission / ai.RAW).is_file())
        self.assertFalse((self.mission / gate.REVIEW_SIG).exists())
        self.assertFalse((self.mission / gate.ATTEST_SIG).exists())
        with self.assertRaisesRegex(gate.ReviewHeld, "AWAITING_EXTERNAL_REVIEW_FILES"):
            gate.accept_external_review(self.root)
        state = dispatch.load(self.mission)
        self.assertEqual(state["stage_index"], 0)
        self.assertFalse(state["owner_approval_granted"])
        self.assertFalse(state["release_authorised"])

    def test_no_duplicate_api_call_after_restart(self):
        first = ai.run_once(self.root, lambda *_: mock_response())
        second = ai.run_once(self.root, lambda *_: self.fail("duplicate model request"))
        self.assertEqual(first["request_sha256"], second["request_sha256"])

    def test_provider_incomplete_requires_operator_reconciliation(self):
        with self.assertRaisesRegex(gate.ReviewHeld, "PROVIDER_RESPONSE_INCOMPLETE"):
            ai.run_once(self.root, lambda *_: mock_response(status="incomplete"))
        with self.assertRaisesRegex(gate.ReviewHeld, "PREVIOUS_AI_ATTEMPT"):
            ai.run_once(self.root, lambda *_: self.fail("duplicate model request"))
        self.assertEqual(dispatch.load(self.mission)["stage_index"], 0)

    def test_verified_with_unresolved_corrections_rejected(self):
        with self.assertRaisesRegex(gate.ReviewHeld, "PROVIDER_UNRESOLVED_CORRECTIONS"):
            ai.run_once(self.root, lambda *_: mock_response({**FINDINGS, "verdict": "VERIFIED"}))

    def test_provenance_claim_tamper_blocks_replay(self):
        ai.run_once(self.root, lambda *_: mock_response())
        claim_path = self.mission / ai.CLAIM
        claim = json.loads(claim_path.read_text())
        claim["request_sha256"] = "tampered"
        claim_path.write_text(json.dumps(claim))
        with self.assertRaisesRegex(gate.ReviewHeld, "RAW_RECEIPT_CLAIM_MISMATCH"):
            ai.run_once(self.root, lambda *_: self.fail("duplicate call"))

    def test_two_separate_signatures_required_for_negative_verdict(self):
        ai.run_once(self.root, lambda *_: mock_response())
        reviewer = json.loads((self.mission / gate.REVIEW).read_text())
        evidence_bytes = (self.mission / gate.EVIDENCE).read_bytes()
        sig = hmac.new(self.reviewer_key.encode(), gate.canonical(reviewer), hashlib.sha256).hexdigest()
        (self.mission / gate.REVIEW_SIG).write_text(sig)
        operator = {
            "schema": "counterweight-operator-attestation/1",
            "mission_id": self.mission.name,
            "request_sha256": reviewer["request_sha256"],
            "operator_id": "independent-operator",
            "reviewer_id": reviewer["reviewer_id"],
            "review_sha256": hashlib.sha256(gate.canonical(reviewer)).hexdigest(),
            "evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
            "independently_sourced_and_checked": True,
            "external_source_reference": reviewer["invocation_id"]}
        self.signed(gate.ATTEST, operator, self.operator_key)
        result = gate.accept_external_review(self.root)
        state = dispatch.load(self.mission)
        self.assertEqual(result["verdict"], "NEEDS_REVISION")
        self.assertEqual(state["status"], "HELD")
        self.assertFalse(state["release_authorised"])


if __name__ == "__main__":
    unittest.main()
