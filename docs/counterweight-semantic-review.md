# Counterweight semantic-review handoff

**Status:** External-review intake implemented. No independent model or secured reviewer transport is connected.

## Sequence

1. The existing integrity worker completes with supervisor AWAITING_REPORT, exit 0, and a valid integrity packet.
2. The private host creates COUNTERWEIGHT_REVIEW_REQUEST.json once under the existing mission directory /state/council/missions/MISSION-20261026-BE0827D3BD, containing the exact owner instruction, charter and verification hashes. The request body is never logged.
3. A separate reviewer evaluates the unmodified private request through an approved independent channel and retains the actual invocation trace.
4. A distinct operator authenticates the reviewer and verifies the trace out of band.
5. Reviewer and operator sign their evidence with separate credentials. The host validates hashes, signatures, identity fields and the unchanged stage before calling the existing Council dispatcher. Only VERIFIED advances to the Director stage. Other verdicts hold. Checker and owner gates remain unchanged.

## Private evidence files (never commit to GitHub)

- COUNTERWEIGHT_REVIEW_REQUEST.json: immutable original prompt, charter, review criteria, hashes, mission binding.
- COUNTERWEIGHT_EXTERNAL_EVIDENCE.json: actual external source trace with provider, invocation_id and request_sha256.
- COUNTERWEIGHT_REVIEW.json: schema counterweight-external-review/1, mission_id, request_sha256, original_prompt_sha256, reviewer_id, provider, invocation_id, verdict, summary, scope_limits, assumptions, risk_findings, required_corrections, evidence_refs, handoff_text.
- COUNTERWEIGHT_REVIEW.hmac: HMAC SHA256 hex digest over canonical UTF8 JSON of reviewer document.
- COUNTERWEIGHT_ATTESTATION.json: schema counterweight-operator-attestation/1, mission and request binding, separate operator_id, reviewer_id, canonical review_sha256, exact evidence file evidence_sha256, independently_sourced_and_checked true, external_source_reference.
- COUNTERWEIGHT_ATTESTATION.hmac: operator signature over canonical operator document.

Canonical JSON: json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"). Sign with hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest().

Private credentials POS_CW_REVIEWER_HMAC_KEY and POS_CW_OPERATOR_HMAC_KEY must each be at least 32 UTF8 bytes and distinct. They are **NOT configured**. Do not commit, print, paste or share them. Provision via approved secure secret management only when two separately accountable signing parties are ready. Council stage workers do not inherit these secrets. The secured upload transport and independent model credentials are also not configured.

**Security limitations:** HMAC establishes possession of the corresponding signing key, not automatic proof that an independent model was invoked. A different operator must check the actual external invocation. A compromised host controlling both keys could spoof both signatures. For stronger production independence, use separately held signing keys/public-key verification and authenticated provider receipts.

Railway event COUNTERWEIGHT_SEMANTIC_STATUS contains only the mission ID, high-level status, request digest and approval booleans. It never logs raw prompts or review evidence.

Integration tests: python -m unittest discover -s tests -p 'test_counterweight_semantic_integration.py'.
