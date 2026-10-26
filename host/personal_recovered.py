"""Counterweight recovery: reconcile ONLY the known module-resolution failure.

This runs on the private host at startup. It does not submit any stage verdict.
"""
import json
import sys
from pathlib import Path
from council.runtime import mission_supervisor as supervisor
from host import combined
from host import personal_combined

original=combined.prepare
MISSION="MISSION-20261026-BE0827D3BD"
def prepare():
    path=original()
    root=combined.COUNCIL
    config=json.loads(path.read_text(encoding="utf-8"))
    desired={"argv":[sys.executable,"-c",
                     "import sys; sys.path.insert(0, '/srv/project-os'); from host.counterweight_integrity import main; main()"],
             "idempotent":True,"max_retries":1}
    stages=config.setdefault("stages",{})
    previous=stages.get("COUNTERBALANCE_PROMPT_GATE")
    if previous not in (None,{"argv":[sys.executable,"-m","host.counterweight_integrity"],
                             "idempotent":True,"max_retries":0},desired):
        raise ValueError("Unknown Counterweight config: manual reconciliation required")
    row=supervisor.status(root)["runs"]
    record=next((x for x in row if x["mission_id"]==MISSION),None)
    if record and record["status"]=="FAILED":
        log=root/"supervisor"/"logs"/(record["run_id"]+".log")
        data=log.read_text(encoding="utf-8",errors="replace") if log.is_file() else ""
        # Do not infer a root cause from an exit code alone.
        if "No module named 'host'" not in data:
            print(json.dumps({"event":"COUNTERWEIGHT_REPAIR_HELD",
                              "reason":"Failure does not match verified module import signature"}),flush=True)
            return path
        stages["COUNTERBALANCE_PROMPT_GATE"]=desired
        path.write_text(json.dumps(config,indent=2)+"\n",encoding="utf-8")
        supervisor.confirmed_retry(root,config,MISSION,acknowledged=True)
        print(json.dumps({"event":"COUNTERWEIGHT_IMPORT_REPAIRED",
                          "mission_id":MISSION,"retry":"EXPLICITLY_RECONCILED",
                          "approval":"UNCHANGED"}),flush=True)
        return path
    if previous is None or previous==desired:
        stages["COUNTERBALANCE_PROMPT_GATE"]=desired
        path.write_text(json.dumps(config,indent=2)+"\n",encoding="utf-8")
    return path

combined.prepare=prepare
if __name__=="__main__":
    from host.counterweight_observer import report
    import threading
    threading.Thread(target=report,daemon=True).start()
    raise SystemExit(combined.main())
