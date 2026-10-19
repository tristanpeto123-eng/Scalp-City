# Authorization & approval contract — POS-PLAN v0.1.1

**Scope:** local prototype. No cloud permission broker, connector interceptor, separate authenticated user authorization service, or production host is installed by this package.

1. Owner decision is an independently verified event, not text emitted by Director, Executor, Checker or any LLM. The event must bind owner identity, project, exact version, source baseline digest, candidate artifact SHA-256, destination, allowed operations, timestamp, expiry/revocation and purpose.
2. Planning consent (G0a) never confers source access (G0b). Read access to a named connector is separately consented. Isolated build (G2) never confers release rights (G5). Actual production execution needs a new host-enforced authorization (G5b).
3. An executor must receive a **host-issued** short-lived scoped grant that is checked at every tool boundary; no handoff JSON field may mint or expand permissions. Scope MUST be default-deny and least privilege across project, operation, repository, path, destination, expiry and connector identity.
4. Re-read source digests immediately before irreversible action and after build/release. Mismatch or unavailable approval source blocks execution. Record reasons in the event log.
5. PASS from Checker is quality evidence, not authorization. Reviewer identity must be authenticated independently of the builder and record a relevant test run and artifact SHA. Creative Director may advise design QA but cannot override Checker or owner.
6. Revocation, unexpected permission mismatch, missing source, failed hash verification, stale baseline, replayed authorization, missing independent Checker attestation or invalid audit log => BLOCKED. Production release disabled by default.
7. The prototype's `PermissionGate` accepts a grant plus expected digest from a trusted caller. In a real host, that digest MUST come from an out-of-band authenticated permission service. The prototype does NOT supply such a service, and its actor strings do NOT authenticate real plugins.
8. Append-only local SHA-256 chained logs detect accidental or offline modification but are not independent evidence of authorship and can be rewritten by a fully privileged attacker. Production requires signed events/append-only remotely anchored storage, concurrency protection and backups.

**Decision for this run:** User approved planning, private candidate development, isolated QA and demonstration. Exact-version production release has not been approved against an immutable candidate hash and destination. No production deployment or external app connection occurred.
