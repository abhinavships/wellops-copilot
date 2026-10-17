from wellops import tools as T


def rows(res):
    return T.store().get(res.handle.split("#")[0] and "c1", res.handle).rows


def test_resolve_entity(deps):
    r = T.resolve_entity(deps(), "wells in Northeast Block 8 for the last 4 months, ask Michael")
    ids = {(x["kind"], x["id"]) for x in r.preview}
    assert ("area", "NE-08") in ids and ("user", "U-201") in ids
    dr = next(x for x in r.preview if x["kind"] == "date_range")
    assert (dr["start"], dr["end"]) == ("2026-06-17", "2026-10-17")
    y = T.resolve_entity(deps(), "yesterday")
    assert y.preview[0]["start"] == "2026-10-16"


def test_ne8_not_producing_with_reasons(deps):
    r = T.well_status(deps(), {"kind": "area", "ids": ["Northeast Block 8"]}, only="not_producing")
    assert [x["asset_id"] for x in r.preview] == ["WELL-0164", "WELL-0170", "WELL-0172", "WELL-0175"]
    by = {x["asset_id"]: x for x in r.preview}
    assert by["WELL-0164"]["reason_summary"].startswith("No recorded reason")
    assert by["WELL-0164"]["inspection_note"] == "Pump jack not moving."
    assert by["WELL-0175"]["reason_summary"].startswith("No recorded reason") and by["WELL-0175"]["delta_c"] == 0.4
    assert "MNT-0170-031" in by["WELL-0170"]["reason_summary"] and "MNT-0172-028" in by["WELL-0172"]["reason_summary"]
    assert by["WELL-0170"]["recorded_status"] == "down" and by["WELL-0164"]["recorded_status_stale"]
    assert {f.kind for f in r.dq_flags} >= {"recorded_status_stale", "repair_in_progress"}
    assert r.sql and "ins_eff" in r.sql and r.row_count == 4


def test_well_0001_status_explained_by_note(deps):
    r = T.well_status(deps(), ["WELL-0001"])
    row = r.preview[0]
    assert not row["producing"] and row["delta_c"] == 3.2 and row["recorded_status_stale"]
    assert "CTX-0001-003" in row["reason_summary"]  # CTX-0001-002 is context, not a reason (E12)


def test_downtime_2025_is_21_days(deps):
    r = T.downtime(deps(), ["WELL-0001"], "2025-01-01", "2025-12-31")
    assert r.preview[0]["downtime_days"] == 21 and r.preview[0]["non_producing_dates"].startswith("2025-07-24")
    assert any("downtime_hours" in n for n in r.notes)


def test_downtime_ne7_top3(deps):
    r = T.downtime(deps(), {"kind": "area", "ids": ["Northeast Block 7"]}, "2026-06-16", "2026-10-16", "well", 4)
    assert [x["downtime_days"] for x in r.preview] == [49, 42, 42, 28] and r.preview[0]["asset_id"] == "WELL-0121"


def test_spills_well_0001(deps):
    r = T.spills(deps(), ["WELL-0001"], "2022-10-17")
    assert r.row_count == 6 and len(r.related_handles) == 1
    corr = T.store().get("c1", r.related_handles[0])
    assert [x["inspection_id"] for x in corr.rows] == ["INS-0001-0070"]  # rainwater, CTX-0001-001
    agg = T.spills(deps(), ["WELL-0001"], "2022-10-17", group_by="none")
    assert agg.preview[0] == {"spill_count": 6, "total_volume_bbl": 15.1}
    assert r.preview[-2]["spill_class"] == "reportable" and r.preview[0]["lat"]


def test_spills_heatmap_totals(deps):
    r = T.spills(deps(), None, "2026-01-01", "2026-10-17", group_by="area_month")
    tot = {}
    for x in T.store().get("c1", r.handle).rows:
        tot[x["area"]] = tot.get(x["area"], 0) + x["spill_count"]
    assert tot == {"Southwest Block 23": 35, "Southwest Block 24": 43, "Northeast Block 7": 37, "Northeast Block 8": 26}


def test_obligations_well_0001(deps):
    r = T.obligations(deps(), ["WELL-0001"], "2025-10-17", "2026-10-17")
    ob = {(x["protocol"], x["trigger_ref"]): x for x in T.store().get("c1", r.handle).rows}
    sr1 = ob[("SR-01", "INS-0001-0185")]
    assert sr1["state"] == "breached" and "41 h" in sr1["detail"] and sr1["fine_usd"] == 5000
    assert "REG-0001-002" in sr1["evidence_ids"]
    sr2 = ob[("SR-02", "INS-0001-0185")]
    assert sr2["state"] == "breached" and "MNT-0001-029" in sr2["evidence_ids"]
    sr3 = ob[("SR-03", "INS-0001-0185")]
    assert sr3["due_at"].startswith("2026-08-10") and sr3["state"] != "met" and "open" in sr3["detail"]
    rc4 = ob[("RC-04", "REG-0001-002")]
    assert rc4["state"] == "breached" and rc4["due_at"] == "2026-07-30"
    rca = ob[("Manual §10", "flowline")]
    assert rca["state"] == "pending" and all(m in rca["evidence_ids"] for m in ("MNT-0001-028", "MNT-0001-029", "MNT-0001-032"))
    assert ob[("RC-02", "2026")]["state"] == "met" and "2026-03-07" in ob[("RC-02", "2026")]["detail"]
    s4 = next(x for x in ob.values() if x["protocol"] == "Manual §4")
    assert s4["state"] == "pending" and "CTX-0001-003" in s4["evidence_ids"]
    assert ob[("SR-02", "INS-0001-0201")]["state"] == "met"


def test_sr02_containment_ne_sw24(deps):
    r = T.sql(deps(), """SELECT m.asset_id, f.num FROM report_facts f JOIN maintenance m USING (dataset_id, maintenance_id)
        JOIN inspections i ON i.dataset_id=m.dataset_id AND i.inspection_id=m.triggered_by_inspection_id
        JOIN wells w ON w.dataset_id=m.dataset_id AND w.asset_id=m.asset_id
        WHERE f.dataset_id={ds} AND f.field='containment_hours' AND f.num > 4 AND w.area_id='SW-24'
        AND i.inspected_at::DATE >= DATE '2026-07-19' ORDER BY 1""")
    assert [(x["asset_id"], x["num"]) for x in r.preview] == [("WELL-0082", 7.1), ("WELL-0100", 8.1)]


def test_rca_and_repair_costs(deps):
    r = T.rca_candidates(deps(), {"kind": "area", "ids": ["Northeast Block 7"]}, "pump_jack")
    assert [x["asset_id"] for x in r.preview] == ["WELL-0108", "WELL-0121", "WELL-0138"]
    c = T.repair_costs(deps(), ["WELL-0121", "WELL-0138", "WELL-0107"], "2026-01-01", "2026-10-17")
    assert {x["asset_id"]: x["total_usd"] for x in c.preview} == {"WELL-0121": 25794, "WELL-0138": 46942, "WELL-0107": 5176}


def test_evidence_chain_and_docs(deps):
    r = T.evidence_chain(deps(), "MNT-0001-029")
    kinds = {x["kind"] for x in r.preview + T.store().get("c1", r.handle).rows}
    assert {"inspection", "maintenance", "report_fact", "reg_event"} <= kinds
    assert any(c.span for c in r.citations)
    d = T.read_doc(deps(), "OM-T200", "9.3")
    assert "15" in d.preview[0]["text"]
    assert T.search_docs(deps(), "flowline integrity").row_count >= 1
    assert T.search_reports(deps(), "root cause", "WELL-0001").row_count >= 1


def test_sql_guard(deps):
    for bad in ["DELETE FROM assertions", "SELECT 1; DROP TABLE wells", "SELECT * FROM episodes", "PRAGMA version",
                "COPY wells TO 'x'", "SELECT * FROM read_csv('/etc/passwd')"]:
        try:
            T.sql(deps(), bad)
        except T.ToolError:
            continue
        raise AssertionError(bad)
    r = T.sql(deps(), "SELECT count(*) AS n FROM wells WHERE dataset_id={ds}")
    assert r.preview == [{"n": 200}] and "'sample'" in r.sql
