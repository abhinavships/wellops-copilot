"""L0: the substrate alone (no LLM) must reproduce the answer key where the key is verifiable.

These pin semantics, not prompts. If a test disagrees with the key, triage per DATA.md §3 before
"fixing" anything: it may be a key error.
"""

import re
from datetime import date, timedelta

import duckdb
import pytest
import yaml

from wellops.settings import settings
from wellops.substrate import ingest

DS = "sample"
NOW = "2026-10-17 23:59:59+00"


@pytest.fixture(scope="session")
def con(tmp_path_factory):
    c = ingest.connect(tmp_path_factory.mktemp("db") / "t.duckdb")
    ingest.ingest(settings.data_dir, DS, c)
    return c


def q(con, sql, *args):
    return con.execute(sql, list(args)).fetchall()


def area(con, name):
    return q(con, "SELECT area_id FROM areas WHERE dataset_id=? AND name=?", DS, name)[0][0]


def test_rule_quotes_exist_verbatim():
    cfg = yaml.safe_load(open(ingest.HERE / "rules.yaml"))
    norm = lambda s: re.sub(r"\s+", " ", s.replace("**", "")).strip()
    for r in cfg["rules"]:
        doc = norm((settings.data_dir / cfg["docs"][r["doc_id"]]).read_text())
        assert norm(r["quote"]) in doc, r


def test_q1_well_0001_not_producing_status_stale(con):
    (row,) = q(con, "SELECT producing, round(delta_c,1), recorded_status, recorded_status_stale, inspection_id "
                    "FROM latest_status(?, ?::TIMESTAMPTZ) WHERE asset_id='WELL-0001'", DS, NOW)
    assert row == (False, 3.2, "active", True, "INS-0001-0203")
    notes = q(con, "SELECT ref FROM context_for(?, ?::TIMESTAMPTZ) WHERE asset_id='WELL-0001' "
                   "AND valid_from <= DATE '2026-10-15' AND coalesce(valid_to, DATE '9999-01-01') >= DATE '2026-10-15'",
              DS, NOW)
    assert ("CTX-0001-003",) in notes


def test_q2_ne8_not_producing(con):
    rows = q(con, "SELECT asset_id FROM latest_status(?, ?::TIMESTAMPTZ) WHERE area_id=? AND NOT producing "
                  "ORDER BY 1", DS, NOW, area(con, "Northeast Block 8"))
    assert [r[0] for r in rows] == ["WELL-0164", "WELL-0170", "WELL-0172", "WELL-0175"]


def test_q4_spills_well_0001(con):
    (n, v) = q(con, "SELECT count(*), round(sum(volume_eff),1) FROM spills_real(?, ?::TIMESTAMPTZ) "
                    "WHERE asset_id='WELL-0001'", DS, NOW)[0]
    assert (n, v) == (6, 15.1)


def test_q5_downtime_2025_uses_delta_not_absolute(con):
    rows = q(con, "SELECT inspected_at::DATE FROM ins_eff(?, ?::TIMESTAMPTZ) WHERE asset_id='WELL-0001' "
                  "AND year(inspected_at)=2025 AND NOT producing ORDER BY 1", DS, NOW)
    assert 7 * len(rows) == 21
    assert rows[0][0] == date(2025, 7, 24)  # 51.6 °C wellhead, still not producing


def test_q6_ne7_top3_downtime(con):
    rows = q(con, "SELECT asset_id, 7*count(*) d FROM ins_eff(?, ?::TIMESTAMPTZ) WHERE area_id=? AND NOT producing "
                  "AND inspected_at::DATE >= ? GROUP BY 1 ORDER BY d DESC, asset_id LIMIT 4",
             DS, NOW, area(con, "Northeast Block 7"), date(2026, 6, 16))
    assert [r[1] for r in rows] == [49, 42, 42, 28] and rows[0][0] == "WELL-0121"


def test_q8_heatmap_totals(con):
    rows = dict(q(con, "SELECT a.name, count(*) FROM spills_real(?, ?::TIMESTAMPTZ) s JOIN areas a "
                       "ON a.dataset_id=? AND a.area_id=s.area_id WHERE year(inspected_at)=2026 GROUP BY 1",
                  DS, NOW, DS))
    assert rows == {"Southwest Block 23": 35, "Southwest Block 24": 43, "Northeast Block 7": 37,
                    "Northeast Block 8": 26}


def test_q10_containment_only_in_reports(con):
    rows = q(con, """SELECT m.asset_id, f.num FROM report_facts f JOIN maintenance m USING (dataset_id, maintenance_id)
                     JOIN inspections i ON i.dataset_id=m.dataset_id AND i.inspection_id=m.triggered_by_inspection_id
                     JOIN wells w ON w.dataset_id=m.dataset_id AND w.asset_id=m.asset_id
                     WHERE f.dataset_id=? AND f.field='containment_hours' AND f.num > 4 AND w.area_id='SW-24'
                       AND i.inspected_at::DATE >= DATE '2026-10-17' - INTERVAL 90 DAY ORDER BY 1""", DS)
    assert rows == [("WELL-0082", 7.1), ("WELL-0100", 8.1)]


def test_q14_rca_scope_override_hits_t350_only(con):
    sql = """SELECT m.asset_id FROM repairs(?, ?::TIMESTAMPTZ) m JOIN wells w USING (dataset_id, asset_id)
             JOIN params(?, ?::TIMESTAMPTZ) p ON p.asset_id=m.asset_id AND p.param='rca_corrective_count_min'
             WHERE w.area_id=? AND m.maintenance_type='corrective' AND m.equipment_kind='pump_jack'
               AND m.started_at > TIMESTAMPTZ '2025-10-17 23:59:59+00'
             GROUP BY m.asset_id, p.value HAVING count(*) >= p.value ORDER BY 1"""
    ne7 = area(con, "Northeast Block 7")
    assert [r[0] for r in q(con, sql, DS, NOW, DS, NOW, ne7)] == ["WELL-0108", "WELL-0121", "WELL-0138"]
    con.execute("""INSERT INTO assertions VALUES ('t-rule', ?, 'rule_override', 'well_type', 'T350',
                   'rca_corrective_count_min', 2, NULL, 3, 'U-201', 'team rule', 'chat', 'test', NULL, NULL,
                   TIMESTAMPTZ '2026-10-17 10:00:00+00', 'confirmed', 'fleet', NULL)""", [DS])
    try:
        got = [r[0] for r in q(con, sql, DS, NOW, DS, NOW, ne7)]
        assert got == ["WELL-0108", "WELL-0120", "WELL-0121", "WELL-0138"]  # WELL-0102 (T200) must not appear
    finally:
        con.execute("DELETE FROM assertions WHERE assertion_id='t-rule'")


def test_q12_value_correction_overlay_keeps_original(con):
    con.execute("""INSERT INTO assertions VALUES ('t-corr', ?, 'value_correction', 'inspection', 'INS-0164-0205',
                   'wellhead_temp_c', 41.0, NULL, 24.0, 'U-201', 'faulty thermal gun', 'chat', 'test', NULL, NULL,
                   TIMESTAMPTZ '2026-10-17 10:00:00+00', 'confirmed', 'fleet', NULL)""", [DS])
    try:
        (row,) = q(con, "SELECT wellhead_temp_c, wellhead_eff, round(delta_c,1), producing FROM ins_eff(?, ?::TIMESTAMPTZ) "
                        "WHERE inspection_id='INS-0164-0205'", DS, NOW)
        assert row == (24.0, 41.0, 21.1, True)
        # before the correction was made, the as-of view still shows the original
        (old,) = q(con, "SELECT producing FROM ins_eff(?, TIMESTAMPTZ '2026-10-16 12:00:00+00') "
                        "WHERE inspection_id='INS-0164-0205'", DS)
        assert old == (False,)
    finally:
        con.execute("DELETE FROM assertions WHERE assertion_id='t-corr'")


def test_new_manual_rules_are_extracted_verbatim():
    from wellops.settings import settings
    from wellops.substrate.ingest import manual_rules, split_sections
    rules, parent = manual_rules("T350", split_sections((settings.data_dir / "fleet/well-types/T350.md").read_text()))
    got = {r["param"]: (r["value"], r["section"], r["quote"]) for r in rules}
    assert got["production_delta_min_c"] == (20.0, "3", "at least 20 °C above ambient")  # same as rules.yaml
    assert got["wellhead_temp_max_c"] == (95.0, "4", "at or below 95 °C") and parent == "T200"
    md = ("# T500 manual\n\n## 1. Scope\n\nProcedures not listed here follow the T200 manual.\n\n## 3. Production\n\n"
          "A T500 well is **producing (active)** only when its wellhead is at least **25 °C above\nambient** air.\n")
    rules, parent = manual_rules("T500", split_sections(md))
    assert [(r["param"], r["value"], r["section"]) for r in rules] == [("production_delta_min_c", 25.0, "3")] and parent == "T200"
