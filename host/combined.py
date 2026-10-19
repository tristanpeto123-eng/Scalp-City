"""Run Council supervisor, read-only Brain and owner-directed notification relay."""
from pathlib import Path
import json
import os
import subprocess
import sys
import time
from archivist.engine import codex

ROOT = Path("/state")
COUNCIL = ROOT / "council"
def prepare():
    ROOT.mkdir(parents=True,exist_ok=True)
    for p in (COUNCIL,COUNCIL/"missions",ROOT/"registers",ROOT/"brain"):
        p.mkdir(parents=True,exist_ok=True)
    config = COUNCIL/"supervisor-config.json"
    if not config.exists():
        config.write_text(json.dumps({"stages":{},"poll_seconds":60,"heartbeat_seconds":90,"stage_seconds":1800}),encoding="utf8")
    db=codex.connect(ROOT/"archivist.sqlite3");db.close()
    return config

def main():
    cfg=prepare()
    commands = [
        [sys.executable,"-m","council.runtime.mission_supervisor","--root",str(COUNCIL),"serve","--config",str(cfg)],
        [sys.executable,"-m","brain.engine.brain_service"]
    ]
    if os.getenv("BRAIN_NTFY_TOPIC"):
        commands.append([sys.executable,"-m","host.notify"])
    ps=[subprocess.Popen(c,start_new_session=False) for c in commands]
    print(json.dumps({"event":"PROJECT_OS_HOST_STARTED","council_missions":"UNKNOWN_UNTIL_CONNECTED",
                      "workers":"NOT_CONFIGURED","brain_source":"local volume only",
                      "notification_relay":"ENABLED" if len(commands)==3 else "DISABLED"}),flush=True)
    try:
        while True:
            for p in ps:
                if p.poll() is not None:
                    print(json.dumps({"event":"COMPONENT_EXITED","pid":p.pid,"exit":p.returncode}),flush=True)
                    return 1
            time.sleep(2)
    finally:
        for p in ps:
            if p.poll() is None:p.terminate()
        for p in ps:
            try:p.wait(timeout=5)
            except subprocess.TimeoutExpired:p.kill()

if __name__=="__main__":raise SystemExit(main())
