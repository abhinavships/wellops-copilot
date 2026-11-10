from wellops.memory.constraints import check
from wellops.memory.disputes import propose_correction, resolve_dispute
from wellops.tools.core import get_db


def chat_assertions():
    return get_db().rows("SELECT count(*) n FROM assertions WHERE source='chat'")[0]["n"]


def test_constraints_reject_nonsense_and_explain_computed_fields():
    assert check("wellhead", "22.6") == ("wellhead_temp_c", 22.6, None)
    assert check("wellhead_recorded_c", "3000.6")[2].startswith("Wellhead temperature 3000.6 °C is outside")
    assert "must be a number" in check("delta", "1.2.2")[2] or "not editable" in check("delta", "1.2.2")[2]
    assert "not editable" in check("delta_c", 1.2)[2]
    assert "rule" in check("threshold", "infinity")[2]
    assert "finite" in check("ambient_temp_c", "infinity")[2] or "must be a number" in check("ambient_temp_c", "infinity")[2]


def test_multi_field_edit_without_reason_is_checked_per_field_and_waits(deps, monkeypatch):
    """(feedback_commits off) The user's row edit: 25.6 -> 22.6, 25.6 -> 3000.6, delta 3.2 -> 1.2.2, threshold 15 -> infinity, no reason."""
    from wellops.settings import settings
    monkeypatch.setattr(settings, "feedback_commits", False)
    d = deps("U-201", "multi")
    r = propose_correction(d, "INS-0001-0203", {"wellhead_temp_c": "22.6", "wellhead_recorded_c": "3000.6",
                                                "delta_c": "1.2.2", "threshold": "infinity"}, "this data is wrong")
    st = {c["field"]: c["status"] for c in r["changes"]}
    assert st == {"wellhead_temp_c": "rejected", "delta_c": "computed", "delta_min_c": "computed"} or \
        [c["status"] for c in r["changes"]] == ["pending", "rejected", "computed", "computed"]
    assert chat_assertions() == 0  # nothing stored without a reason or confirmation
    assert {e["label"] for e in r["evidence"]} >= {"Recorded reading", "Rule applied"}
    out = resolve_dispute(d, r["dispute_id"], "correct")  # only the one valid pending change is applied
    assert out["status"] == "corrected" and [c["status"] for c in out["changes"]].count("applied") == 1
    assert chat_assertions() == 1


def test_unjustified_correction_commits_now_by_default(deps):
    """"No, there wasn't a spill": the user's word is stored at once (attributed, undoable), so every later answer uses it."""
    d = deps("U-201", "nospill")
    r = propose_correction(d, "INS-0097-0201", {"spill": False}, "no there wasn't")
    assert r["changes"][0]["status"] == "applied"
    a = get_db().rows("SELECT kind, target_attr, value_num, author_user_id FROM assertions WHERE source='chat'")
    assert a and a[0]["kind"] == "detection_correction" and a[0]["value_num"] == 0.0 and a[0]["author_user_id"] == "U-201"


def test_justified_correction_applies_now_and_shows_conflict(deps):
    """Q12: a justified correction is recorded immediately, original kept, conflicting note surfaced."""
    d = deps("U-201", "q12")
    r = propose_correction(d, "WELL-0164", {"wellhead_temp_c": 41.0},
                           "The handheld thermal gun was faulty; the field crew re-measured the wellhead at 41.0 °C.", "2026-10-15")
    assert r["record"]["id"] == "INS-0164-0205" and r["changes"][0]["status"] == "applied"
    assert any("Pump jack not moving" in str(c) for c in r["conflicts"]) or any("Pump jack not moving" in str(e.get("quote")) for e in r["evidence"])
    assert get_db().rows("SELECT original_num FROM assertions WHERE source='chat'")[0]["original_num"] == 24.0
