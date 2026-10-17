"""Regression gate: compare a candidate run against a baseline, per case and in aggregate, with a noise margin.

  uv run python -m evals.qa.gate --baseline <run> --candidate <run> [--margin 0.05] [--out report.md] [--json out.json]

<run> is a run id (or unique prefix), `latest`, `latest:<target>`, or a run directory path (imported on the fly).
Exit code 0 = no regression, 1 = regression, 2 = usage error (unknown run, no overlapping cases).

A case regresses when any of these hold (only cases present in both runs are compared):
  - a cross-principal leak appears (hard fail, no margin);
  - its mean score drops by more than the case threshold;
  - its pass rate over the k runs drops by more than the case threshold (with k = 1, pass -> fail counts in full).
The case threshold is max(margin, 2 x standard error of the per-run scores) when either side ran k > 1, so a flaky case
with a wide spread must move further before it is called a regression. In aggregate, the mean score, pass rate and
pass^k regress when they drop by more than `margin`. Cases that only the baseline has are reported, and fail the gate
only with --require-all.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from evals.qa import store
from evals.qa.taxonomy import BY_KEY


@dataclass
class CaseDelta:
    case_id: str
    title: str
    base_score: float
    cand_score: float
    base_pass: float
    cand_pass: float
    threshold: float
    status: str  # regressed | improved | unchanged
    reasons: list[str] = field(default_factory=list)
    base_category: str | None = None
    cand_category: str | None = None

    @property
    def delta(self) -> float:
        return self.cand_score - self.base_score


@dataclass
class GateResult:
    baseline: str
    candidate: str
    margin: float
    cases: list[CaseDelta]
    aggregate: dict
    only_baseline: list[str]
    only_candidate: list[str]
    regressed: bool
    reasons: list[str]

    def to_dict(self) -> dict:
        d = asdict(self)
        for c, cd in zip(d["cases"], self.cases):
            c["delta"] = round(cd.delta, 4)
        return d


def _se(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1)) / math.sqrt(len(xs))


def _agg(cases: dict[str, dict], ids: list[str]) -> dict:
    if not ids:
        return dict(mean_score=None, pass_rate=None, pass_hat_k=None)
    return dict(mean_score=sum(cases[i]["mean_score"] for i in ids) / len(ids),
                pass_rate=sum(cases[i]["pass_rate"] for i in ids) / len(ids),
                pass_hat_k=sum(all(cases[i]["passed"]) for i in ids) / len(ids))


def compare(con: sqlite3.Connection, baseline: str, candidate: str, margin: float = 0.05, require_all: bool = False) -> GateResult:
    b, c = store.case_runs(con, baseline), store.case_runs(con, candidate)
    common = sorted(set(b) & set(c))
    deltas: list[CaseDelta] = []
    for cid in common:
        bb, cc = b[cid], c[cid]
        k = max(len(bb["scores"]), len(cc["scores"]))
        thr = max(margin, 2 * math.sqrt(_se(bb["scores"]) ** 2 + _se(cc["scores"]) ** 2)) if k > 1 else margin
        why = []
        if cc["leak"] and not bb["leak"]:
            why.append("new cross-principal leak")
        if cc["mean_score"] < bb["mean_score"] - thr:
            why.append(f"score {bb['mean_score']:.3f} -> {cc['mean_score']:.3f}")
        if cc["pass_rate"] < bb["pass_rate"] - thr:
            why.append(f"pass rate {bb['pass_rate']:.2f} -> {cc['pass_rate']:.2f}")
        improved = (cc["mean_score"] > bb["mean_score"] + thr or cc["pass_rate"] > bb["pass_rate"] + thr) and not why
        deltas.append(CaseDelta(cid, cc["title"] or bb["title"] or "", bb["mean_score"], cc["mean_score"], bb["pass_rate"],
                                cc["pass_rate"], round(thr, 4), "regressed" if why else "improved" if improved else "unchanged",
                                why, bb["failure_category"], cc["failure_category"]))
    ab, ac = _agg(b, common), _agg(c, common)
    reasons = [f"{d.case_id}: {'; '.join(d.reasons)}" for d in deltas if d.status == "regressed"]
    agg = {}
    for key in ("mean_score", "pass_rate", "pass_hat_k"):
        bv, cv = ab[key], ac[key]
        reg = bv is not None and cv is not None and cv < bv - margin
        agg[key] = dict(baseline=bv, candidate=cv, delta=None if bv is None or cv is None else cv - bv, regressed=reg)
        if reg:
            reasons.append(f"aggregate {key} {bv:.3f} -> {cv:.3f} (margin {margin})")
    only_b, only_c = sorted(set(b) - set(c)), sorted(set(c) - set(b))
    if require_all and only_b:
        reasons.append(f"{len(only_b)} baseline case(s) missing from the candidate: {', '.join(only_b[:10])}")
    return GateResult(baseline, candidate, margin, deltas, agg, only_b, only_c, bool(reasons), reasons)


def _f(v) -> str:
    return "–" if v is None else f"{v:.3f}"


def report_md(g: GateResult, runs: dict[str, dict] | None = None) -> str:
    runs = runs or {}
    verdict = "**REGRESSION**" if g.regressed else "**PASS**"
    L = [f"# Regression gate: {verdict}", "",
         f"baseline `{g.baseline}` vs candidate `{g.candidate}`, noise margin {g.margin}, {len(g.cases)} common case(s)", ""]
    for rid, label in ((g.baseline, "baseline"), (g.candidate, "candidate")):
        r = runs.get(rid)
        if r:
            L.append(f"- {label}: target={r.get('target')} model={r.get('model')} harness={r.get('harness')} "
                     f"git={r.get('git_sha')}{' (inferred)' if r.get('git_sha_inferred') else ''} data={r.get('data_version')}"
                     f"{' PARTIAL' if r.get('partial') else ''}")
    L += ["", "## Aggregate (common cases)", "", "| metric | baseline | candidate | delta | |", "|---|---|---|---|---|"]
    for k, a in g.aggregate.items():
        L.append(f"| {k} | {_f(a['baseline'])} | {_f(a['candidate'])} | {_f(a['delta'])} | {'REGRESSED' if a['regressed'] else ''} |")
    if g.reasons:
        L += ["", "## Why it failed", ""] + [f"- {r}" for r in g.reasons]
    order = {"regressed": 0, "improved": 1, "unchanged": 2}
    L += ["", "## Per case", "", "| case | status | baseline | candidate | delta | threshold | failure (cand) | title |",
          "|---|---|---|---|---|---|---|---|"]
    for d in sorted(g.cases, key=lambda d: (order[d.status], d.delta)):
        cat = BY_KEY[d.cand_category].label if d.cand_category in BY_KEY else ""
        L.append(f"| {d.case_id} | {d.status.upper() if d.status == 'regressed' else d.status} | {d.base_score:.3f} | "
                 f"{d.cand_score:.3f} | {d.delta:+.3f} | {d.threshold:.3f} | {cat} | {d.title[:50]} |")
    if g.only_baseline:
        L += ["", f"Only in baseline ({len(g.only_baseline)}): {', '.join(g.only_baseline)}"]
    if g.only_candidate:
        L += ["", f"Only in candidate ({len(g.only_candidate)}): {', '.join(g.only_candidate)}"]
    return "\n".join(L) + "\n"


def _resolve(con: sqlite3.Connection, ref: str) -> str | None:
    p = Path(ref)
    if p.is_dir():
        run = store.load_run_dir(p)
        if not run:
            return None
        store.ingest(con, run)
        return run["run_id"]
    return store.resolve_run(con, ref)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="evals.qa.gate", description=__doc__.split("\n\n")[0])
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--margin", type=float, default=0.05)
    ap.add_argument("--require-all", action="store_true", help="fail when baseline cases are missing from the candidate")
    ap.add_argument("--db", default=None, help="QA store path (default evals/results/qa.sqlite or $QA_DB)")
    ap.add_argument("--import", dest="do_import", action="store_true", help="import evals/results before resolving runs")
    ap.add_argument("--out", default=None, help="write the markdown report here")
    ap.add_argument("--json", default=None, help="write the JSON result here")
    a = ap.parse_args(argv)
    con = store.connect(a.db)
    if a.do_import:
        store.import_paths(con)
    b, c = _resolve(con, a.baseline), _resolve(con, a.candidate)
    if not b or not c:
        print(f"unknown run: {a.baseline if not b else a.candidate}", file=sys.stderr)
        return 2
    g = compare(con, b, c, a.margin, a.require_all)
    if not g.cases:
        print(f"no common cases between {b} and {c}", file=sys.stderr)
        return 2
    runs = {r["run_id"]: r for r in store.list_runs(con) if r["run_id"] in (b, c)}
    md = report_md(g, runs)
    if a.out:
        Path(a.out).write_text(md)
    if a.json:
        Path(a.json).write_text(json.dumps(g.to_dict(), indent=1, default=str))
    print(md)
    return 1 if g.regressed else 0


if __name__ == "__main__":
    sys.exit(main())
