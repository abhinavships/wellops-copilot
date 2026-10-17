"""Documents and open SQL: read_doc, search_docs, search_reports, sql (read-only, capped, SELECT/WITH only)."""

from __future__ import annotations

import re

from wellops.contracts import Citation, RunDeps

from .core import ROW_CAP, Db, ToolError, ToolOut, ds, finish, get_db, lit, query_result

CATALOG = """Canonical relations (use these, not raw tables, for derived facts). Table macros take (dataset, as-of):
 ins_eff(ds, at)      inspections + overlays: wellhead_eff, ambient_eff, volume_eff, delta_c, delta_min_c, producing, spill_real, *_overlay
 latest_status(ds,at) latest inspection per well + recorded_status, recorded_status_stale
 spills_real(ds, at)  real spills + spill_class, lat, lon
 params(ds, at)       effective rule parameter per well (asset_id, param, value, source)
 context_for(ds, at)  notes + shared facts/claims (asset_id, ref, kind, author_user_id, text, valid_from, valid_to, inspection_ref, status)
 repairs(ds, at)      maintenance + open_as_of
Raw tables (filter dataset_id): wells areas users equipment inspections maintenance reg_events notes reports report_facts docs
 rules assertions dq_flags. Placeholders {ds} and {asof} (a TIMESTAMPTZ literal) are substituted for you.
Downtime = 7 days x inspections where NOT producing. Never use maintenance.downtime_hours."""

FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|detach|copy|pragma|install|load|call|set|export|"
                       r"import|truncate|replace|vacuum|checkpoint|read_csv|read_json|read_parquet|read_text|read_blob|"
                       r"glob|episodes|handles|duckdb_\w+|pragma_\w+|sniff_csv)\b", re.I)


def guard(query: str) -> str:
    q = re.sub(r"--[^\n]*|/\*.*?\*/", " ", query, flags=re.S).strip().rstrip(";").strip()
    bare = re.sub(r"'(?:[^']|'')*'", "''", q)
    if ";" in bare:
        raise ToolError("one statement only")
    if not re.match(r"(?is)^(select|with)\b", q):
        raise ToolError("only SELECT/WITH statements are allowed")
    if (m := FORBIDDEN.search(bare)):
        raise ToolError(f"keyword/table not allowed in read-only sql: {m.group(0)}")
    return q


def sql(deps: RunDeps, query: str, shape: str = "entity_list") -> ToolOut:
    """Read-only SQL over the canonical macros and raw tables. Result capped at 2000 rows."""
    db = get_db()
    q = guard(query.replace("{ds}", ds(deps)).replace("{asof}", f"TIMESTAMPTZ '{deps.asof}'"))
    try:
        cols, rows = db.q(f"SELECT * FROM ({q}) LIMIT {ROW_CAP + 1}")
    except Exception as e:  # surface SQL errors to the model so it can fix the query
        raise ToolError(f"SQL error: {str(e)[:300]}") from e
    trunc = len(rows) > ROW_CAP
    return finish(db, deps, shape, cols, rows[:ROW_CAP], sql=q, truncated=trunc,
                  notes=[f"row cap {ROW_CAP}" + (" - TRUNCATED" if trunc else ""), "open SQL: model-written"])


def read_doc(deps: RunDeps, doc_id: str, section: str | None = None) -> ToolOut:
    """Read a manual/SOP section (prefix match: '9' returns 9, 9.1, 9.3). Without section: list sections."""
    db = get_db()
    d = ds(deps)
    if section:
        sec = section.lstrip("§").strip()
        sql_ = (f"SELECT doc_id, section, title, text FROM docs WHERE dataset_id={d} AND doc_id={lit(doc_id)} AND "
                f"(section={lit(sec)} OR section LIKE {lit(sec + '.%')} OR lower(title)=lower({lit(sec)})) ORDER BY section")
    else:
        sql_ = (f"SELECT doc_id, section, title, length(text) AS chars FROM docs WHERE dataset_id={d} AND "
                f"doc_id={lit(doc_id)} ORDER BY section")
    out = query_result(db, deps, "text", sql_, ["section text is verbatim from the source document"])
    if not out.row_count:
        ids = [r["doc_id"] for r in db.rows(f"SELECT DISTINCT doc_id FROM docs WHERE dataset_id={d}")]
        raise ToolError(f"no such doc/section; docs: {ids}")
    out.citations += [Citation(kind="doc", id=f"{r['doc_id']} §{r['section']}", label=r["title"]) for r in out.preview]
    return out


def _terms(q: str) -> list[str]:
    return [t for t in re.findall(r"[\w§.-]+", q.lower()) if len(t) > 1][:8]


def _snippet(col: str, t: str) -> str:
    return f"substr({col}, greatest(1, position({lit(t)} IN lower({col})) - 120), 320)"


def search_docs(deps: RunDeps, query: str, doc_id: str | None = None, limit: int = 8) -> ToolOut:
    """Keyword search (all terms, case-insensitive) over manuals and SOPs. Returns snippets with section ids."""
    db = get_db()
    ts = _terms(query)
    if not ts:
        raise ToolError("empty query")
    cond = " AND ".join(f"lower(text) LIKE {lit('%' + t + '%')}" for t in ts)
    sql_ = (f"SELECT doc_id, section, title, {_snippet('text', ts[0])} AS snippet FROM docs WHERE dataset_id={ds(deps)} "
            f"AND {cond}" + (f" AND doc_id={lit(doc_id)}" if doc_id else "") + f" ORDER BY doc_id, section LIMIT {int(limit)}")
    out = query_result(db, deps, "text", sql_, ["ILIKE keyword search (all terms must appear); read_doc for full text"])
    out.citations += [Citation(kind="doc", id=f"{r['doc_id']} §{r['section']}") for r in out.preview]
    return out


def search_reports(deps: RunDeps, query: str, asset_id: str | None = None, limit: int = 8) -> ToolOut:
    """Keyword search over maintenance report text. Returns report snippets with maintenance ids."""
    db = get_db()
    ts = _terms(query)
    if not ts:
        raise ToolError("empty query")
    cond = " AND ".join(f"lower(r.text) LIKE {lit('%' + t + '%')}" for t in ts)
    sql_ = (f"SELECT r.maintenance_id, r.asset_id, m.started_at::DATE AS started, m.issue, r.path, "
            f"position({lit(ts[0])} IN lower(r.text)) AS span_start, {_snippet('r.text', ts[0])} AS snippet "
            f"FROM reports r JOIN maintenance m ON m.dataset_id=r.dataset_id AND m.maintenance_id=r.maintenance_id "
            f"WHERE r.dataset_id={ds(deps)} AND {cond}" + (f" AND r.asset_id={lit(asset_id)}" if asset_id else "")
            + f" ORDER BY m.started_at DESC LIMIT {int(limit)}")
    return query_result(db, deps, "text", sql_, ["ILIKE keyword search (all terms must appear), newest first"])
