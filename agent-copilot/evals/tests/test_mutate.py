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
    assert "FINDING_t500_dropped_from_views_without_rule" in res and res["t500_threshold_25_applied_after_rule"]["ok"]


def test_collision_scenario_is_detected(tmp_path):
    m = make_mutated(tmp_path / "c", seed=3, n_new=4, n_t500=2, n_rename=3, collide=True)
    res = run_checks(tmp_path / "c", m, tmp_path / "c.duckdb")
    assert res["collision_detected_by_check"]["ok"]
    assert all(v["ok"] for v in res.values()), {k: v for k, v in res.items() if not v["ok"]}
