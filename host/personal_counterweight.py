"""Configure only the deterministic Counterweight integrity stage on the durable host."""
import json
from host import personal_combined
from host import combined

_original=combined.prepare

def configured_prepare():
    path=_original()
    config=json.loads(path.read_text(encoding="utf-8"))
    expected={"argv":["/usr/local/bin/python","-m","host.counterweight_integrity"],
              "idempotent":True,"max_retries":0}
    stages=config.setdefault("stages",{})
    if "COUNTERBALANCE_PROMPT_GATE" in stages and stages["COUNTERBALANCE_PROMPT_GATE"]!=expected:
        raise ValueError("Refusing to overwrite existing Counterweight configuration")
    stages["COUNTERBALANCE_PROMPT_GATE"]=expected
    path.write_text(json.dumps(config,indent=2)+"\n",encoding="utf-8")
    return path

combined.prepare=configured_prepare
if __name__=="__main__":
    raise SystemExit(combined.main())
