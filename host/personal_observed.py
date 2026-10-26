"""Personal host with safe Counterweight observability; no gate mutation."""
import threading
from host import combined
from host import personal_counterweight_v2
from host.counterweight_observer import report

if __name__=="__main__":
    threading.Thread(target=report,daemon=True,name="counterweight-readonly-observer").start()
    raise SystemExit(combined.main())
