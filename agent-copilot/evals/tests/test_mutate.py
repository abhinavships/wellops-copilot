import pytest

from evals import CACHE_DIR
from evals.mutate.check import run_checks
from evals.mutate.mutate import make_mutated


@pytest.fixture(scope="module")
def base(tmp_path_factory):
    d = tmp_path_factory.mktemp("mut") / "base"
    m = make_mutated(d, seed=3, n_new=6, n_t500=3, n_rename=10)
    return d, m, run_checks(d, m, d.parent / "b.duckdb")


def test_mutation_manifest(base):
    d, m, _ = base
    assert len(m["new_wells"]) == 6 and len(m["t500_wells"]) == 3 and len(m["renamed"]) == 10
    assert (d / "fleet/well-types/T500.md").exists()
    r = m["renamed"][0]
    assert not (d / "assets" / r["maintenance_id"].split("-")[1].join(["WELL-", ""]) / r["old"]).exists()


def test_original_dataset_untouched():
    from evals import SAMPLE_DATA
    assert not (SAMPLE_DATA / "fleet/well-types/T500.md").exists()
    assert not [p for p in SAMPLE_DATA.glob("assets/WELL-*") if int(p.name[5:]) > 200]


def test_mutated_ingests_and_invariants_hold(base):
    _, _, res = base
    failed = {k: v for k, v in res.items() if not v["ok"]}
    assert not failed, failed
    for k in ("t500_visible_in_views", "t500_threshold_25_from_manual", "t500_rule_quote_verbatim_in_manual",
              "t500_inherits_t200", "t500_dq_flag_rules_extracted"):
        assert res[k]["ok"], (k, res[k])


def test_collision_scenario_is_detected(tmp_path):
    m = make_mutated(tmp_path / "c", seed=3, n_new=4, n_t500=2, n_rename=3, collide=True)
    res = run_checks(tmp_path / "c", m, tmp_path / "c.duckdb")
    assert res["collision_detected_by_check"]["ok"] and res["duplicate_inspection_ids_flagged"]["ok"]
    assert all(v["ok"] for v in res.values()), {k: v for k, v in res.items() if not v["ok"]}


def test_type_without_manual_falls_back_and_is_flagged(tmp_path):
    """A new type with no manual must not vanish from the views: T200 thresholds plus a dq flag that says so."""
    from wellops.substrate import ingest
    m = make_mutated(tmp_path / "n", seed=3, n_new=2, n_t500=2, n_rename=1)
    (tmp_path / "n/fleet/well-types/T500.md").unlink()
    con = ingest.connect(tmp_path / "n.duckdb")
    ingest.ingest(tmp_path / "n", "mutated", con)
    q = lambda sql, *a: con.execute(sql, list(a)).fetchall()  # noqa: E731
    raw = q("SELECT count(*) FROM inspections WHERE dataset_id='mutated' AND asset_id IN (SELECT unnest(?))", m["t500_wells"])[0][0]
    vis = q("SELECT count(*), min(delta_min_c), max(delta_min_c) FROM ins_eff('mutated', TIMESTAMPTZ '2026-11-14 23:59:59+00') "
            "WHERE asset_id IN (SELECT unnest(?))", m["t500_wells"])[0]
    assert raw > 0 and vis == (raw, 15.0, 15.0)
    assert q("SELECT kind FROM dq_flags WHERE dataset_id='mutated' AND entity_id='T500'") == [("well_type_without_rule",)]
