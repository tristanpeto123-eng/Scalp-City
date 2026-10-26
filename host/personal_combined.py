"""Start the existing combined host with an idempotent personal project seed."""
import json
from host import combined
from host.personal_project import ensure_personal_project

_original_prepare = combined.prepare


def prepare_with_personal_project():
    config = _original_prepare()
    mission = ensure_personal_project(combined.COUNCIL)
    print(json.dumps({"event": "PERSONAL_MISSION_REGISTERED", "mission_id": mission.name,
                      "expected_stage": "COUNTERBALANCE_PROMPT_GATE",
                      "stage_workers": "NOT_CONFIGURED"}), flush=True)
    return config


combined.prepare = prepare_with_personal_project

if __name__ == "__main__":
    raise SystemExit(combined.main())
