"""SQLite results store for eval runs: runs → case_results → turns → scores (one row per scorer).

The run directories written by `evals.run` (`results/<run_id>/results.json`, or `partial.jsonl` while a run is still
going or after it was killed) are the source of truth; this store is a queryable index over them. Import is
idempotent: a run is re-imported when its source file changed (size + mtime fingerprint), so a live run that keeps
appending to partial.jsonl shows up case by case.

CLI:
  uv run python -m evals.qa.store import [results_dir_or_run_dir ...]   # default: evals/results
  uv run python -m evals.qa.store list
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable

from evals import REPO_ROOT, RESULTS_DIR
from evals.qa.taxonomy import classify_case, classify_turn

DEFAULT_DB = Path(os.environ.get("QA_DB", RESULTS_DIR / "qa.sqlite"))
RUN_ID_RE = re.compile(r"^(\d{8})-(\d{6})-([a-z]+)-([a-z]+)")
MODEL_RE = re.compile(r"^\s*(.+?)\s*(?:→|->)\s*(.+?)\s*$")
DATASET_RE = re.compile(r"\bdataset[=: ]+([\w.-]+)", re.I)

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, started_at TEXT, target TEXT, suite TEXT, k INTEGER,
  model TEXT, harness TEXT, git_sha TEXT, git_sha_inferred INTEGER DEFAULT 0, data_version TEXT,
  n_cases INTEGER, n_case_runs INTEGER, mean_score REAL, pass_rate REAL, pass_hat_k REAL, pass_at_k REAL,
  leaks INTEGER, latency_mean_s REAL, latency_total_s REAL, tokens_total INTEGER, retries_total INTEGER,
  partial INTEGER, interrupted TEXT, source_path TEXT, fingerprint TEXT, imported_at TEXT,
  args_json TEXT, per_scorer_json TEXT, meta_eval_json TEXT
);
CREATE TABLE IF NOT EXISTS case_results (
  id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
  case_id TEXT NOT NULL, run_idx INTEGER, suite TEXT, title TEXT, score REAL, passed INTEGER, leak INTEGER,
  failure_category TEXT, categories_json TEXT, latency_s REAL, tokens INTEGER, n_turns INTEGER, error TEXT,
  UNIQUE (run_id, case_id, run_idx)
);
CREATE TABLE IF NOT EXISTS turns (
  id INTEGER PRIMARY KEY AUTOINCREMENT, case_result_id INTEGER NOT NULL REFERENCES case_results(id) ON DELETE CASCADE,
  run_id TEXT NOT NULL, case_id TEXT NOT NULL, run_idx INTEGER, turn_idx INTEGER, turn_id TEXT,
  user TEXT, chat TEXT, clock TEXT, say TEXT, setup INTEGER, score REAL, passed INTEGER, leak INTEGER,
  latency_s REAL, tokens INTEGER, retries INTEGER, model TEXT, error TEXT,
  failure_category TEXT, taxonomy_json TEXT, expect_json TEXT, answer_json TEXT, trace_json TEXT
);
CREATE TABLE IF NOT EXISTS scores (
  turn_row_id INTEGER NOT NULL REFERENCES turns(id) ON DELETE CASCADE, run_id TEXT NOT NULL, case_id TEXT NOT NULL,
  scorer TEXT NOT NULL, value REAL, reason TEXT, details_json TEXT
);
CREATE INDEX IF NOT EXISTS ix_case_results_case ON case_results(case_id);
CREATE INDEX IF NOT EXISTS ix_turns_case_result ON turns(case_result_id);
CREATE INDEX IF NOT EXISTS ix_scores_run ON scores(run_id, scorer);
"""


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(path or DEFAULT_DB)
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(p), check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.executescript(SCHEMA)
    return con


# ---------------------------------------------------------------- extraction helpers
def _j(v) -> str:
    return json.dumps(v, default=str)


def _meta(turn: dict) -> dict:
    a = turn.get("answer")
    return (a.get("_meta") or {}) if isinstance(a, dict) else {}


def turn_model(turn: dict) -> tuple[str | None, str | None]:
    """(harness, model) from the wellops.usage / wellops.verification events the API streams."""
    ev = _meta(turn).get("events") or {}
    harness = model = None
    for u in ev.get("wellops.usage") or []:
        m = (u or {}).get("model")
        if m:
            mm = MODEL_RE.match(m)
            harness, model = (mm.group(1), mm.group(2)) if mm else (None, m)
    for v in ev.get("wellops.verification") or []:
        harness = harness or (v or {}).get("harness")
    return harness, model


def _tok(v) -> int | None:
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, dict):
        for k in ("total", "total_tokens"):
            if isinstance(v.get(k), (int, float)):
                return int(v[k])
        parts = [v.get(k) for k in ("input", "output", "prompt", "completion", "input_tokens", "output_tokens",
                                    "prompt_tokens", "completion_tokens")]
        nums = [int(p) for p in parts if isinstance(p, (int, float))]
        return sum(nums) if nums else None
    return None


def turn_tokens(turn: dict) -> int | None:
    m = _meta(turn)
    t = _tok(m.get("tokens"))
    for u in (m.get("events") or {}).get("wellops.usage") or []:
        t = t if t is not None else _tok((u or {}).get("tokens") or u)
    return t


def turn_retries(turn: dict) -> int | None:
    vs = (_meta(turn).get("events") or {}).get("wellops.verification") or []
    r = [v.get("retries") for v in vs if isinstance(v, dict) and isinstance(v.get("retries"), int)]
    return max(r) if r else None


def data_version(results: list[dict]) -> str | None:
    for r in results:
        for t in r.get("turns", []):
            for a in (t.get("answer") or {}).get("assumptions") or []:
                m = DATASET_RE.search(str(a))
                if m:
                    return m.group(1)
    return None


def run_started_at(run_id: str, fallback: float | None = None) -> str | None:
    m = RUN_ID_RE.match(run_id)
    if m:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S").isoformat()
    return datetime.fromtimestamp(fallback).isoformat(timespec="seconds") if fallback else None


def infer_git_sha(started_at: str | None, cwd: Path = REPO_ROOT) -> str | None:
    """Best guess for legacy runs that did not record a sha: the last commit before the run started."""
    if not started_at:
        return None
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%h", f"--before={started_at}"], cwd=cwd, capture_output=True,
                             text=True, timeout=5)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def current_git_sha(cwd: Path = REPO_ROOT) -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=cwd, capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


_TRUTHS: dict[tuple[str, str], dict] | None = None


def case_truths() -> dict[tuple[str, str], dict]:
    """(case_id, turn_id) -> truth block from the case files (older run files do not carry it)."""
    global _TRUTHS
    if _TRUTHS is None:
        try:
            from evals.cases import load_cases
            _TRUTHS = {(c["id"], t.get("id", f"t{i + 1}")): t["truth"] for c in load_cases("all")
                       for i, t in enumerate(c["turns"]) if t.get("truth")}
        except Exception:  # a broken case file must not stop the explorer
            _TRUTHS = {}
    return _TRUTHS


# ---------------------------------------------------------------- loading run directories
def load_run_dir(d: Path) -> dict | None:
    """{run_id, args, summary?, results, partial, source, fingerprint} from results.json, else partial.jsonl."""
    d = Path(d)
    rj, pj = d / "results.json", d / "partial.jsonl"
    if rj.exists():
        doc = json.loads(rj.read_text())
        src = rj
        out = dict(run_id=doc.get("run_id") or d.name, args=doc.get("args") or {}, summary=doc.get("summary") or {},
                   results=doc.get("results") or [], meta=doc.get("meta") or {},
                   partial=bool((doc.get("summary") or {}).get("interrupted")))
    elif pj.exists():
        src = pj
        rows = []
        for line in pj.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    pass  # the last line may be half-written while a run is in progress
        m = RUN_ID_RE.match(d.name)
        args = dict(target=m.group(3), suite=m.group(4), k=max([r.get("run", 0) for r in rows], default=0) + 1) if m else {}
        out = dict(run_id=d.name, args=args, summary={}, results=rows, meta={}, partial=True)
    else:
        return None
    side = d / "meta.json"  # optional annotations for runs that predate recorded metadata (model, harness, git_sha)
    if side.exists():
        out["meta"] = {**json.loads(side.read_text()), **out["meta"]}
    me = d / "meta_eval.json"
    if me.exists():
        out["meta_eval"] = json.loads(me.read_text())
    st = src.stat()
    out["source"] = str(src)
    out["fingerprint"] = f"{st.st_size}:{int(st.st_mtime)}"
    out["mtime"] = st.st_mtime
    return out


def ingest(con: sqlite3.Connection, run: dict, *, git_sha: str | None = None, model: str | None = None,
           harness: str | None = None, force: bool = False) -> bool:
    """Write one run (as returned by load_run_dir or built in-process by evals.run). Returns False when unchanged."""
    rid = run["run_id"]
    row = con.execute("SELECT fingerprint FROM runs WHERE run_id = ?", (rid,)).fetchone()
    if row and not force and run.get("fingerprint") and row["fingerprint"] == run["fingerprint"]:
        return False
    results, args, meta = run.get("results") or [], run.get("args") or {}, run.get("meta") or {}
    truths = case_truths()
    with con:
        con.execute("DELETE FROM runs WHERE run_id = ?", (rid,))
        per_scorer: dict[str, list[float]] = defaultdict(list)
        lat_turns, tokens_total, retries_total, models, harnesses = [], 0, 0, set(), set()
        cases_seen: dict[str, list[dict]] = defaultdict(list)
        started = meta.get("started_at") or run_started_at(rid, run.get("mtime"))
        con.execute("INSERT INTO runs (run_id) VALUES (?)", (rid,))
        for r in results:
            turns = r.get("turns") or []
            ctruth = {t.get("turn_id"): t.get("truth") or truths.get((r["case_id"], t.get("turn_id"))) for t in turns}
            cls = classify_case(turns, ctruth)
            c_lat = sum(t.get("latency_s") or 0 for t in turns)
            c_tok = [x for x in (turn_tokens(t) for t in turns) if x is not None]
            err = next((t.get("error") for t in turns if t.get("error")), None)
            cur = con.execute(
                "INSERT INTO case_results (run_id, case_id, run_idx, suite, title, score, passed, leak, failure_category,"
                " categories_json, latency_s, tokens, n_turns, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, r["case_id"], r.get("run", 0), r.get("suite"), r.get("title"), r.get("score"), int(bool(r.get("passed"))),
                 int(bool(r.get("leak"))), cls.primary, _j(cls.categories), round(c_lat, 3), sum(c_tok) if c_tok else None,
                 len(turns), (err or "").splitlines()[0][:500] if err else None))
            cr_id = cur.lastrowid
            cases_seen[r["case_id"]].append(r)
            for i, t in enumerate(turns):
                h, m = turn_model(t)
                if m:
                    models.add(m)
                if h:
                    harnesses.add(h)
                tok, ret = turn_tokens(t), turn_retries(t)
                tokens_total += tok or 0
                retries_total += ret or 0
                lat_turns.append(t.get("latency_s") or 0)
                tc = classify_turn(t, ctruth.get(t.get("turn_id")))
                agg = t.get("agg") or {}
                cur = con.execute(
                    "INSERT INTO turns (case_result_id, run_id, case_id, run_idx, turn_idx, turn_id, user, chat, clock, say,"
                    " setup, score, passed, leak, latency_s, tokens, retries, model, error, failure_category, taxonomy_json,"
                    " expect_json, answer_json, trace_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cr_id, rid, r["case_id"], r.get("run", 0), i, t.get("turn_id"), t.get("user"), t.get("chat"),
                     t.get("clock"), t.get("say"), int(bool(t.get("setup"))), agg.get("score"),
                     None if t.get("setup") else int(bool(agg.get("passed"))), int(bool(agg.get("leak"))),
                     t.get("latency_s"), tok, ret, m, t.get("error"), tc.primary, _j(tc.to_dict()),
                     _j(t.get("expect") or {}), _j(t.get("answer")), _j(t.get("trace") or [])))
                tid = cur.lastrowid
                for name, s in (t.get("scores") or {}).items():
                    con.execute("INSERT INTO scores (turn_row_id, run_id, case_id, scorer, value, reason, details_json)"
                                " VALUES (?,?,?,?,?,?,?)", (tid, rid, r["case_id"], name, s.get("value"), s.get("reason"),
                                                            _j(s.get("details") or {})))
                    if s.get("value") is not None and not t.get("setup"):
                        per_scorer[name].append(s["value"])
        n_runs = len(results)
        case_means = [sum(x.get("score", 0) for x in rs) / len(rs) for rs in cases_seen.values()]
        pass_hat = sum(all(x.get("passed") for x in rs) for rs in cases_seen.values()) / len(cases_seen) if cases_seen else None
        pass_at = sum(any(x.get("passed") for x in rs) for rs in cases_seen.values()) / len(cases_seen) if cases_seen else None
        sha = git_sha or meta.get("git_sha") or args.get("git_sha")
        inferred = 0
        if not sha:
            sha, inferred = infer_git_sha(started), 1
        tgt = args.get("target")
        con.execute(
            "UPDATE runs SET started_at=?, target=?, suite=?, k=?, model=?, harness=?, git_sha=?, git_sha_inferred=?,"
            " data_version=?, n_cases=?, n_case_runs=?, mean_score=?, pass_rate=?, pass_hat_k=?, pass_at_k=?, leaks=?,"
            " latency_mean_s=?, latency_total_s=?, tokens_total=?, retries_total=?, partial=?, interrupted=?,"
            " source_path=?, fingerprint=?, imported_at=?, args_json=?, per_scorer_json=?, meta_eval_json=? WHERE run_id=?",
            (started, tgt, args.get("suite"), args.get("k"),
             model or meta.get("model") or (", ".join(sorted(models)) if models else ("computed truth" if tgt == "fake" else None)),
             harness or meta.get("harness") or (", ".join(sorted(harnesses)) if harnesses else tgt),
             sha, inferred, meta.get("data_version") or data_version(results) or "sample",
             len(cases_seen), n_runs, sum(case_means) / len(case_means) if case_means else None,
             sum(bool(x.get("passed")) for x in results) / n_runs if n_runs else None, pass_hat, pass_at,
             sum(any(x.get("leak") for x in rs) for rs in cases_seen.values()),
             sum(lat_turns) / len(lat_turns) if lat_turns else None, round(sum(lat_turns), 3),
             tokens_total or None, retries_total, int(bool(run.get("partial"))),
             (run.get("summary") or {}).get("interrupted"), run.get("source"), run.get("fingerprint"),
             datetime.now().isoformat(timespec="seconds"), _j(args),
             _j({n: sum(v) / len(v) for n, v in per_scorer.items()}), _j(run.get("meta_eval")) if run.get("meta_eval") else None,
             rid))
    return True


def run_dirs(paths: Iterable[Path]) -> list[Path]:
    out = []
    for p in paths:
        p = Path(p)
        if (p / "results.json").exists() or (p / "partial.jsonl").exists():
            out.append(p)
        elif p.is_dir():
            out += sorted(d for d in p.iterdir() if d.is_dir() and ((d / "results.json").exists() or (d / "partial.jsonl").exists()))
    return out


def import_paths(con: sqlite3.Connection, paths: Iterable[Path] = (RESULTS_DIR,), force: bool = False) -> list[str]:
    changed = []
    for d in run_dirs(paths):
        run = load_run_dir(d)
        if run and ingest(con, run, force=force):
            changed.append(run["run_id"])
    return changed


# ---------------------------------------------------------------- queries (shared by the API and the gate)
def _row(r: sqlite3.Row | None) -> dict | None:
    if r is None:
        return None
    d = dict(r)
    for k in list(d):
        if k.endswith("_json"):
            v = d.pop(k)
            d[k[:-5]] = json.loads(v) if v else None
    return d


def list_runs(con: sqlite3.Connection) -> list[dict]:
    return [_row(r) for r in con.execute("SELECT * FROM runs ORDER BY started_at, run_id")]


def resolve_run(con: sqlite3.Connection, ref: str) -> str | None:
    """Exact id, unique prefix, 'latest', or 'latest:<target>'."""
    if ref.startswith("latest"):
        _, _, tgt = ref.partition(":")
        q = "SELECT run_id FROM runs" + (" WHERE target = ?" if tgt else "") + " ORDER BY started_at DESC, run_id DESC LIMIT 1"
        r = con.execute(q, (tgt,) if tgt else ()).fetchone()
        return r["run_id"] if r else None
    r = con.execute("SELECT run_id FROM runs WHERE run_id = ?", (ref,)).fetchone()
    if r:
        return r["run_id"]
    rs = con.execute("SELECT run_id FROM runs WHERE run_id LIKE ?", (ref + "%",)).fetchall()
    return rs[0]["run_id"] if len(rs) == 1 else None


def get_run(con: sqlite3.Connection, run_id: str) -> dict | None:
    run = _row(con.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone())
    if not run:
        return None
    run["cases"] = [_row(r) for r in con.execute(
        "SELECT * FROM case_results WHERE run_id = ? ORDER BY case_id, run_idx", (run_id,))]
    return run


def case_runs(con: sqlite3.Connection, run_id: str) -> dict[str, dict]:
    """case_id -> {mean score, pass rate, scores per run, leak, primary category} aggregated over the k runs."""
    out: dict[str, dict] = {}
    for r in con.execute("SELECT * FROM case_results WHERE run_id = ? ORDER BY case_id, run_idx", (run_id,)):
        c = out.setdefault(r["case_id"], dict(case_id=r["case_id"], title=r["title"], suite=r["suite"], scores=[], passed=[],
                                              leak=False, categories=[], failure_category=None))
        c["scores"].append(r["score"] or 0.0)
        c["passed"].append(bool(r["passed"]))
        c["leak"] = c["leak"] or bool(r["leak"])
        for cat in json.loads(r["categories_json"] or "[]"):
            if cat not in c["categories"]:
                c["categories"].append(cat)
        c["failure_category"] = c["failure_category"] or r["failure_category"]
    for c in out.values():
        c["mean_score"] = sum(c["scores"]) / len(c["scores"])
        c["pass_rate"] = sum(c["passed"]) / len(c["passed"])
    return out


def case_detail(con: sqlite3.Connection, run_id: str, case_id: str) -> dict | None:
    crs = [_row(r) for r in con.execute("SELECT * FROM case_results WHERE run_id = ? AND case_id = ? ORDER BY run_idx",
                                        (run_id, case_id))]
    if not crs:
        return None
    for cr in crs:
        turns = [_row(t) for t in con.execute("SELECT * FROM turns WHERE case_result_id = ? ORDER BY turn_idx", (cr["id"],))]
        for t in turns:
            t["scores"] = {s["scorer"]: dict(value=s["value"], reason=s["reason"], details=json.loads(s["details_json"] or "{}"))
                           for s in con.execute("SELECT * FROM scores WHERE turn_row_id = ?", (t["id"],))}
        cr["turns"] = turns
    return dict(run_id=run_id, case_id=case_id, title=crs[0]["title"], runs=crs)


def case_history(con: sqlite3.Connection, case_id: str) -> list[dict]:
    return [dict(r) | dict(categories=json.loads(r["categories_json"] or "[]")) for r in con.execute(
        "SELECT c.run_id, c.run_idx, c.score, c.passed, c.leak, c.failure_category, c.categories_json, c.latency_s,"
        " c.tokens, r.started_at, r.target, r.model, r.harness, r.git_sha FROM case_results c JOIN runs r USING (run_id)"
        " WHERE c.case_id = ? ORDER BY r.started_at, c.run_id, c.run_idx", (case_id,))]


def taxonomy_counts(con: sqlite3.Connection, run_id: str | None = None, level: str = "case") -> dict:
    """Counts per failure category over failed case runs (primary + any) or failed turns; optionally one run."""
    tbl = "case_results" if level == "case" else "turns"
    where, params = (f"WHERE {tbl}.passed = 0", [])
    if run_id:
        where += f" AND {tbl}.run_id = ?"
        params.append(run_id)
    primary: dict[str, int] = defaultdict(int)
    anyc: dict[str, int] = defaultdict(int)
    examples: dict[str, list] = defaultdict(list)
    col = "categories_json" if tbl == "case_results" else "taxonomy_json"
    n = 0
    for r in con.execute(f"SELECT run_id, case_id, failure_category, {col} AS c FROM {tbl} {where} ORDER BY run_id DESC, case_id", params):
        n += 1
        if r["failure_category"]:
            primary[r["failure_category"]] += 1
        cats = json.loads(r["c"] or "[]")
        cats = cats.get("categories", []) if isinstance(cats, dict) else cats
        for cat in cats:
            anyc[cat] += 1
            if len(examples[cat]) < 8 and all(e["case_id"] != r["case_id"] for e in examples[cat]):
                examples[cat].append(dict(run_id=r["run_id"], case_id=r["case_id"]))  # newest run first, one per case
    return dict(level=level, run_id=run_id, failed=n, primary=dict(primary), any=dict(anyc), examples=dict(examples))


def matrix(con: sqlite3.Connection, suite: str | None = None, target: str | None = None, limit: int = 30) -> dict:
    q, p = "SELECT run_id FROM runs", []
    conds = []
    if target:
        conds.append("target = ?")
        p.append(target)
    if suite:
        conds.append("(suite = ? OR suite = 'all')")
        p.append(suite)
    if conds:
        q += " WHERE " + " AND ".join(conds)
    runs = [r["run_id"] for r in con.execute(q + " ORDER BY started_at DESC, run_id DESC LIMIT ?", (*p, limit))][::-1]
    cells: dict[str, dict] = {}
    titles: dict[str, str] = {}
    for rid in runs:
        for cid, c in case_runs(con, rid).items():
            if suite and c["suite"] != suite:
                continue
            titles[cid] = c["title"]
            cells.setdefault(cid, {})[rid] = dict(score=round(c["mean_score"], 4), pass_rate=c["pass_rate"], leak=c["leak"],
                                                  failure_category=c["failure_category"], k=len(c["scores"]))
    cases = sorted(cells)
    return dict(runs=runs, cases=[dict(case_id=c, title=titles[c]) for c in cases], cells=cells)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="evals.qa.store")
    sub = ap.add_subparsers(dest="cmd", required=True)
    imp = sub.add_parser("import", help="import run directories (default: evals/results)")
    imp.add_argument("paths", nargs="*", default=[str(RESULTS_DIR)])
    imp.add_argument("--force", action="store_true")
    sub.add_parser("list")
    ap.add_argument("--db", default=None)
    a = ap.parse_args(argv)
    con = connect(a.db)
    if a.cmd == "import":
        ch = import_paths(con, [Path(p) for p in a.paths], force=a.force)
        print(f"imported {len(ch)} run(s): {', '.join(ch) or '-'}")
    else:
        for r in list_runs(con):
            print(f"{r['run_id']:<40} {r['target'] or '':<7} {str(r['model'] or ''):<28} cases={r['n_cases']:<3} "
                  f"pass={r['pass_rate'] or 0:.2f} score={r['mean_score'] or 0:.3f}{' PARTIAL' if r['partial'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
