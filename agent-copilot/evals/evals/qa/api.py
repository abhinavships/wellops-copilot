"""Read-only QA explorer API over the SQLite results store.

  cd agent-copilot/evals && uv run uvicorn evals.qa.api:app --port 8020

Env: QA_DB (store path, default evals/results/qa.sqlite), QA_RESULTS_DIRS (run directories to index, separated by
os.pathsep, default evals/results), QA_REFRESH_S (rescan interval, default 5 s; runs still being written appear case
by case). Only GET endpoints; nothing here can change a run.
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from evals import RESULTS_DIR
from evals.qa import gate, store
from evals.qa.taxonomy import describe

DIRS = [Path(p) for p in os.environ.get("QA_RESULTS_DIRS", str(RESULTS_DIR)).split(os.pathsep) if p]
REFRESH_S = float(os.environ.get("QA_REFRESH_S", "5"))
_lock = threading.Lock()
_last_scan = 0.0

app = FastAPI(title="WellOps QA explorer API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])


def _con():
    return store.connect(os.environ.get("QA_DB") or None)


def refresh(force: bool = False) -> list[str]:
    """Index new or changed run directories, at most every REFRESH_S seconds."""
    global _last_scan
    with _lock:
        if not force and time.time() - _last_scan < REFRESH_S:
            return []
        _last_scan = time.time()
        con = _con()
        try:
            return store.import_paths(con, DIRS)
        finally:
            con.close()


def _run_or_404(con, ref: str) -> str:
    rid = store.resolve_run(con, ref)
    if not rid:
        raise HTTPException(404, f"unknown run {ref}")
    return rid


@app.get("/qa/health")
def health():
    return dict(ok=True, dirs=[str(d) for d in DIRS], db=str(os.environ.get("QA_DB") or store.DEFAULT_DB))


@app.get("/qa/runs")
def runs():
    refresh()
    con = _con()
    return dict(runs=store.list_runs(con))


@app.get("/qa/runs/{run_id}")
def run(run_id: str):
    refresh()
    con = _con()
    rid = _run_or_404(con, run_id)
    r = store.get_run(con, rid)
    r["taxonomy"] = store.taxonomy_counts(con, rid)
    return r


@app.get("/qa/runs/{run_id}/cases/{case_id}")
def run_case(run_id: str, case_id: str):
    con = _con()
    rid = _run_or_404(con, run_id)
    d = store.case_detail(con, rid, case_id)
    if not d:
        raise HTTPException(404, f"case {case_id} not in run {rid}")
    return d


@app.get("/qa/cases/{case_id}/history")
def case_history(case_id: str):
    refresh()
    return dict(case_id=case_id, history=store.case_history(_con(), case_id))


@app.get("/qa/matrix")
def matrix(suite: str | None = "golden", target: str | None = None, limit: int = Query(30, ge=1, le=200)):
    refresh()
    return store.matrix(_con(), suite=suite or None, target=target or None, limit=limit)


@app.get("/qa/compare")
def compare(a: str, b: str, margin: float = Query(0.05, ge=0, le=1)):
    """a = baseline, b = candidate (same semantics as `python -m evals.qa.gate`)."""
    con = _con()
    ra, rb = _run_or_404(con, a), _run_or_404(con, b)
    g = gate.compare(con, ra, rb, margin)
    runs_ = {r["run_id"]: r for r in store.list_runs(con) if r["run_id"] in (ra, rb)}
    return g.to_dict() | dict(report_md=gate.report_md(g, runs_), runs=runs_)


@app.get("/qa/taxonomy")
def taxonomy(run: str | None = None, level: str = Query("case", pattern="^(case|turn)$")):
    refresh()
    con = _con()
    rid = _run_or_404(con, run) if run else None
    return dict(categories=describe(), counts=store.taxonomy_counts(con, rid, level))
