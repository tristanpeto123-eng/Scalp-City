"""Project OS personal host with independent-review handoff and private telemetry."""
import threading
from host import combined
from host import personal_recovered  # existing verified worker config and mission setup
from host.counterweight_observer import report as observe_integrity
from host.counterweight_semantic import monitor as monitor_semantic

if __name__ == "__main__":
    threading.Thread(target=observe_integrity, daemon=True,
                     name="counterweight-integrity-telemetry").start()
    threading.Thread(target=monitor_semantic, args=(combined.COUNCIL,), daemon=True,
                     name="counterweight-semantic-review-queue").start()
    raise SystemExit(combined.main())
