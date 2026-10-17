"""Scratch DuckDB with the substrate ingested (never touches backend/wellops.duckdb)."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from wellops.substrate import ingest

from evals import CACHE_DIR, SAMPLE_DATA

DS = "sample"
_CON = {}


def asof(day: date | str) -> str:
    """Same convention as RunDeps.asof: end of the clock day (UTC)."""
    return f"{day} 23:59:59+00"


def get_con(data_dir: Path = SAMPLE_DATA, ds: str = DS, fresh: bool = False):
    key = (str(data_dir), ds)
    if key in _CON and not fresh:
        return _CON[key]
    CACHE_DIR.mkdir(exist_ok=True)
    path = CACHE_DIR / f"truth_{ds}.duckdb"
    if fresh and path.exists():
        path.unlink()
    con = ingest.connect(path)
    have = con.execute("SELECT count(*) FROM wells WHERE dataset_id=?", [ds]).fetchone()[0]
    if not have:
        ingest.ingest(data_dir, ds, con)
    else:
        from wellops.substrate import views
        views.install(con)
    con.execute("SET TimeZone='UTC'")
    _CON[key] = con
    return con


def q(con, sql: str, *args):
    return con.execute(sql, list(args)).fetchall()


_N = 0


@contextmanager
def overlays(con, items: list[dict], ds: str = DS):
    """Temporarily apply assertions (scenario state) and remove them afterwards."""
    global _N
    ids = []
    try:
        for o in items or []:
            _N += 1
            aid = f"evals-tmp-{_N}"
            ids.append(aid)
            con.execute(
                "INSERT INTO assertions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?::TIMESTAMPTZ,?,?,?)",
                [aid, ds, o["kind"], o["scope_level"], o["scope_key"], o.get("target_attr"), o.get("value_num"),
                 o.get("value_text"), o.get("original_num"), o.get("author", "U-201"), o.get("reason"), "chat",
                 "evals", None, None, o.get("tx_time", "2026-10-17 10:00:00+00"), o.get("status", "confirmed"),
                 "fleet", None])
        yield
    finally:
        for aid in ids:
            con.execute("DELETE FROM assertions WHERE assertion_id=?", [aid])
