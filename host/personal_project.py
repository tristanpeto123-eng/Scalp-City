"""Idempotent creation of the owner-approved PERSONAL Project OS mission.

The mission is registered for observation only. No stage reports are forged,
no worker is started, and owner-release flags remain false.
"""
import json
import os
from pathlib import Path
from council.runtime import dispatcher_v0_2 as dispatch

PROJECT = "project-os-personal"
OWNER_REQUEST = (
    "Approve and then deploy project os project. Those pre defined actions "
    "should appear as selectable buttons after each response after completion"
)
BASELINE = "POS-HIERARCHY-v0.2.0:owner-approved-2026-10-17"

CHARTER = {
    "schema": "project-os-personal-charter/1.0",
    "project": PROJECT,
    "approved_on": "2026-10-26",
    "owner_request_exact": OWNER_REQUEST,
    "approved_objective": (
        "Build a reliable personal project-management system that preserves "
        "owner intent, maintains focus, tracks project history, executes "
        "tasks consistently and learns from verified outcomes."
    ),
    "operating_contract": [
        "Capture ideas without automatically changing the locked objective.",
        "Require explicit owner authorisation to change active objectives or detour.",
        "Flag suspected tangents and offer parking them for later.",
        "Use Counterbalance-first and Director/Creative Director specification gates.",
        "Require Checker G0 before execution and G1 evidence before approval.",
        "Preserve timestamped, source-backed decisions, outputs and lessons.",
        "Show actionable, task-relevant response-end buttons where the UI permits.",
        "Do not imply continuous AI workers or cross-chat UI persistence without proof.",
        "Owner alone authorises protected changes, releases and publishing."
    ],
    "acceptance_tests": [
        "A single locked objective can be viewed, continued, paused and closed.",
        "Off-topic requests can be parked without overwriting that objective.",
        "Decisions and changes are recoverable from durable evidence-backed records.",
        "Progress and completion claims identify actual source and reviewer evidence.",
        "Each supported Project OS chat response ends with usable next-action buttons.",
        "One end-to-end task demonstrates PLAN/DO/CHECK/ACT and owner approval."
    ],
    "next_action_labels": ["Continue task", "View status", "Review plan", "Park idea", "Pause task"],
    "deployment_limits": {
        "stage_workers": "NOT_CONFIGURED",
        "deployment": "PRIVATE_HOST_MISSION_ONLY",
        "chat_ui_buttons": "Rendered in ChatGPT response; not forced by host",
        "owner_release_approved": False
    }
}


def ensure_personal_project(council_root):
    """Create once on the persistent volume; fail closed on a corrupt copy."""
    root = Path(council_root)
    missions = root / "missions"
    missions.mkdir(parents=True, exist_ok=True)
    for candidate in sorted(missions.glob("MISSION-*")):
        manifest = candidate / "mission.json"
        if not manifest.is_file():
            continue
        raw = json.loads(manifest.read_text(encoding="utf-8"))
        if raw.get("project") == PROJECT:
            state = dispatch.load(candidate)  # Verifies hash-linked event chain.
            if state.get("original_prompt") != OWNER_REQUEST:
                raise ValueError("Existing personal mission has a different owner prompt")
            return candidate
    mission = dispatch.start(missions, OWNER_REQUEST, PROJECT, BASELINE)
    charter = mission / "PROJECT_CHARTER.json"
    with charter.open("x", encoding="utf-8") as f:
        json.dump(CHARTER, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.chmod(charter, 0o600)
    return mission
