# Project OS trusted hosting

This repository was designated as the empty Project OS hosting repository. It contains source-only copies of Project OS Council v0.2.7, Brain v0.1.1, and Archivist v0.1.0 used for isolated Railway host deployment.

**No claim of live independent AI agents, automatic chat ingestion, complete task inventory or owner-approved project releases.** The Council supervisor starts with `stages: {}` — therefore **no stage worker commands are configured**. Brain monitors records on the host's own durable volume; old private-plugin state is **not** available until explicitly migrated. The watchdog is a separate service and must use its own persistent volume. Source code can be public; runtime records and credentials must remain private.

Brain mode: `python -m host.combined`. Watchdog mode: `python -m brain.engine.watchdog`, with `BRAIN_TARGET_HEALTH_URL=http://<verified-service-label>.railway.internal:8080/healthz` and `WATCHDOG_LEDGER=/watchdog-state/observations.sqlite3`.

Protected governance: Counterbalance prompt verification -> Directors spec -> Checker G0 -> Executor -> Checker G1 -> Director alignment -> owner release. Supervisory retries are manual only and agent workers are disconnected by default. This deployment is infrastructure commissioning, **not** a task-health completeness guarantee.
