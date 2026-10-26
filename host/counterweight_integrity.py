"""Fail-closed Counterweight gate worker: deterministic prompt integrity, not AI review."""
import hashlib
import json
import os
from pathlib import Path
from council.runtime import dispatcher_v0_2 as dispatch

def main():
    root=Path(os.environ["POS_ROOT"])
    mission=root/"missions"/os.environ["POS_MISSION_ID"]
    state=dispatch.load(mission)
    if state["status"]!="ACTIVE" or state["stage_index"]!=0:
        raise ValueError("Not at Counterweight gate")
    if os.environ["POS_STAGE"]!="COUNTERBALANCE_PROMPT_GATE":
        raise ValueError("Wrong stage")
    original=state["original_prompt"]
    sha=hashlib.sha256(original.encode("utf-8")).hexdigest()
    if sha!=state["original_prompt_sha256"]:
        raise ValueError("Original instruction altered")
    charter=json.loads((mission/"PROJECT_CHARTER.json").read_text(encoding="utf-8"))
    if charter["owner_request_exact"]!=original:
        raise ValueError("Charter differs from owner instruction")
    # Deterministic checks alone cannot prove intent, ambiguity, or safety.
    # Produce a pending human/independent-model review packet, not VERIFIED.
    packet={
        "stage":"COUNTERBALANCE_PROMPT_GATE",
        "mission_id":state["mission_id"],
        "prompt_sha256":sha,
        "integrity":"PASS",
        "semantic_review":"REQUIRED",
        "owner_release_authorised":False,
        "next_step":"Review owner intent, scope limits, assumptions, risks and corrections; submit separately evidenced Counterweight verdict."
    }
    path=mission/"COUNTERWEIGHT_INTEGRITY.json"
    if not path.exists():
        path.write_text(json.dumps(packet,indent=2)+"\n",encoding="utf-8")
        os.chmod(path,0o600)
    elif json.loads(path.read_text(encoding="utf-8"))!=packet:
        raise ValueError("Existing integrity packet mismatch")
    print(json.dumps({"result":"INTEGRITY_PASS_REVIEW_PENDING","mission_id":state["mission_id"],
                      "owner_gate":"UNCHANGED","semantic_verification":"NOT_ATTESTED"}),flush=True)

if __name__=="__main__":main()
