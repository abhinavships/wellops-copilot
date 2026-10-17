"""The knowledge index in DuckDB: `kb_chunks` + an FTS (BM25) index, `kb_vectors` (dense, optional), hybrid retrieval.

reindex() diffs freshly built chunks against the table (by chunk id + sha1), so a new note or upload touches only its
own rows; the FTS index is rebuilt only when something changed (it is a static snapshot, ~1 s for the sample fleet).
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import date, datetime
from typing import Any

import duckdb

from wellops.settings import settings

from . import embed as E
from .chunk import Chunk, all_chunks

RRF_K = 60  # reciprocal-rank fusion constant (Cormack et al.)
POOL = 60  # candidates per ranker before fusion
KINDS = ("doc", "report", "note", "media")

DDL = """
CREATE TABLE IF NOT EXISTS kb_chunks (cid VARCHAR PRIMARY KEY, dataset_id VARCHAR, kind VARCHAR, src_id VARCHAR,
    cite_kind VARCHAR, title VARCHAR, section VARCHAR, text VARCHAR, span_start INTEGER, span_end INTEGER,
    asset_id VARCHAR, author VARCHAR, ts TIMESTAMPTZ, sha VARCHAR, rev VARCHAR);
CREATE TABLE IF NOT EXISTS kb_vectors (sha VARCHAR, model VARCHAR, vec FLOAT[], PRIMARY KEY (sha, model));
"""
COLS = list(Chunk.__dataclass_fields__)


def _con(db) -> tuple[duckdb.DuckDBPyConnection, Any]:
    """Accept a wellops Db (use its lock) or a bare DuckDB connection (ingest)."""
    return (db.con, db.lock) if hasattr(db, "lock") else (db, contextlib.nullcontext())


def _fts(con) -> bool:
    try:
        con.execute("LOAD fts")
    except duckdb.Error:
        try:
            con.execute("INSTALL fts")
            con.execute("LOAD fts")
        except duckdb.Error:
            return False
    return True


def _has_fts_index(con) -> bool:
    return bool(con.execute("SELECT count(*) FROM duckdb_schemas() WHERE schema_name='fts_main_kb_chunks'").fetchone()[0])


def _bulk(con, table: str, rows: list[dict], cols: str) -> None:
    """Insert many rows through a temp JSON file (DuckDB executemany is slow for thousands of rows)."""
    if not rows:
        return
    fd, p = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as f:
        json.dump(rows, f, default=str)
    try:
        con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM read_json(?, format='array', columns={cols})", [p])
    finally:
        os.unlink(p)


CHUNK_COLS = ("{cid:'VARCHAR', dataset_id:'VARCHAR', kind:'VARCHAR', src_id:'VARCHAR', cite_kind:'VARCHAR', title:'VARCHAR', "
              "section:'VARCHAR', text:'VARCHAR', span_start:'INTEGER', span_end:'INTEGER', asset_id:'VARCHAR', "
              "author:'VARCHAR', ts:'TIMESTAMPTZ', sha:'VARCHAR', rev:'VARCHAR'}")
VEC_COLS = "{sha:'VARCHAR', model:'VARCHAR', vec:'FLOAT[]'}"


def reindex(db, ds: str | None = None, embed: bool | None = None) -> dict:
    """Bring kb_chunks + the FTS index up to date with docs/reports/notes/media; embed the chunks this call added.

    embed=None follows settings.rag_embed. Backfilling older chunks is embed_missing() (CLI: python -m wellops.rag.index)."""
    ds = ds or settings.dataset_id
    con, lock = _con(db)
    with lock:
        con.execute(DDL)
        fresh = {c.cid: c for c in all_chunks(con, ds)}
        have = dict(con.execute("SELECT cid, rev FROM kb_chunks WHERE dataset_id=?", [ds]).fetchall())
        gone = [c for c, r in have.items() if c not in fresh or fresh[c].rev != r]
        new = [{**c.row(), "rev": c.rev} for cid, c in fresh.items() if have.get(cid) != c.rev]
        if gone:
            con.execute("CREATE OR REPLACE TEMP TABLE _kb_gone (cid VARCHAR)")
            _bulk(con, "_kb_gone", [{"cid": c} for c in gone], "{cid:'VARCHAR'}")
            con.execute("DELETE FROM kb_chunks WHERE cid IN (SELECT cid FROM _kb_gone)")
            con.execute("DROP TABLE _kb_gone")
        _bulk(con, "kb_chunks", new, CHUNK_COLS)
        fts = _fts(con)
        rebuilt = bool(fts and (gone or new or not _has_fts_index(con)))
        if rebuilt:
            con.execute("PRAGMA create_fts_index('kb_chunks', 'cid', 'title', 'text', stemmer='porter', "
                        "stopwords='english', ignore='(\\.|[^a-z0-9])+', strip_accents=1, lower=1, overwrite=1)")  # keep digits: H2S, SR-01
    stats = dict(dataset_id=ds, chunks=len(fresh), added=len(new), removed=len([c for c in gone if c not in fresh]),
                 fts=fts, fts_rebuilt=rebuilt)
    want = settings.rag_embed if embed is None else embed
    stats.update(embed_missing(db, ds, shas={r["sha"] for r in new}) if want and new else dict(embedded=0))
    return stats


def embed_missing(db, ds: str, limit: int | None = None, shas: set[str] | None = None) -> dict:
    """Embed chunk texts with no cached vector yet (distinct sha only, docs/media/notes before reports, capped).

    `shas` restricts the work to those texts (an upload embeds only what it added)."""
    con, lock = _con(db)
    if not E.available():
        return dict(embedded=0, embed_skipped="dense layer off (no GEMINI_API_KEY, RAG_EMBED=false, or cooling down)")
    model, cap = E.model_key(), settings.rag_embed_max if limit is None else limit
    with lock:
        todo = con.execute("""SELECT sha, any_value(text ORDER BY ts DESC NULLS LAST) AS text,
                                     min(CASE kind WHEN 'doc' THEN 0 WHEN 'media' THEN 1 WHEN 'note' THEN 2 ELSE 3 END) AS pr,
                                     max(ts) AS ts
                              FROM kb_chunks c WHERE dataset_id=? AND NOT EXISTS
                                (SELECT 1 FROM kb_vectors v WHERE v.sha=c.sha AND v.model=?)
                                AND (? OR sha IN (SELECT unnest(?::VARCHAR[])))
                              GROUP BY sha ORDER BY pr, ts DESC NULLS LAST""",
                           [ds, model, shas is None, sorted(shas or [])]).fetchall()
    left = len(todo) - min(len(todo), cap)
    done, err = 0, None
    for i in range(0, min(len(todo), cap), 500):  # commit every 500 so a mid-run failure keeps what was paid for
        part = todo[i:min(i + 500, cap)]
        try:
            vecs = E.embed([t for _, t, _, _ in part])
        except E.EmbedError as e:
            err = str(e)
            break
        with lock:
            _bulk(con, "kb_vectors", [dict(sha=s, model=model, vec=v) for (s, *_), v in zip(part, vecs)], VEC_COLS)
        done += len(part)
    out: dict[str, Any] = dict(embedded=done, embed_pending=len(todo) - done)
    if err:
        out["embed_error"] = err
    elif left:
        out["embed_capped"] = f"{left} chunk texts wait for the next reindex (rag_embed_max={cap})"
    return out


# --- retrieval -----------------------------------------------------------------------------------

def _filters(ds: str, asset_id: str | None, kind: str | None, start: date | None, end: date | None,
             asof: str | None) -> tuple[str, list]:
    w, p = ["c.dataset_id=?"], [ds]
    if kind:
        w.append("c.kind=?")
        p.append(kind)
    if asset_id:  # a well's own records; manuals/SOPs apply fleet-wide so they stay eligible
        w.append("(c.asset_id=? OR c.kind='doc')")
        p.append(asset_id)
    if start:
        w.append("(c.ts IS NULL OR c.ts::DATE >= ?)")
        p.append(start)
    if end:
        w.append("(c.ts IS NULL OR c.ts::DATE <= ?)")
        p.append(end)
    if asof:  # records written after the run's clock do not exist yet (uploads/docs are timeless)
        w.append("(c.ts IS NULL OR c.kind IN ('doc','media') OR c.ts <= ?::TIMESTAMPTZ)")
        p.append(asof)
    return " AND ".join(w), p


# one candidate per distinct text (newest copy), so 200 identical templated notes cannot crowd out the pool
def _bm25(con, query: str, where: str, params: list) -> list[tuple[str, float]]:
    if not _has_fts_index(con):
        return []
    return con.execute(f"""SELECT cid, score FROM (SELECT c.cid, c.ts, c.sha, fts_main_kb_chunks.match_bm25(c.cid, ?) AS score
                           FROM kb_chunks c WHERE {where}) WHERE score IS NOT NULL
                           QUALIFY row_number() OVER (PARTITION BY sha ORDER BY ts DESC NULLS LAST) = 1
                           ORDER BY score DESC, ts DESC NULLS LAST LIMIT {POOL}""", [query, *params]).fetchall()


def _dense(con, qv: list[float], where: str, params: list) -> list[tuple[str, float]]:
    return con.execute(f"""SELECT c.cid, list_cosine_similarity(v.vec, ?::FLOAT[]) AS score
                           FROM kb_chunks c JOIN kb_vectors v ON v.sha=c.sha AND v.model=?
                           WHERE {where} QUALIFY row_number() OVER (PARTITION BY c.sha ORDER BY c.ts DESC NULLS LAST) = 1
                           ORDER BY score DESC, c.ts DESC NULLS LAST LIMIT {POOL}""",
                       [qv, E.model_key(), *params]).fetchall()


def retrieve(db, ds: str, query: str, k: int = 6, asset_id: str | None = None, kind: str | None = None,
             start: date | None = None, end: date | None = None, asof: str | None = None) -> tuple[list[dict], list[str]]:
    """Hybrid search: BM25 and cosine rankings fused by RRF; identical passages collapse onto the newest one."""
    con, lock = _con(db)
    where, params = _filters(ds, asset_id, kind, start, end, asof)
    notes: list[str] = []
    with lock:
        _fts(con)
        lex = _bm25(con, query, where, params)
        has_vec = con.execute("SELECT count(*) FROM kb_vectors WHERE model=?", [E.model_key()]).fetchone()[0] > 0
    sem: list[tuple[str, float]] = []
    if has_vec and E.available():
        try:
            qv = E.embed_query(query)
            with lock:
                sem = _dense(con, qv, where, params)
        except E.EmbedError as e:
            notes.append(f"dense layer unavailable ({e}); BM25 only")
    elif not has_vec:
        notes.append("no cached embeddings yet; BM25 only")
    notes.append(f"ranking: {'BM25 + dense cosine, reciprocal-rank fusion (k=60)' if sem else 'BM25 (porter stemmer)'}")
    fused: dict[str, float] = {}
    for ranking in (lex, sem):
        for r, (cid, _) in enumerate(ranking):
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + r + 1)
    if not fused:
        return [], notes
    lexs, sems = dict(lex), dict(sem)
    with lock:
        cur = con.execute(f"SELECT {', '.join(COLS)} FROM kb_chunks WHERE cid IN (SELECT unnest(?::VARCHAR[]))", [list(fused)])
        rows = [dict(zip(COLS, r)) for r in cur.fetchall()]
    ts_key = lambda r: r["ts"].timestamp() if isinstance(r["ts"], datetime) else 0.0  # noqa: E731
    rows.sort(key=lambda r: (-fused[r["cid"]], -ts_key(r)))
    out: list[dict] = []
    for r in rows:  # same words elsewhere (templated notes/reports) collapse onto the newest copy
        if len(out) < k and all(o["sha"] != r["sha"] for o in out):
            out.append({**r, "rrf": fused[r["cid"]], "bm25": lexs.get(r["cid"]), "cosine": sems.get(r["cid"])})
    with lock:
        dup = dict(con.execute(f"""SELECT c.sha, count(*) - 1 FROM kb_chunks c WHERE {where}
                                   AND c.sha IN (SELECT unnest(?::VARCHAR[])) GROUP BY c.sha""",
                               [*params, [o["sha"] for o in out]]).fetchall())
    for o in out:
        o["same_text"] = dup.get(o["sha"], 0)
    return out, notes


def main() -> None:
    """`python -m wellops.rag.index [dataset]`: (re)build the index and embed everything not yet embedded."""
    import sys

    from wellops.substrate import ingest
    ds = sys.argv[1] if len(sys.argv) > 1 else settings.dataset_id
    con = ingest.connect()
    print(json.dumps({**reindex(con, ds, embed=False), **embed_missing(con, ds)}, indent=2))


if __name__ == "__main__":
    main()
