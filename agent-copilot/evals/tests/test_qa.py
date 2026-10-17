"""QA layer: results store, failure taxonomy, regression gate and explorer API."""
import copy
import json

import pytest

from evals.cases import load_cases
from evals.metrics.scorers import aggregate, score_turn
from evals.qa import gate, store
from evals.qa.taxonomy import CATEGORIES, classify_turn, expected_window, ungrounded_numbers, window_mismatch
from evals.runner.episode import run_case
from evals.runner.faults import FAULTS
from evals.runner.targets import FakeTarget

# fault injected into a correct answer -> the category the taxonomy must put the failed turn in
FAULT_CATEGORY = {
    "swap_number": "wrong_number", "drop_set_member": "missing_entity", "drop_date": "missing_entity",
    "drop_conflict": "conflict_not_shown", "leak_other_user_data": "memory_leak", "wrong_visual": "wrong_visual",
    "drop_citation": "missing_evidence", "cite_unrelated_well": "missing_evidence",
    "drop_overlay_disclosure": "correction_not_disclosed", "drop_unconfirmed": "conflict_not_shown",
    "drop_no_prior": "invented_history", "drop_memory_write": "correction_not_applied",
    "wrong_memory_value": "correction_not_applied", "use_downtime_hours": "trajectory", "too_many_steps": "trajectory",
}


@pytest.fixture(scope="module")
def golden():
    return load_cases("golden")


@pytest.fixture(scope="module")
def fake_results(golden):
    t = FakeTarget()
    return [run_case(c, t) for c in golden]


def _turn(answer, trace, t, err=None):
    sc = score_turn(answer, t.get("expect") or {}, trace)
    return dict(turn_id=t.get("id", "t1"), user=t["user"], chat=t["chat"], clock=t["clock"], say=t["say"], setup=False,
                expect=t.get("expect") or {}, truth=t.get("truth"), answer=answer, trace=trace, latency_s=1.0, error=err,
                scores={k: dict(value=v.value, reason=v.reason, details=v.details) for k, v in sc.items()},
                agg=dict(score=0.0, passed=False, leak=False) if err else aggregate(sc))


def _write_run(tmp_path, run_id, results, args=None, meta=None):
    d = tmp_path / run_id
    d.mkdir()
    (d / "results.json").write_text(json.dumps(dict(run_id=run_id, args=args or dict(target=run_id.split("-")[2], suite="golden", k=1),
                                                    meta=meta or {}, summary={}, results=results), default=str))
    return d


def _degrade(results, case_id, score=0.2):
    out = copy.deepcopy(results)
    for r in out:
        if r["case_id"] == case_id:
            r["score"], r["passed"] = score, False
    return out


# ---------------------------------------------------------------- taxonomy
def test_categories_are_unique_and_include_the_required_ones():
    keys = [c.key for c in CATEGORIES]
    assert len(keys) == len(set(keys))
    assert {"wrong_number", "missing_entity", "wrong_window", "ungrounded_claim", "missing_evidence", "wrong_visual",
            "memory_leak", "correction_not_applied", "correction_not_disclosed", "conflict_not_shown", "run_error"} <= set(keys)


def test_clean_turns_have_no_failure_category(fake_results):
    for r in fake_results:
        for t in r["turns"]:
            assert classify_turn(t).primary is None, (r["case_id"], t["turn_id"])


def test_every_injected_fault_lands_in_its_category(golden):
    """Meta-eval for the taxonomy: the same 15 faults the scorer meta-eval injects must be classified correctly."""
    target, seen = FakeTarget(), {f: 0 for f in FAULTS}
    for c in golden:
        for t in c["turns"]:
            if t.get("setup"):
                continue
            ans, trace = target.respond(c, t)
            for fname, (_, inj) in FAULTS.items():
                res = inj(copy.deepcopy(ans), copy.deepcopy(trace), t.get("expect") or {})
                if res is None:
                    continue
                turn = _turn(*res, t)
                if turn["agg"]["passed"]:
                    continue  # the fault did not apply to anything this turn scores
                cls = classify_turn(turn)
                assert FAULT_CATEGORY[fname] in cls.categories, (c["id"], fname, cls.categories)
                seen[fname] += 1
    assert all(seen.values()), seen


def test_leak_is_primary_over_content_failures(golden):
    c = next(c for c in golden if any((t.get("expect") or {}).get("must_not_contain") for t in c["turns"]))
    t = next(t for t in c["turns"] if (t.get("expect") or {}).get("must_not_contain"))
    ans, trace = FakeTarget().respond(c, t)
    ans["markdown"] += " " + t["expect"]["must_not_contain"][0]
    ans["visuals"] = [dict(type="heatmap")]
    assert classify_turn(_turn(ans, trace, t)).primary == "memory_leak"


def test_run_error_and_timeout():
    t = dict(user="U", chat="c", clock="2026-10-17", say="q", expect={})
    turn = _turn({"markdown": ""}, [], t, err="ReadTimeout: timed out\nTraceback ...")
    cls = classify_turn(turn)
    assert cls.primary == "run_error" and cls.hits[0].evidence["timeout"] is True
    turn = _turn({"markdown": ""}, [], t, err="RuntimeError: RUN_ERROR: Exceeded maximum output retries (2)")
    assert classify_turn(turn).categories[:2] == ["run_error", "ungrounded_claim"]


def test_wrong_window_from_trace_args():
    truth = dict(fn="top_downtime", args=dict(area="Northeast Block 7", k=3, since="2026-06-16", until="2026-10-16"))
    ok = dict(clock="2026-10-17", trace=[dict(name="downtime", args=dict(start="2026-06-17", end="2026-10-17"))])
    bad = dict(clock="2026-10-17", trace=[dict(name="downtime", args=json.dumps(dict(start="2026-07-17", end="2026-10-17")))])
    assert window_mismatch(ok, truth) is None
    ev = window_mismatch(bad, truth)
    assert ev and ev["expected"][0] == "2026-06-16" and ev["called"][0]["start"] == "2026-07-17"
    # "last 12 months" and a calendar year that is still running
    assert expected_window(dict(args=dict(months=12)), "2026-10-17")[0].isoformat() == "2025-10-17"
    cy = dict(clock="2026-10-17", trace=[dict(name="spills", args=dict(start="2026-01-01", end="2026-12-31"))])
    assert window_mismatch(cy, dict(args=dict(year=2026))) is None
    # tools without a window and no truth window: no judgement
    assert window_mismatch(dict(clock="2026-10-17", trace=[dict(name="well_status", args={})]), truth) is None
    assert window_mismatch(ok, dict(args=dict(well="WELL-0001"))) is None


def test_ungrounded_numbers_need_real_payloads():
    payload = json.dumps(dict(rows=[dict(asset_id="WELL-0175", well_name="NE-08 Well 175", delta_c=0.4, threshold_c=15.0)],
                              notes=["x" * 200]))
    t = dict(say="which wells?", expect={}, trace=[dict(name="well_status", result=payload)],
             answer=dict(markdown="NE-08 Well 175 is at 0.4 °C vs 15.0 °C; total 812.5 bbl."))
    assert ungrounded_numbers(t) == [812.5]
    t["trace"] = [dict(name="well_status", result=payload + " " * 2000)]  # truncated stream: not judged
    assert ungrounded_numbers(t) == []
    t["trace"] = [dict(name="sql", result=dict(row_count=3))]  # fake-target trace: not judged
    assert ungrounded_numbers(t) == []


# ---------------------------------------------------------------- store
def test_store_ingests_results_and_partial_runs(tmp_path, fake_results):
    con = store.connect(tmp_path / "qa.sqlite")
    _write_run(tmp_path, "20261017-120000-fake-golden", fake_results, meta=dict(git_sha="abc1234", model="m1"))
    p = tmp_path / "20261017-130000-api-golden"
    p.mkdir()
    bad = _degrade(fake_results, "Q06")
    with (p / "partial.jsonl").open("w") as f:
        for r in bad[:3]:
            f.write(json.dumps(r) + "\n")
        f.write('{"case_id": "Q0')  # half-written last line of a live run
    assert sorted(store.import_paths(con, [tmp_path])) == ["20261017-120000-fake-golden", "20261017-130000-api-golden"]
    assert store.import_paths(con, [tmp_path]) == []  # unchanged fingerprints: idempotent
    runs = {r["run_id"]: r for r in store.list_runs(con)}
    full, part = runs["20261017-120000-fake-golden"], runs["20261017-130000-api-golden"]
    assert full["n_cases"] == 18 and full["pass_rate"] == 1.0 and full["git_sha"] == "abc1234" and not full["git_sha_inferred"]
    assert full["model"] == "m1" and full["data_version"] == "sample" and full["per_scorer"]["facts"] == 1.0
    assert part["partial"] == 1 and part["target"] == "api" and part["n_cases"] == 3
    n_turns = sum(len(r["turns"]) for r in fake_results)
    assert con.execute("SELECT count(*) FROM turns WHERE run_id = ?", (full["run_id"],)).fetchone()[0] == n_turns
    assert con.execute("SELECT count(DISTINCT scorer) FROM scores").fetchone()[0] == 10
    d = store.case_detail(con, full["run_id"], "Q11")
    assert len(d["runs"][0]["turns"]) == 2 and "facts" in d["runs"][0]["turns"][0]["scores"]
    assert store.resolve_run(con, "20261017-12") == full["run_id"] and store.resolve_run(con, "latest:api") == part["run_id"]
    hist = store.case_history(con, "Q01")
    assert [h["run_id"] for h in hist] == [full["run_id"], part["run_id"]]
    # re-import after the source changed replaces the run instead of duplicating it
    with (p / "partial.jsonl").open("w") as f:
        f.write("\n".join(json.dumps(r) for r in bad) + "\n")
    assert store.import_paths(con, [tmp_path]) == [part["run_id"]]
    assert con.execute("SELECT count(*) FROM case_results WHERE run_id = ?", (part["run_id"],)).fetchone()[0] == 18


def test_store_records_failure_category_and_taxonomy_counts(tmp_path, golden):
    q6 = next(c for c in golden if c["id"] == "Q06")
    t = q6["turns"][0]
    ans, trace = FakeTarget().respond(q6, t)
    ans["visuals"] = [dict(type="map")]
    res = [dict(case_id="Q06", suite="golden", title=q6["title"], run=0, turns=[_turn(ans, trace, t)], score=0.75,
                passed=False, leak=False)]
    con = store.connect(tmp_path / "qa.sqlite")
    store.ingest(con, store.load_run_dir(_write_run(tmp_path, "20261017-140000-api-golden", res)))
    row = con.execute("SELECT failure_category, categories_json FROM case_results").fetchone()
    assert row["failure_category"] == "wrong_visual" and json.loads(row["categories_json"]) == ["wrong_visual"]
    tc = store.taxonomy_counts(con)
    assert tc["failed"] == 1 and tc["primary"] == {"wrong_visual": 1}
    assert store.taxonomy_counts(con, level="turn")["any"] == {"wrong_visual": 1}


# ---------------------------------------------------------------- gate
@pytest.fixture()
def gate_db(tmp_path, fake_results):
    con = store.connect(tmp_path / "qa.sqlite")
    for rid, res in [("20261017-100000-fake-golden", fake_results),
                     ("20261017-110000-fake-golden", copy.deepcopy(fake_results)),
                     ("20261017-120000-api-golden", _degrade(fake_results, "Q06")),
                     ("20261017-130000-api-golden", _degrade(fake_results, "Q06", score=0.97))]:
        store.ingest(con, store.load_run_dir(_write_run(tmp_path, rid, res)))
    return con, tmp_path


def test_gate_passes_identical_runs(gate_db):
    con, _ = gate_db
    g = gate.compare(con, "20261017-100000-fake-golden", "20261017-110000-fake-golden")
    assert not g.regressed and {d.status for d in g.cases} == {"unchanged"} and len(g.cases) == 18


def test_gate_flags_a_case_regression_with_reasons(gate_db):
    con, _ = gate_db
    g = gate.compare(con, "20261017-100000-fake-golden", "20261017-120000-api-golden")
    assert g.regressed
    reg = [d for d in g.cases if d.status == "regressed"]
    assert [d.case_id for d in reg] == ["Q06"] and "score 1.000 -> 0.200" in reg[0].reasons[0]
    assert not g.aggregate["mean_score"]["regressed"]  # -0.044 aggregate is inside the margin; the case still fails it
    # the reverse direction is an improvement, not a regression
    back = gate.compare(con, "20261017-120000-api-golden", "20261017-100000-fake-golden")
    assert not back.regressed and [d.case_id for d in back.cases if d.status == "improved"] == ["Q06"]


def test_gate_margin_absorbs_score_noise_but_not_pass_to_fail(gate_db):
    con, _ = gate_db
    g = gate.compare(con, "20261017-100000-fake-golden", "20261017-130000-api-golden", margin=0.05)
    d = next(d for d in g.cases if d.case_id == "Q06")
    assert d.reasons == ["pass rate 1.00 -> 0.00"]  # score -0.03 is noise; a pass turning into a fail is not
    g2 = gate.compare(con, "20261017-110000-fake-golden", "20261017-100000-fake-golden", margin=0.0)
    assert not g2.regressed


def test_gate_noise_threshold_widens_with_k(tmp_path, fake_results):
    """k = 3: one bad run out of three is inside the noise band; three bad runs out of three are a regression."""
    con = store.connect(tmp_path / "qa.sqlite")
    base = [dict(r, run=i) for i in range(3) for r in copy.deepcopy(fake_results)]
    args = dict(target="fake", suite="golden", k=3)
    store.ingest(con, store.load_run_dir(_write_run(tmp_path, "20261017-100000-fake-golden", base, args=args)))
    for rid, scores in [("20261017-110000-fake-golden", [0.3, 1.0, 1.0]), ("20261017-120000-fake-golden", [0.3, 0.3, 0.3])]:
        cand = copy.deepcopy(base)
        for r in cand:
            if r["case_id"] == "Q01":
                r["score"] = scores[r["run"]]
                r["passed"] = r["score"] == 1.0
        store.ingest(con, store.load_run_dir(_write_run(tmp_path, rid, cand, args=args)))
    flaky = next(d for d in gate.compare(con, "20261017-100000-fake-golden", "20261017-110000-fake-golden").cases if d.case_id == "Q01")
    assert flaky.threshold > 0.4 and flaky.status == "unchanged"
    steady = next(d for d in gate.compare(con, "20261017-100000-fake-golden", "20261017-120000-fake-golden").cases if d.case_id == "Q01")
    assert steady.threshold == 0.05 and steady.status == "regressed" and steady.reasons[1] == "pass rate 1.00 -> 0.00"


def test_gate_new_leak_is_always_a_regression(gate_db, fake_results):
    con, tmp = gate_db
    leaky = copy.deepcopy(fake_results)
    leaky[0]["leak"] = True
    store.ingest(con, store.load_run_dir(_write_run(tmp, "20261017-150000-api-golden", leaky)))
    g = gate.compare(con, "20261017-100000-fake-golden", "20261017-150000-api-golden", margin=1.0)
    assert g.regressed and "new cross-principal leak" in g.cases[0].reasons


def test_gate_cli_exit_codes_and_report(gate_db, capsys):
    con, tmp = gate_db
    db = str(tmp / "qa.sqlite")
    out = tmp / "gate.md"
    assert gate.main(["--db", db, "--baseline", "20261017-100000", "--candidate", "20261017-110000"]) == 0
    assert gate.main(["--db", db, "--baseline", "20261017-100000", "--candidate", "latest:api", "--out", str(out),
                      "--json", str(tmp / "gate.json")]) == 1
    md = out.read_text()
    assert "REGRESSION" in md and "| Q06 | REGRESSED |" in md
    assert json.loads((tmp / "gate.json").read_text())["regressed"] is True
    assert gate.main(["--db", db, "--baseline", "nope", "--candidate", "latest"]) == 2
    # a run directory path is imported on the fly
    assert gate.main(["--db", db, "--baseline", str(tmp / "20261017-100000-fake-golden"),
                      "--candidate", str(tmp / "20261017-110000-fake-golden")]) == 0


# ---------------------------------------------------------------- API
def test_api_endpoints(tmp_path, monkeypatch, fake_results):
    from fastapi.testclient import TestClient

    from evals.qa import api
    _write_run(tmp_path, "20261017-100000-fake-golden", fake_results)
    _write_run(tmp_path, "20261017-120000-api-golden", _degrade(fake_results, "Q06"))
    monkeypatch.setenv("QA_DB", str(tmp_path / "qa.sqlite"))
    monkeypatch.setattr(api, "DIRS", [tmp_path])
    monkeypatch.setattr(api, "_last_scan", 0.0)
    c = TestClient(api.app)
    runs = c.get("/qa/runs").json()["runs"]
    assert [r["run_id"] for r in runs] == ["20261017-100000-fake-golden", "20261017-120000-api-golden"]
    r = c.get("/qa/runs/20261017-120000").json()
    assert len(r["cases"]) == 18 and r["taxonomy"]["failed"] == 1
    assert c.get("/qa/runs/20261017-120000/cases/Q11").json()["runs"][0]["turns"][1]["scores"]["memory"] is not None
    assert len(c.get("/qa/cases/Q06/history").json()["history"]) == 2
    m = c.get("/qa/matrix").json()
    assert m["cells"]["Q06"]["20261017-120000-api-golden"]["score"] == 0.2
    cmp = c.get("/qa/compare", params=dict(a="20261017-100000", b="20261017-120000")).json()
    assert cmp["regressed"] and "REGRESSION" in cmp["report_md"]
    tx = c.get("/qa/taxonomy").json()
    assert len(tx["categories"]) == len(CATEGORIES) and tx["counts"]["failed"] == 1
    assert c.get("/qa/runs/nope").status_code == 404
    assert c.post("/qa/runs").status_code == 405  # read-only
