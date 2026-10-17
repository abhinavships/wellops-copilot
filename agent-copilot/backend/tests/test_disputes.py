from wellops.memory.disputes import open_dispute, resolve_dispute
from wellops.tools.core import get_db


def test_dispute_shows_proof_and_changes_nothing_until_confirmed(deps):
    d = deps("U-201", "disp1")
    r = open_dispute(d, "WELL-0001", "producing", "producing", "pump is running")
    assert r["record"]["id"] == "INS-0001-0203" and r["current_value"] is False
    labels = [e["label"] for e in r["evidence"]]
    assert labels[0] == "Recorded reading" and "Rule applied" in labels and "Field note" in labels
    assert get_db().rows("SELECT count(*) n FROM assertions WHERE source='chat'")[0]["n"] == 0  # nothing committed yet
    assert resolve_dispute(d, r["dispute_id"], "keep")["status"] == "kept"
    r2 = open_dispute(d, "INS-0001-0203", "wellhead_temp_c", 41.0)
    out = resolve_dispute(d, r2["dispute_id"], "correct")
    assert out["status"] == "corrected" and out["assertion_id"]
    assert resolve_dispute(d, r2["dispute_id"], "correct")["status"] == "corrected"  # idempotent
