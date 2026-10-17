from datetime import datetime, timezone

from wellops import tools as T
from wellops.contracts import AssertionIn
from wellops.memory import assert_fact, list_conflicts, recall_episodes, record_episode, undo_assertion


def ne8_not_producing(deps, who="U-201"):
    r = T.well_status(deps(who), {"kind": "area", "ids": ["NE-8"]}, only="not_producing")
    return [x["asset_id"] for x in r.preview], r


def test_value_correction_makes_well_producing_and_conflicts(deps):
    out = assert_fact(deps(), AssertionIn(kind="value_correction", scope_level="inspection", scope_key="INS-0164-0205",
                                          target_attr="wellhead_temp_c", value_num=41.0,
                                          reason="faulty thermal gun; re-measured"))
    assert out.status == "confirmed" and out.original_num == 24.0 and out.author_user_id == "U-201"
    assert any("WELL-0164 now producing" in e and "21.1" in e for e in out.effects)
    (c,) = out.conflicts
    says = [s["says"] for s in c.sides]
    assert "Pump jack not moving." in says
    assert any(s["author"] == "Tyler Hughes" for s in c.sides) and c.ask
    wells, r = ne8_not_producing(deps)
    assert wells == ["WELL-0170", "WELL-0172", "WELL-0175"]
    # another user sees the correction, attributed, and the disagreeing note
    r2 = T.well_status(deps("U-202", "c2"), ["WELL-0164"])
    row = r2.preview[0]
    assert row["producing"] and row["wellhead_overlay"] == out.assertion_id and row["wellhead_recorded_c"] == 24.0
    assert out.assertion_id in r2.applied_overlays and r2.conflicts and r2.conflicts[0].subject.startswith("INS-0164-0205")
    # undo = supersede
    undo_assertion(deps(), out.assertion_id)
    assert ne8_not_producing(deps)[0][0] == "WELL-0164"


def test_volume_correction_flips_to_reportable(deps):
    out = assert_fact(deps(), AssertionIn(kind="analysis_correction", scope_level="inspection", scope_key="INS-0151-0201",
                                          target_attr="spill_est_volume_bbl", value_num=5.4, reason="field crew measured"))
    assert out.original_num == 4.5
    txt = " | ".join(out.effects)
    assert "moderate -> reportable" in txt
    assert "SR-01 breached" in txt and "no written notice" in txt
    assert "SR-03 pending: due 2026-11-23" in txt
    sp = T.spills(deps("U-401", "c9"), {"kind": "area", "ids": ["NE-8"]}, "2026-07-19", "2026-10-17")
    rep = [x["asset_id"] for x in sp.preview if x["reportable"]]
    assert rep == ["WELL-0151"] and out.assertion_id in sp.applied_overlays


def test_t350_rule_override_scope(deps):
    ne7 = {"kind": "area", "ids": ["NE-7"]}
    before = [x["asset_id"] for x in T.rca_candidates(deps(), ne7, "pump_jack").preview]
    assert before == ["WELL-0108", "WELL-0121", "WELL-0138"]
    out = assert_fact(deps(), AssertionIn(kind="rule_override", scope_level="well_type", scope_key="T350",
                                          target_attr="rca_corrective_count_min", value_num=2, reason="team rule"))
    assert out.original_num == 3 and any("WELL-0120" in e for e in out.effects)
    r = T.rca_candidates(deps("U-201", "new-chat"), ne7, "pump_jack", include_below=True)
    got = [x["asset_id"] for x in r.preview if x["meets_threshold"]]
    assert got == ["WELL-0108", "WELL-0120", "WELL-0121", "WELL-0138"]
    assert "WELL-0102" not in got and out.assertion_id in r.applied_overlays
    below = [x for x in r.preview if x["asset_id"] == "WELL-0102"]
    assert below and not below[0]["meets_threshold"]  # T200 well with 2 repairs stays below its threshold of 3


def test_claim_unconfirmed_with_evidence(deps):
    ok = assert_fact(deps(), AssertionIn(kind="status_claim", scope_level="well", scope_key="WELL-0175",
                                         value_text="WELL-0175 has been shut in for planned work since 14 Oct",
                                         valid_from=None))
    assert ok.status == "confirmed" and not ok.conflicts
    bad = assert_fact(deps("U-204"), AssertionIn(kind="status_claim", scope_level="well", scope_key="WELL-0175",
                                                  value_text="WELL-0175 is running fine"))
    assert bad.status == "unconfirmed"
    (c,) = bad.conflicts
    assert c.resolution == "claim_unconfirmed" and "0.4" in c.sides[0]["says"] and c.sides[0]["ref"] == "INS-0175-0204"
    assert c.sides[1]["author"] == "Tyler Hughes"
    assert ne8_not_producing(deps)[0].count("WELL-0175") == 1  # the claim never changes the sensor state


def test_planned_downtime_shared(deps):
    assert_fact(deps("U-102"), AssertionIn(kind="planned_downtime", scope_level="well", scope_key="WELL-0007",
                                           value_text="shut in for a 10-day planned workover", valid_from="2026-10-17",
                                           valid_to="2026-10-27"))
    r = T.sql(deps("U-201", "x"), "SELECT ref, kind, author_user_id, text, valid_from, valid_to FROM context_for({ds}, {asof}) "
                                 "WHERE asset_id='WELL-0007' AND source='chat'")
    assert r.preview[0]["author_user_id"] == "U-102" and r.preview[0]["valid_from"] == "2026-10-17"


def test_disagreeing_notes_detection_stands(deps):
    cs = list_conflicts(deps(), "INS-0101-0197")
    (c,) = [c for c in cs if "spill detection" in c.subject]
    refs = {s["ref"] for s in c.sides}
    assert refs == {"CTX-0101-002", "CTX-0101-003"} and c.resolution == "data_supports_a"
    sp = T.spills(deps(), ["WELL-0101"], "2026-08-01", "2026-08-31")
    assert [x["inspection_id"] for x in sp.preview] == ["INS-0101-0197"]
    ec = T.evidence_chain(deps(), "CTX-0101-002")
    assert ec.conflicts and any(r["id"] == "MNT-0101-029" for r in T.store().get("c1", ec.handle).rows)


def test_principal_isolation_and_time_scoring(deps):
    d1 = deps("U-201", "day1")
    when = datetime(2026, 10, 16, 10, 0, tzinfo=timezone.utc)
    record_episode(d1, "Which 3 wells in NE-7 had the most downtime in the last 4 months?",
                   ["WELL-0121", "WELL-0138", "WELL-0107"], ["r1"], "top-3 downtime wells in NE-7", ts=when)
    record_episode(d1, "status of WELL-0001", ["WELL-0001"], ["r2"], "status", ts=when)
    r = recall_episodes(deps("U-201", "day2"), "the three wells we discussed yesterday")
    assert r.preview[0]["entities"] == ["WELL-0121", "WELL-0138", "WELL-0107"]
    leak = recall_episodes(deps("U-202", "other"), "the three wells we discussed yesterday")
    assert leak.row_count == 0 and any("no matching" in n for n in leak.notes)
    none = recall_episodes(deps("U-201", "day2"), "the wells from last week")
    assert none.row_count == 0 or all(str(e["ts"])[:10] >= "2026-10-10" for e in none.preview)
