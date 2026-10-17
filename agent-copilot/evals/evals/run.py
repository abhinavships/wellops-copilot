"""CLI: uv run python -m evals.run --target fake|api --suite golden|synthetic|all --k 3 [--opik]"""
from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from collections import defaultdict
from datetime import datetime

from evals import RESULTS_DIR
from evals.cases import load_cases
from evals.metrics.scorers import SCORERS, pass_k
from evals.runner.episode import run_case
from evals.runner.targets import make_target


def summarize(results: list[dict], k: int) -> dict:
    by_case: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_case[r["case_id"]].append(r)
    per_scorer: dict[str, list[float]] = defaultdict(list)
    for r in results:
        for t in r["turns"]:
            if t["setup"]:
                continue
            for n, s in t["scores"].items():
                if s["value"] is not None:
                    per_scorer[n].append(s["value"])
    pk = pass_k([[r["passed"] for r in rs] for rs in by_case.values()])
    cases = {cid: dict(title=rs[0]["title"], suite=rs[0]["suite"], mean_score=sum(r["score"] for r in rs) / len(rs),
                       pass_all_k=all(r["passed"] for r in rs), pass_rate=sum(r["passed"] for r in rs) / len(rs),
                       leak=any(r["leak"] for r in rs)) for cid, rs in by_case.items()}
    lat = [t["latency_s"] for r in results for t in r["turns"]]
    return dict(n_cases=len(by_case), k=k, mean_score=sum(c["mean_score"] for c in cases.values()) / max(len(cases), 1),
                pass_hat_k=pk["pass_hat_k"], pass_at_k=pk["pass_at_k"], leaks=sum(c["leak"] for c in cases.values()),
                per_scorer_mean={n: sum(v) / len(v) for n, v in per_scorer.items()}, cases=cases,
                latency_mean_s=sum(lat) / len(lat) if lat else 0.0)


def report_md(run_id: str, args, summ: dict, audit: dict | None, meta: dict | None, opik_url: str | None) -> str:
    L = [f"# Eval run {run_id}", "", f"target=`{args.target}` suite=`{args.suite}` k={args.k} cases={summ['n_cases']}"
         + (f" opik={opik_url}" if opik_url else ""), "",
         f"- mean aggregate (0.5 facts + 0.25 evidence + 0.25 visual): **{summ['mean_score']:.3f}**",
         f"- pass^{args.k} (all runs fully pass): **{summ['pass_hat_k']:.3f}**, pass@{args.k}: {summ['pass_at_k']:.3f}",
         f"- cases with a cross-principal leak (hard fail): **{summ['leaks']}**", ""]
    if summ.get("interrupted"):
        L += [f"> **PARTIAL RUN** (interrupted: {summ['interrupted']}): only finished cases are scored.", ""]
    L += ["## Per-scorer mean (where applicable)", ""]
    L += [f"- {n}: {v:.3f}" for n, v in sorted(summ["per_scorer_mean"].items())]
    L += ["", "## Cases", "", "| case | title | mean | pass^k | leak |", "|---|---|---|---|---|"]
    for cid, c in sorted(summ["cases"].items()):
        L.append(f"| {cid} | {c['title'][:60]} | {c['mean_score']:.2f} | {'yes' if c['pass_all_k'] else 'NO'} | {'LEAK' if c['leak'] else ''} |")
    if audit:
        L += ["", "## Key audit", "", "counts: " + ", ".join(f"{k}={v}" for k, v in sorted(audit["summary"].items())),
              "(`disagrees_with_key` is triaged by a human: our bug / key error / ambiguity; never auto-fixed.)"]
    if meta:
        L += ["", "## Meta-eval (fault injection)", "", "| scorer | applied | caught | recall |", "|---|---|---|---|"]
        for n, d in sorted(meta["per_scorer"].items()):
            L.append(f"| {n} | {d['applied']} | {d['caught']} | {d['recall']:.2f} |")
        L.append(f"\nbaseline false-alarm rate on clean answers: {meta['baseline_false_alarm_rate']:.3f}")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="evals.run")
    ap.add_argument("--target", choices=["fake", "api", "hermes"], default="fake")
    ap.add_argument("--suite", choices=["golden", "synthetic", "all"], default="golden")
    ap.add_argument("--k", type=int, default=1)
    ap.add_argument("--opik", action="store_true", help="push dataset + run as Opik experiment (needs OPIK_API_KEY/OPIK_WORKSPACE)")
    ap.add_argument("--api-url", default=None)
    ap.add_argument("--cases", default=None, help="comma-separated case ids")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--no-meta", action="store_true")
    ap.add_argument("--project", default=None)
    a = ap.parse_args(argv)

    cases = load_cases(a.suite)
    if a.cases:
        want = set(a.cases.split(","))
        cases = [c for c in cases if c["id"] in want]
    if a.limit:
        cases = cases[: a.limit]
    target = make_target(a.target, **({"base_url": a.api_url} if a.api_url and a.target == "api" else {}))
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{a.target}-{a.suite}"
    out = RESULTS_DIR / run_id
    out.mkdir(parents=True, exist_ok=True)
    runner = lambda case, i: run_case(case, target, i)  # noqa: E731

    opik_url = None
    results: list[dict] = []
    if a.opik:
        from evals.runner import opik_sink
        client = opik_sink.get_client(a.project or opik_sink.PROJECT)
        if client is None:
            print("[opik] OPIK_API_KEY / OPIK_WORKSPACE not set: running offline, results written to JSON only", file=sys.stderr)
        else:
            ev, results = opik_sink.run_experiment(
                client, dataset_name=f"wellops-{a.suite}", cases=cases, runner=runner, k=a.k,
                experiment_name=f"{a.target}-{a.suite}-{run_id}", project=a.project or opik_sink.PROJECT,
                config=dict(target=a.target, suite=a.suite, k=a.k, run_id=run_id))
            opik_url = getattr(ev, "experiment_url", None) or getattr(ev, "url", None)
    interrupted = None
    if not results:
        # each finished case is appended at once, so a killed run (server restart, SIGTERM, Ctrl-C) keeps what it did
        signal.signal(signal.SIGTERM, _raise_interrupt)
        try:
            with (out / "partial.jsonl").open("a") as pf:
                for i in range(a.k):
                    for c in cases:
                        print(f"[run {i + 1}/{a.k}] {c['id']} ...", file=sys.stderr, flush=True)
                        results.append(run_case(c, target, i))
                        pf.write(json.dumps(results[-1], default=str) + "\n")
                        pf.flush()
        except KeyboardInterrupt as e:
            interrupted = str(e) or "KeyboardInterrupt"
            print(f"[interrupted: {interrupted}] writing a partial report for {len(results)} case run(s)", file=sys.stderr)
        if not results:
            print("no case finished; nothing to report", file=sys.stderr)
            return 130

    summ = summarize(results, a.k)
    if interrupted:
        summ["interrupted"] = interrupted
    audit = meta = None
    if a.suite in ("golden", "all"):
        from evals.truth.audit import audit as run_audit
        audit = run_audit([c for c in cases if c["suite"] == "golden"])
        (out / "key_audit.json").write_text(json.dumps(audit, indent=1, default=str))
    if a.target == "fake" and not a.no_meta and not interrupted:
        from evals.runner.faults import meta_eval
        meta = meta_eval(cases, target)
        (out / "meta_eval.json").write_text(json.dumps(meta, indent=1))
    (out / "results.json").write_text(json.dumps(dict(run_id=run_id, args=vars(a), summary=summ, results=results), indent=1, default=str))
    md = report_md(run_id, a, summ, audit, meta, opik_url)
    (out / "report.md").write_text(md)
    print(md)
    print(f"results: {out}")
    return 130 if interrupted else 0


def _raise_interrupt(signum, frame):
    raise KeyboardInterrupt(f"signal {signum}")


if __name__ == "__main__":
    sys.exit(main())
