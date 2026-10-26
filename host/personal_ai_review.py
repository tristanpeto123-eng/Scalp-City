"""Project OS Personal host with opt-in model review and credential-isolated children."""
import json
import os
import subprocess
import sys
import threading
import time
from host import combined
from host import personal_recovered  # preserves mission, integrity worker and recovery policy
from host.counterweight_observer import report as integrity_monitor
from host.counterweight_semantic import monitor as evidence_monitor
from host.counterweight_ai_connector import monitor as provider_monitor


def main():
    config = combined.prepare()
    for name, fn, args in (
        ("counterweight-integrity-readonly", integrity_monitor, ()),
        ("counterweight-semantic-intake", evidence_monitor, (combined.COUNCIL,)),
        ("counterweight-provider-connector", provider_monitor, (combined.COUNCIL,)),
    ):
        threading.Thread(target=fn, args=args, name=name, daemon=True).start()

    # Old combined.main inherited all environment variables into each child.
    # Keep identical commands but exclude model credentials and review signing keys.
    child_env = {key: value for key, value in os.environ.items()
                 if not key.startswith("POS_CW_") and key != "OPENAI_API_KEY"}
    commands = [
        [sys.executable, "-m", "council.runtime.mission_supervisor", "--root",
         str(combined.COUNCIL), "serve", "--config", str(config)],
        [sys.executable, "-m", "brain.engine.brain_service"],
    ]
    if os.environ.get("BRAIN_NTFY_TOPIC"):
        commands.append([sys.executable, "-m", "host.notify"])
    children = [subprocess.Popen(cmd, env=child_env, start_new_session=False)
                for cmd in commands]
    print(json.dumps({"event": "PROJECT_OS_AI_HOST_STARTED",
                      "external_ai": "OPT_IN_ONLY",
                      "credential_isolation": "CHILD_ENV_SCRUBBED",
                      "independent_operator": "REQUIRED",
                      "owner_release_authorised": False}), flush=True)
    try:
        while True:
            for proc in children:
                if proc.poll() is not None:
                    print(json.dumps({"event": "COMPONENT_EXITED", "pid": proc.pid,
                                      "exit": proc.returncode}), flush=True)
                    return 1
            time.sleep(2)
    finally:
        for proc in children:
            if proc.poll() is None:
                proc.terminate()
        for proc in children:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
