# BRAIN-P001 v0.1.1 — Host commissioning contract

Status: **SOURCE READY, NOT HOST COMMISSIONED**. The private Brain plugin is a distribution of instructions and local Python source. It does not invoke a worker or start a host merely by installation.

## Verified source protocols

- Council 0.2.7: `missions/MISSION-*/mission.json` + hashed `events.jsonl`; `supervisor/state.sqlite3` is its optional worker journal. Brain opens database sources with SQLite read-only and does not start or restart stage workers.
- Archivist 0.1.0: `archivist.sqlite3`, ordinary council-visible events by matching project; owner-only analytics and sensitive records excluded.
- Global task coverage remains PARTIAL / UNKNOWN until the owner designates a complete task inventory and adapters enumerate all its pages. Reading a folder containing zero missions does not prove zero active tasks.

## Local deployment

Run on a trusted Linux machine with Docker Compose. Point `COUNCIL_ROOT` `REGISTERS_ROOT` and `ARCHIVIST_ROOT` at actual current stores and invoke `docker compose -f ops/docker-compose.yml up -d --build` from the Brain project root. They are mounted **read-only**. Brain and watchdog have independent state volumes. Watchdog is a separate process/container, so it may detect Brain process failure; **both still depend on one Docker host**, and a host outage can stop both. For an external failure domain, deploy watchdog on a separate host/service.

The Brain health endpoint `/healthz` is 200 only if a recent scan successfully read Council; it never asserts global completeness. `/livez` merely proves the HTTP process exists, not monitoring success. `/status` is disabled unless a random, long `BRAIN_STATUS_TOKEN` is configured and requests supply bearer auth. Avoid exposing either health endpoint publicly without deliberate network configuration.

## Railway plan (not executed)

A GitHub repository containing this full source must be selected/created and connected. Install one service using `Dockerfile`, attach a persistent `/state` volume, and configure `BRAIN_COUNCIL_ROOT` to a path where the real **current** Council missions and supervisor journal are accessible; likewise mount the master registers at `BRAIN_REGISTERS_ROOT` and the Archivist store at `BRAIN_ARCHIVIST_DB`. Provision a **separate** watchdog service from the same source; attach a separate durable volume; set `BRAIN_TARGET_HEALTH_URL` to `http://<actual-brain-private-dns>.railway.internal:<port>/healthz`. A Volume in an unrelated project does not provide access to Council's task inventory. A separate process without durable shared source data is not a commissioned connection. Use service healthchecks and host metrics; validate watchdog failure injection and restart persistence.

Security: separate source and ledger privileges, private networking, no user passwords in repo, supervisor retry and owner-gate unchanged. Watchdog records local incidents and stdout transitions only; no user notification mechanism is commissioned. Do not claim it sends alerts.

## Release/commission gates

1. Verify runtime source path is exact and not an empty demo directory. Verify Council mission, project master-register and Archivist source versions, connect read-only, list scope and last successful reads; fail closed on invalid hashes.
2. Exercise all B01–B24 on deterministic harness, then actual adapters and persistent host. Check old run replay, partial writes, lease/cycle logic, source unavailability, multi-project inventory and evidence integrity. The unit tests are partial, not 24/24 commissioning.
3. Verify two independent service/container runtime IDs, live `/healthz` and watchdog incident after Brain intentionally stops. Restart Brain and observe a new persisted scan, then a watchdog recovery incident. Test separate host outage path or provide a third-party uptime monitor if high availability is needed.
4. Log the exact artifact/commit, source receipt, run ID, test output, observation times and Checker verdict. Owner exact-version release remains distinct.

**No changes to Council governance, trading strategies, permissions, trading or financial accounts.**
