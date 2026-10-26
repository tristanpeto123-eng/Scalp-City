"""Personal host with fail-closed Counterweight integrity worker."""
import json
import sys
from host import combined
from host import personal_combined

original=combined.prepare
def prepare():
    path=original()
    config=json.loads(path.read_text(encoding="utf-8"))
    desired={"argv":[sys.executable,"-m","host.counterweight_integrity"],"idempotent":True,"max_retries":0}
    stages=config.setdefault("stages",{})
    if "COUNTERBALANCE_PROMPT_GATE" in stages and stages["COUNTERBALANCE_PROMPT_GATE"]!=desired:
        raise ValueError("Counterweight worker configuration conflict")
    stages["COUNTERBALANCE_PROMPT_GATE"]=desired
    path.write_text(json.dumps(config,indent=2)+"\n",encoding="utf-8")
    return path
combined.prepare=prepare
if __name__=="__main__":raise SystemExit(combined.main())
