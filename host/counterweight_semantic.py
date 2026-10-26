"""External Counterweight semantic-review handoff with two-role HMAC attestations.

This module NEVER performs semantic reasoning itself or claims to have invoked an
independent model. External review evidence and signatures must be provided by
separately authorised parties. Missing credentials/evidence fail closed.
"""
import hashlib
import hmac
import json
import os
import sqlite3
import time
from pathlib import Path
from council.runtime import dispatcher_v0_2 as dispatch

MISSION_ID = "MISSION-20261026-BE0827D3BD"
STAGE = "COUNTERBALANCE_PROMPT_GATE"
REQUEST = "COUNTERWEIGHT_REVIEW_REQUEST.json"
REVIEW = "COUNTERWEIGHT_REVIEW.json"
REVIEW_SIG = "COUNTERWEIGHT_REVIEW.hmac"
ATTEST = "COUNTERWEIGHT_ATTESTATION.json"
ATTEST_SIG = "COUNTERWEIGHT_ATTESTATION.hmac"
EVIDENCE = "COUNTERWEIGHT_EXTERNAL_EVIDENCE.json"
MAX_EVIDENCE = 262144


class ReviewHeld(ValueError):
    """Fail-closed condition; no mission-stage mutation."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    if path.is_symlink() or not path.is_file():
        raise ReviewHeld("MISSING_OR_SYMLINK_ARTIFACT")
    return json.loads(path.read_text(encoding="utf-8"))


def write_once(path, value):
    """Creation only; never overwrite evidence on restart."""
    raw = json.dumps(value, indent=2, ensure_ascii=False).encode("utf-8") + b"\n"
    if path.is_symlink():
        raise ReviewHeld("SYMLINK_ARTIFACT")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise ReviewHeld("REVIEW_REQUEST_CHANGED")
        return False
    with os.fdopen(fd, "wb") as output:
        output.write(raw)
    return True


def mission_state(root):
    root = Path(root).resolve(strict=True)
    mission = root / "missions" / MISSION_ID
    if mission.is_symlink() or not mission.is_dir():
        raise ReviewHeld("MISSING_MISSION")
    state = dispatch.load(mission)  # verifies hash-linked event chain
    if state.get("mission_id") != MISSION_ID or state.get("project") != "project-os-personal":
        raise ReviewHeld("WRONG_MISSION")
    if state.get("owner_approval_granted") is not False or state.get("release_authorised") is not False:
        raise ReviewHeld("OWNER_GATE_INVARIANT")
    return mission, state


def integrity_ready(root, mission, state):
    if state.get("status") != "ACTIVE" or state.get("stage_index") != 0:
        raise ReviewHeld("NOT_AT_COUNTERWEIGHT_GATE")
    original = state.get("original_prompt", "")
    if digest(original.encode("utf-8")) != state.get("original_prompt_sha256"):
        raise ReviewHeld("PROMPT_HASH_MISMATCH")
    charter_path = mission / "PROJECT_CHARTER.json"
    integrity_path = mission / "COUNTERWEIGHT_INTEGRITY.json"
    charter = read_json(charter_path)
    integrity = read_json(integrity_path)
    if charter.get("owner_request_exact") != original:
        raise ReviewHeld("CHARTER_PROMPT_MISMATCH")
    if (integrity.get("mission_id") != MISSION_ID or
            integrity.get("prompt_sha256") != state["original_prompt_sha256"] or
            integrity.get("integrity") != "PASS" or
            integrity.get("semantic_review") != "REQUIRED" or
            integrity.get("owner_release_authorised") is not False):
        raise ReviewHeld("INTEGRITY_PACKET_INVALID")
    db_path = Path(root) / "supervisor" / "state.sqlite3"
    if not db_path.is_file():
        raise ReviewHeld("SUPERVISOR_NOT_READY")
    con = sqlite3.connect("file:" + str(db_path) + "?mode=ro", uri=True, timeout=5)
    try:
        row = con.execute("SELECT status,exit_code,stage FROM runs WHERE mission_id=?",
                          (MISSION_ID,)).fetchone()
    finally:
        con.close()
    if not row or row != ("AWAITING_REPORT", 0, STAGE):
        raise ReviewHeld("INTEGRITY_WORKER_NOT_CONFIRMED_COMPLETE")
    return charter, charter_path, integrity_path


def prepare(root):
    mission, state = mission_state(root)
    charter, charter_path, integrity_path = integrity_ready(root, mission, state)
    value = {
        "schema": "counterweight-semantic-request/1",
        "mission_id": MISSION_ID,
        "stage": STAGE,
        "role": "Counterweight",
        "project": state["project"],
        "baseline_ref": state["baseline_ref"],
        "original_prompt": state["original_prompt"],
        "original_prompt_sha256": state["original_prompt_sha256"],
        "charter": charter,
        "charter_file_sha256": digest(charter_path.read_bytes()),
        "integrity_file_sha256": digest(integrity_path.read_bytes()),
        "criteria": [
            "Preserve the unmodified exact owner instruction and its intended result.",
            "State scope boundaries, assumptions, conflicts and concrete risks.",
            "Distinguish established evidence from inference and unresolved gaps.",
            "Decide VERIFIED, NEEDS_REVISION or BLOCKED; do not hide corrections.",
            "Provide a source-grounded Director handoff when VERIFIED.",
            "No Checker G0/G1, owner approval, or release gate may be bypassed."
        ],
        "provenance_requirement": "Separately reviewed, source-grounded, dual-attested external review"
    }
    path = mission / REQUEST
    created = write_once(path, value)
    return {"event": "COUNTERWEIGHT_SEMANTIC_STATUS", "mission_id": MISSION_ID,
            "request": "CREATED" if created else "READY",
            "request_sha256": digest(path.read_bytes()),
            "verdict": "PENDING_EXTERNAL_REVIEW", "stage": STAGE,
            "owner_approval": False, "release_authorised": False}


def _key(name):
    value = os.environ.get(name, "")
    if len(value.encode("utf-8")) < 32:
        raise ReviewHeld("ATTESTATION_KEYS_NOT_CONFIGURED")
    return value.encode("utf-8")


def _check_signed(path, sig_path, key):
    value = read_json(path)
    sig = sig_path.read_text(encoding="ascii").strip() if sig_path.is_file() and not sig_path.is_symlink() else ""
    if len(sig) != 64 or any(c not in "0123456789abcdef" for c in sig):
        raise ReviewHeld("MISSING_OR_INVALID_ATTESTATION_SIGNATURE")
    expected = hmac.new(key, canonical(value), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        raise ReviewHeld("ATTESTATION_SIGNATURE_MISMATCH")
    return value


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def accept_external_review(root):
    """Import ONLY a real, separately signed review; never synthesize a verdict."""
    mission, state = mission_state(root)
    integrity_ready(root, mission, state)
    request_path = mission / REQUEST
    request = read_json(request_path)
    if request.get("original_prompt_sha256") != state["original_prompt_sha256"]:
        raise ReviewHeld("REVIEW_REQUEST_PROMPT_MISMATCH")
    # Existing request cannot change invisibly because prepare() is idempotent.
    prepare(root)
    if not all((mission / name).is_file() for name in (REVIEW, REVIEW_SIG, ATTEST, ATTEST_SIG, EVIDENCE)):
        raise ReviewHeld("AWAITING_EXTERNAL_REVIEW_FILES")
    reviewer_key = _key("POS_CW_REVIEWER_HMAC_KEY")
    operator_key = _key("POS_CW_OPERATOR_HMAC_KEY")
    if hmac.compare_digest(reviewer_key, operator_key):
        raise ReviewHeld("REVIEWER_AND_OPERATOR_KEYS_IDENTICAL")
    reviewer = _check_signed(mission / REVIEW, mission / REVIEW_SIG, reviewer_key)
    operator = _check_signed(mission / ATTEST, mission / ATTEST_SIG, operator_key)
    trace_path = mission / EVIDENCE
    if trace_path.is_symlink() or trace_path.stat().st_size > MAX_EVIDENCE or trace_path.stat().st_size == 0:
        raise ReviewHeld("INVALID_EXTERNAL_EVIDENCE_FILE")
    trace = read_json(trace_path)
    if not isinstance(reviewer, dict) or not isinstance(operator, dict) or not isinstance(trace, dict):
        raise ReviewHeld("INVALID_REVIEW_STRUCTURE")
    req_hash = digest(request_path.read_bytes())
    review_hash = digest(canonical(reviewer))
    evidence_hash = digest(trace_path.read_bytes())
    for doc in (reviewer, operator):
        if doc.get("mission_id") != MISSION_ID or doc.get("request_sha256") != req_hash:
            raise ReviewHeld("REVIEW_NOT_BOUND_TO_CURRENT_REQUEST")
    if reviewer.get("schema") != "counterweight-external-review/1" or operator.get("schema") != "counterweight-operator-attestation/1":
        raise ReviewHeld("REVIEW_SCHEMA_INVALID")
    if reviewer.get("verdict") not in {"VERIFIED", "NEEDS_REVISION", "BLOCKED"}:
        raise ReviewHeld("INVALID_SEMANTIC_VERDICT")
    for field in ("reviewer_id", "provider", "invocation_id", "summary"):
        if not _nonempty(reviewer.get(field)):
            raise ReviewHeld("INCOMPLETE_REVIEW_METADATA")
    for field in ("operator_id", "external_source_reference"):
        if not _nonempty(operator.get(field)):
            raise ReviewHeld("INCOMPLETE_OPERATOR_ATTESTATION")
    if reviewer["reviewer_id"] == operator["operator_id"]:
        raise ReviewHeld("REVIEWER_AND_OPERATOR_NOT_SEPARATE")
    if (operator.get("reviewer_id") != reviewer["reviewer_id"] or
            operator.get("review_sha256") != review_hash or
            operator.get("evidence_sha256") != evidence_hash or
            operator.get("independently_sourced_and_checked") is not True):
        raise ReviewHeld("EXTERNAL_PROVENANCE_NOT_ATTESTED")
    if (trace.get("provider") != reviewer["provider"] or
            trace.get("invocation_id") != reviewer["invocation_id"] or
            trace.get("request_sha256") != req_hash):
        raise ReviewHeld("EXTERNAL_INVOCATION_TRACE_MISMATCH")
    if reviewer.get("original_prompt_sha256") != state["original_prompt_sha256"]:
        raise ReviewHeld("REVIEW_ORIGINAL_PROMPT_MISMATCH")
    for field in ("scope_limits", "assumptions", "risk_findings", "required_corrections", "evidence_refs"):
        values = reviewer.get(field)
        if not isinstance(values, list) or any(not _nonempty(x) for x in values):
            raise ReviewHeld("INVALID_REVIEW_FINDINGS")
    if not reviewer["scope_limits"] or not reviewer["evidence_refs"]:
        raise ReviewHeld("MISSING_SCOPE_OR_EVIDENCE")
    if reviewer["verdict"] == "VERIFIED" and (reviewer["required_corrections"] or not _nonempty(reviewer.get("handoff_text"))):
        raise ReviewHeld("UNRESOLVED_CORRECTIONS_OR_HANDOFF")
    evidence_files = (request_path, mission / REVIEW, mission / REVIEW_SIG,
                      mission / ATTEST, mission / ATTEST_SIG, trace_path)
    refs = [{"path": str(path.resolve(strict=True)), "sha256": digest(path.read_bytes())}
            for path in evidence_files]
    report = {
        "stage": STAGE,
        "role": "Counterweight",
        "verdict": reviewer["verdict"],
        "summary": reviewer["summary"],
        "reviewer_mode": "external_review_dual_attested_by_operator",
        "original_prompt": state["original_prompt"],
        "original_prompt_sha256": state["original_prompt_sha256"],
        "scope_limits": reviewer["scope_limits"],
        "assumptions": reviewer["assumptions"],
        "risk_findings": reviewer["risk_findings"],
        "required_corrections": reviewer["required_corrections"],
        "evidence_refs": reviewer["evidence_refs"],
        "handoff_text": reviewer.get("handoff_text", ""),
        "artifact_refs": refs,
        "owner_approval_granted": False,
        "release_authorised": False,
    }
    result = dispatch.submit(mission, report)
    if result["owner_approval_granted"] is not False or result["release_authorised"] is not False:
        raise RuntimeError("Owner gate was changed unexpectedly")
    return {"event": "COUNTERWEIGHT_SEMANTIC_STATUS", "mission_id": MISSION_ID,
            "review": "ACCEPTED_EVIDENCE_BACKED", "verdict": reviewer["verdict"],
            "stage_index": result["stage_index"], "mission_status": result["status"],
            "owner_approval": False, "release_authorised": False}


def monitor(root, interval=15):
    """Prepare queue, consume only verified external receipts; no AI impersonation."""
    last = None
    while True:
        try:
            prepare_result = prepare(root)
            try:
                status = accept_external_review(root)
            except ReviewHeld as held:
                status = {**prepare_result, "receipt": held.code}
        except ReviewHeld as held:
            if held.code == "NOT_AT_COUNTERWEIGHT_GATE":
                _, current = mission_state(root)
                status = {"event": "COUNTERWEIGHT_SEMANTIC_STATUS", "mission_id": MISSION_ID,
                          "review": "COUNTERWEIGHT_HANDOFF_COMPLETE" if current.get("stage_index", 0) > 0 else "HELD",
                          "mission_status": current["status"],
                          "next_stage_index": current["stage_index"],
                          "owner_approval": False, "release_authorised": False}
            else:
                status = {"event": "COUNTERWEIGHT_SEMANTIC_STATUS", "mission_id": MISSION_ID,
                          "review": "HELD", "reason": held.code}
        except Exception as exc:
            status = {"event": "COUNTERWEIGHT_SEMANTIC_STATUS", "mission_id": MISSION_ID,
                      "review": "ERROR", "error_type": type(exc).__name__}
        serialised = json.dumps(status, sort_keys=True)
        if serialised != last:
            print(serialised, flush=True)
            last = serialised
        time.sleep(interval)
