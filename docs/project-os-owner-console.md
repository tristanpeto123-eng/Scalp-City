# Project OS owner console — staged implementation

This is a **new opt-in component**, not a replacement for the existing Brain, Council, supervisor or BLACK_OPS_CMD task tools.

## Verified current deployment
Railway project `Project OS — Brain & Council` runs `python -m host.personal_ai_review` from `tristanpeto123-eng/Scalp-City`. Its existing `/state` volume is preserved. The deployed Brain offers `/healthz` and an optional bearer-protected `/status`; no authenticated command execution HTTP API was established.

## What this PR actually implements
- Mobile-first owner console at `/console` with an in-memory bearer token (not stored in localStorage).
- Explicit allowlist of two operations: real GitHub PR #75/CI refresh using GitHub's API, and **recording a request for owner review**. The latter is NOT an approval.
- SQLite audit on persistent storage with explicit completed, awaiting_review and failed states.
- No arbitrary command execution, no trading, no GitHub merge, no Council approval bypass.
- Unit tests and a PR workflow for authentication, audit, and failure handling.

## Deployment prerequisites — NOT yet fulfilled
1. Configure a unique `POS_CONSOLE_TOKEN` of at least 32 characters in a secret store.
2. Expose an HTTPS reverse proxy or separately routed service; **do not expose the console over public HTTP** because the bearer token would travel unencrypted.
3. Provide persistent `POS_CONSOLE_AUDIT=/state/console/audit.sqlite3` and `POS_CONSOLE_PORT` as needed.
4. Run `python -m host.owner_console` in a separately managed process. It is intentionally not started automatically by the existing production host.
5. Add authenticated, least-privilege GitHub API integration and backend task claim/lease adapters before enabling merge/deploy/task-execution buttons. GitHub public API monitoring is rate-limited and is not a substitute for authenticated full CI/check/protection verification.
6. Complete security review and test results before production exposure.

## Validation
`python -m unittest discover -s tests -p 'test_owner_console.py' -v`

**Status:** staged PR implementation; not deployed, not an autonomous execution engine. Do not claim end-to-end operational status.

## Security review update (2026-11-08)
- Initial GitHub Actions run 42511997784 completed successfully for commit 329fd878; this does **not** validate later security changes.
- Security hardening on the PR branch: server binds only to 127.0.0.1; no direct public binding; nonce-scoped JavaScript CSP; cross-origin write requests rejected; SQLite audit rejects symlink targets and is mode 0600.
- Added tests for cross-origin action denial and audit-file permissions.
- A missing or pending CI run for the latest head is a **blocker**. Do not merge based on the initial green run.
- Console remains inaccessible externally until HTTPS proxy, secret provisioning and security approval are completed. Never expose loopback server directly.
- GitHub workflow-run list is informational, not a complete required-checks/branch-protection verdict.

## Council dashboard integration (staged)
- Added authenticated GET /console/council: reads actual supervisor SQLite run and incident rows using read-only mode, with explicit unavailable state. Set POS_COUNCIL_ROOT to the existing trusted Council root containing supervisor/state.sqlite3.
- The interface now displays Council execution status and incidents alongside GitHub monitoring and the audit log.
- Request throttling is implemented per client IP in-process (30 requests/minute). This is supplementary; enforce rate limits and TLS at the proxy.
- BLACK_OPS_CMD connector currently exposes list/read/claim/report only; it does **not** expose owner task submission or HTTP worker API. No remote task execution/claim adapter is represented as operational.
- Owner approval and release gates remain solely in the existing Council runtime. The console never infers gate state or issues approval; approval state is explicitly not evaluated in this read-only view.
- Run new PR CI on the latest code commit, inspect branch protection, and perform an HTTPS/proxy integration review before any merge or deployment.
