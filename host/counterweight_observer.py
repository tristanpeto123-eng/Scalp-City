"""Read-only, allowlisted Counterweight status telemetry for private Railway logs.

Never reports raw owner prompts, worker stdout, tokens, file contents, or approval.
"""
import hashlib
import json
import sqlite3
import time
from pathlib import Path
from council.runtime import dispatcher_v0_2 as dispatch

ROOT=Path("/state/council")
MISSION="MISSION-20261026-BE0827D3BD"
def snapshot():
    mission=ROOT/"missions"/MISSION
    state=dispatch.load(mission)
    out={"event":"COUNTERWEIGHT_READONLY_STATUS","mission_id":MISSION,
         "stage":dispatch.STAGES[state["stage_index"]][0],
         "mission_status":state["status"],
         "owner_approval":state["owner_approval_granted"],
         "release_authorised":state["release_authorised"],
         "integrity_packet":"ABSENT","worker_result":"UNCONFIRMED"}
    db=ROOT/"supervisor"/"state.sqlite3"
    if db.is_file():
        con=sqlite3.connect("file:"+str(db)+"?mode=ro",uri=True,timeout=2)
        try:
            row=con.execute("SELECT status,exit_code FROM runs WHERE mission_id=?",(MISSION,)).fetchone()
            if row:
                out["supervisor_status"]=row[0]
                out["worker_exit_code"]=row[1]
        finally:con.close()
    packet=mission/"COUNTERWEIGHT_INTEGRITY.json"
    if packet.is_file():
        p=json.loads(packet.read_text(encoding="utf-8"))
        expected=hashlib.sha256(state["original_prompt"].encode("utf-8")).hexdigest()
        if (p.get("mission_id")==MISSION and p.get("prompt_sha256")==expected
            and p.get("integrity")=="PASS" and p.get("semantic_review")=="REQUIRED"
            and p.get("owner_release_authorised") is False):
            out["integrity_packet"]="VALID"
            out["worker_result"]="INTEGRITY_PASS_REVIEW_PENDING"
        else:
            out["integrity_packet"]="INVALID"
            out["worker_result"]="BLOCKED"
    return out

def report():
    previous=None
    while True:
        try:
            state=snapshot()
            key=json.dumps(state,sort_keys=True)
            if key!=previous:
                print(key,flush=True)
                previous=key
        except Exception as e:
            print(json.dumps({"event":"COUNTERWEIGHT_READONLY_STATUS","worker_result":"UNVERIFIED",
                              "error_type":type(e).__name__}),flush=True)
        time.sleep(15)
