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
