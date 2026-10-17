"""Ingest a mutated dataset (different dataset_id, temp DB) and check substrate invariants."""
from __future__ import annotations

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

from wellops.substrate import ingest

from evals import CACHE_DIR
from evals.mutate.mutate import T500_RULE, make_mutated

DSM = "mutated"


def _q(con, sql, *a):
    return con.execute(sql, list(a)).fetchall()


def add_t500_rules(con) -> None:
    """Emulates `make rules-extract` for the new manual: rule row (quote verified in the manual) + inheritance from T200."""
    con.execute("INSERT INTO rules VALUES (?,?,?,?,?,?,?,?)", [T500_RULE["param"], "well_type", "T500", T500_RULE["value"],
                                                               "degC", T500_RULE["doc_id"], T500_RULE["section"], T500_RULE["quote"]])
    con.execute("INSERT INTO type_inherits VALUES (?,?,?)", [DSM, "T500", "T200"])
    # a T350-style rule row for params that T500 wells need from T200 is resolved through type_inherits
    for p in ("wellhead_temp_max_c",):
        con.execute("INSERT INTO rules VALUES (?,?,?,?,?,?,?,?)", [p, "well_type", "T500", 105.0, "degC", "OM-T500", "4", "at or below 105 °C"])


def run_checks(data_dir: Path, manifest: dict, db_path: Path | None = None) -> dict:
    db_path = db_path or Path(tempfile.mkdtemp()) / "mut.duckdb"
    con = ingest.connect(db_path)
    counts = ingest.ingest(data_dir, DSM, con)
    con.execute("SET TimeZone='UTC'")
    today = date.fromisoformat(manifest.get("suggested_clock_today", "2026-10-17"))
    asof = f"{today} 23:59:59+00"
    res: dict = {}

    def rec(name, ok, detail=""):
        res[name] = dict(ok=bool(ok), detail=detail)

    # ---- counts consistent with the files
    wells_on_disk = len(list((data_dir / "assets").glob("WELL-*")))
    rec("wells_count_matches_disk", counts["wells"] == wells_on_disk, f"db={counts['wells']} disk={wells_on_disk}")
    n_lines = sum(sum(1 for _ in open(p)) for p in (data_dir / "assets").glob("*/inspections.jsonl"))
    rec("inspection_count_matches_files", counts["inspections"] == n_lines, f"db={counts['inspections']} files={n_lines}")
    with_rf = _q(con, "SELECT count(*) FROM maintenance WHERE dataset_id=? AND report_file IS NOT NULL", DSM)[0][0]
    rec("every_report_file_resolves_after_renames", counts["reports"] == with_rf, f"reports={counts['reports']} report_file rows={with_rf}")
    if manifest["renamed"]:
        ids = [r["maintenance_id"] for r in manifest["renamed"]]
        got = _q(con, "SELECT count(*) FROM reports WHERE dataset_id=? AND maintenance_id IN (SELECT unnest(?))", DSM, ids)[0][0]
        rec("renamed_reports_still_ingested", got == len(ids), f"{got}/{len(ids)}")
    # ---- horizon shift
    mx = _q(con, "SELECT max(inspected_at)::DATE FROM inspections WHERE dataset_id=?", DSM)[0][0]
    exp = date(2026, 10, 15) + timedelta(days=manifest["shift_days"])
    rec("horizon_shifted", mx >= exp, f"max inspection {mx}, expected >= {exp}")
    # ---- new type: silently dropped without a rule, present once rules are extracted
    if manifest["t500_wells"]:
        t5 = manifest["t500_wells"]
        raw = _q(con, "SELECT count(*) FROM inspections WHERE dataset_id=? AND asset_id IN (SELECT unnest(?))", DSM, t5)[0][0]
        vis = _q(con, "SELECT count(*) FROM ins_eff(?, ?::TIMESTAMPTZ) WHERE asset_id IN (SELECT unnest(?))", DSM, asof, t5)[0][0]
        rec("FINDING_t500_dropped_from_views_without_rule", vis == 0 and raw > 0,
            f"raw inspections={raw}, visible in ins_eff before rule extraction={vis} (silent drop; profiler must flag new well types)")
        manual = [r for r in _q(con, "SELECT doc_id FROM docs WHERE dataset_id=? AND doc_id='OM-T500'", DSM)]
        rec("t500_manual_ingested_as_doc", bool(manual))
        add_t500_rules(con)
        vis2 = _q(con, "SELECT count(*), min(delta_min_c), max(delta_min_c) FROM ins_eff(?, ?::TIMESTAMPTZ) WHERE asset_id IN (SELECT unnest(?))",
                  DSM, asof, t5)[0]
        rec("t500_threshold_25_applied_after_rule", vis2[0] == raw and vis2[1] == 25 and vis2[2] == 25, f"rows={vis2[0]} theta=({vis2[1]},{vis2[2]})")
    # ---- invariants
    fleet = _q(con, "SELECT count(*) FROM ins_eff(?, ?::TIMESTAMPTZ) WHERE NOT producing AND inspected_at::DATE >= ?", DSM, asof,
               today - timedelta(days=120))[0][0]
    by_area = _q(con, "SELECT coalesce(sum(n),0) FROM (SELECT count(*) n FROM ins_eff(?, ?::TIMESTAMPTZ) WHERE NOT producing "
                      "AND inspected_at::DATE >= ? GROUP BY area_id)", DSM, asof, today - timedelta(days=120))[0][0]
    rec("sum_area_downtime_equals_fleet", fleet == by_area, f"fleet={7 * fleet}d sum_areas={7 * by_area}d")
    w90 = {r[0] for r in _q(con, "SELECT inspection_id FROM spills_real(?, ?::TIMESTAMPTZ) WHERE inspected_at::DATE > ?", DSM, asof, today - timedelta(days=90))}
    w180 = {r[0] for r in _q(con, "SELECT inspection_id FROM spills_real(?, ?::TIMESTAMPTZ) WHERE inspected_at::DATE > ?", DSM, asof, today - timedelta(days=180))}
    rec("spills_window_subset_of_superwindow", w90 <= w180 and len(w90) <= len(w180), f"{len(w90)} <= {len(w180)}")
    tot = _q(con, "SELECT count(*) FROM spills_real(?, ?::TIMESTAMPTZ)", DSM, asof)[0][0]
    by_a = _q(con, "SELECT coalesce(sum(n),0) FROM (SELECT count(*) n FROM spills_real(?, ?::TIMESTAMPTZ) GROUP BY area_id)", DSM, asof)[0][0]
    rec("spill_counts_consistent_area_vs_fleet", tot == by_a, f"fleet={tot} sum_areas={by_a}")
    real_le_detected = _q(con, "SELECT (SELECT count(*) FROM spills_real(?, ?::TIMESTAMPTZ)) <= (SELECT count(*) FROM inspections WHERE dataset_id=? AND spill_detected)", DSM, asof, DSM)[0][0]
    rec("real_spills_le_detections", real_le_detected)
    ls = _q(con, "SELECT count(*) FROM latest_status(?, ?::TIMESTAMPTZ)", DSM, asof)[0][0]
    vis_wells = _q(con, "SELECT count(DISTINCT asset_id) FROM ins_eff(?, ?::TIMESTAMPTZ)", DSM, asof)[0][0]
    rec("one_latest_status_per_visible_well", ls == vis_wells, f"latest_status={ls} wells_in_views={vis_wells}")
    # ---- id collisions
    dup = _q(con, "SELECT count(*) FROM (SELECT inspection_id FROM inspections WHERE dataset_id=? GROUP BY 1 HAVING count(DISTINCT asset_id)>1)", DSM)[0][0]
    if manifest.get("collide"):
        rec("collision_detected_by_check", dup > 0, f"{dup} inspection_ids shared by >1 well (substrate joins on inspection_id alone: cross-well contamination risk)")
        rows = _q(con, "SELECT count(*) FROM ins_eff(?, ?::TIMESTAMPTZ)", DSM, asof)[0][0]
        raw = _q(con, "SELECT count(*) FROM inspections WHERE dataset_id=? AND inspected_at <= ?::TIMESTAMPTZ", DSM, asof)[0][0]
        rec("FINDING_collision_row_inflation", True, f"ins_eff rows={rows} vs raw={raw} (equal => no join fan-out)")
    else:
        rec("no_duplicate_inspection_ids", dup == 0, f"{dup} duplicates")
    con.close()
    return res


def build_and_check(kind: str = "base", seed: int = 11) -> dict:
    out = CACHE_DIR / "mutated" / kind
    m = make_mutated(out, seed=seed, collide=(kind == "collide"))
    return dict(kind=kind, manifest={k: (v if not isinstance(v, list) else len(v)) for k, v in m.items()},
                checks=run_checks(out, m, CACHE_DIR / "mutated" / f"{kind}.duckdb"))


def main() -> None:
    from evals import RESULTS_DIR
    out = {k: build_and_check(k) for k in ("base", "collide")}
    for k, r in out.items():
        print(f"== {k}")
        for n, c in r["checks"].items():
            print(f"  [{'ok' if c['ok'] else 'FAIL'}] {n}: {c['detail']}")
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "mutated_checks.json").write_text(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
