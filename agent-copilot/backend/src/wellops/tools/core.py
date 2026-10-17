"""Shared plumbing for tools: DB access, SQL literals, scopes, handle store, ToolOut construction."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import duckdb
from pydantic import BaseModel

from wellops.contracts import Citation, Conflict, DQFlag, RunDeps, ToolResult
from wellops.settings import settings

PREVIEW_ROWS = 15
ROW_CAP = 2000
ID_RE = re.compile(r"\b(?:INS-\d{4}-\d{4}|MNT-\d{4}-\d{3}|REG-\d{4}-\d{3}|CTX-\d{4}-\d{3})\b")
WELL_RE = re.compile(r"\bWELL-\d{4}\b")


class ToolOut(ToolResult):
    """ToolResult plus conflicts found while building it (contract extension; the verifier reads these)."""

    conflicts: list[Conflict] = []
    related_handles: list[str] = []
    rows_truncated: bool = False


class ToolError(ValueError):
    """Bad arguments from the model; the message is safe to show it."""


def lit(v: Any) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def in_list(vals) -> str:
    vals = list(vals)
    return "(" + (",".join(lit(v) for v in vals) if vals else "NULL") + ")"


def jsonable(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat(sep=" ").replace("+00:00", "Z") if v.tzinfo else v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (list, tuple)):
        return [jsonable(x) for x in v]
    if isinstance(v, dict):
        return {k: jsonable(x) for k, x in v.items()}
    return v


class Db:
    """One DuckDB file, one process. Cursors are cheap; a lock serialises use (DuckDB is single-writer)."""

    def __init__(self, path: Path | str | None = None):
        from wellops.substrate import ingest, views

        self.path = str(path or settings.db_path)
        self.con = ingest.connect(Path(self.path))
        self.con.execute("SET TimeZone='UTC'")
        views.install(self.con)
        self.con.execute("""CREATE TABLE IF NOT EXISTS handles (chat_id VARCHAR, handle VARCHAR, result VARCHAR,
                            rows VARCHAR, PRIMARY KEY (chat_id, handle))""")
        self.lock = threading.RLock()

    def q(self, sql: str, params: list | None = None) -> tuple[list[str], list[dict[str, Any]]]:
        with self.lock:
            cur = self.con.execute(sql, params or [])
            cols = [d[0] for d in cur.description] if cur.description else []
            return cols, [{c: jsonable(v) for c, v in zip(cols, r)} for r in cur.fetchall()]

    def rows(self, sql: str, params: list | None = None) -> list[dict[str, Any]]:
        return self.q(sql, params)[1]

    def run(self, sql: str, params: list | None = None) -> None:
        with self.lock:
            self.con.execute(sql, params or [])


_DB: Db | None = None


def get_db() -> Db:
    global _DB
    if _DB is None:
        _DB = Db()
    return _DB


def set_db(db: Db | None) -> None:
    global _DB
    _DB = db


# --- run context helpers -------------------------------------------------------------------------

def ds(deps: RunDeps) -> str:
    return lit(deps.active_dataset)


def at(deps: RunDeps) -> str:
    return f"TIMESTAMPTZ '{deps.asof}'"


def mac(deps: RunDeps, name: str) -> str:
    return f"{name}({ds(deps)}, {at(deps)})"


def parse_date(v: date | str | None, default: date | None = None) -> date | None:
    if v is None or v == "":
        return default
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError as e:
        raise ToolError(f"bad date {v!r}; use YYYY-MM-DD (resolve_entity can parse relative dates)") from e


def window(deps: RunDeps, start, end) -> tuple[date | None, date]:
    """Inclusive date window; the end is clipped at the run's today (as-of)."""
    e = min(parse_date(end, deps.today), deps.today)
    return parse_date(start), e


def win_pred(col: str, start: date | None, end: date) -> str:
    p = f"{col}::DATE <= DATE '{end}'"
    return p + (f" AND {col}::DATE >= DATE '{start}'" if start else "")


class Scope(BaseModel):
    kind: Literal["wells", "area", "well_type", "all"] = "all"
    ids: list[str] = []  # well ids, area ids or names, or well types


def coerce_scope(db: Db, deps: RunDeps, scope: Scope | dict | str | list | None) -> Scope:
    if scope is None:
        return Scope()
    if isinstance(scope, dict):
        return Scope(**scope)
    if isinstance(scope, Scope):
        return scope
    items = [scope] if isinstance(scope, str) else list(scope)
    if len(items) == 1 and items[0].lower() == "all":
        return Scope()
    wells = [i.upper() for i in items if WELL_RE.fullmatch(i.upper())]
    if len(wells) == len(items):
        return Scope(kind="wells", ids=wells)
    types = [i.upper() for i in items if re.fullmatch(r"T\d{3}", i.upper())]
    if len(types) == len(items):
        return Scope(kind="well_type", ids=types)
    return Scope(kind="area", ids=items)


def _akey(s: str) -> str:
    return re.sub(r"(?<=\D)0+(?=\d)", "", re.sub(r"[^a-z0-9]", "", s.lower()))


def area_ids(db: Db, deps: RunDeps, names: list[str]) -> list[str]:
    rows = db.rows("SELECT area_id, name FROM areas WHERE dataset_id=?", [deps.active_dataset])
    out = []
    for n in names:
        hit = [r["area_id"] for r in rows if _akey(n) in (_akey(r["name"]), _akey(r["area_id"]))]
        if not hit:
            raise ToolError(f"unknown area {n!r}; known: {[r['name'] for r in rows]}")
        out += hit
    return out


def well_pred(db: Db, deps: RunDeps, scope, col: str = "asset_id") -> str:
    """SQL predicate restricting `col` to the wells of a scope."""
    s = coerce_scope(db, deps, scope)
    if s.kind == "all":
        return "TRUE"
    if s.kind == "wells":
        return f"{col} IN {in_list(i.upper() for i in s.ids)}"
    base = f"SELECT asset_id FROM wells WHERE dataset_id={ds(deps)}"
    if s.kind == "area":
        return f"{col} IN ({base} AND area_id IN {in_list(area_ids(db, deps, s.ids))})"
    return f"{col} IN ({base} AND well_type IN {in_list(i.upper() for i in s.ids)})"


def scope_text(scope) -> str:
    s = scope if isinstance(scope, Scope) else Scope()
    return "all wells" if s.kind == "all" else f"{s.kind} {', '.join(s.ids)}"


# --- handle store --------------------------------------------------------------------------------

@dataclass
class Stored:
    result: ToolOut
    rows: list[dict[str, Any]]


class HandleStore:
    """Full rows per (chat, handle): in-process dict, mirrored to the `handles` table for restarts."""

    def __init__(self, db: Db):
        self.db = db
        self.mem: dict[tuple[str, str], Stored] = {}
        self.counters: dict[str, int] = {}

    def put(self, chat_id: str, result: ToolOut, rows: list[dict]) -> str:
        if chat_id not in self.counters:
            self.counters[chat_id] = self.db.rows("SELECT count(*) n FROM handles WHERE chat_id=?", [chat_id])[0]["n"]
        self.counters[chat_id] += 1
        h = f"r{self.counters[chat_id]}"
        result.handle = h
        self.mem[(chat_id, h)] = Stored(result, rows)
        self.db.run("INSERT OR REPLACE INTO handles VALUES (?,?,?,?)",
                    [chat_id, h, result.model_dump_json(), json.dumps(rows, default=str)])
        return h

    def get(self, chat_id: str, handle: str) -> Stored | None:
        if (chat_id, handle) in self.mem:
            return self.mem[(chat_id, handle)]
        r = self.db.rows("SELECT result, rows FROM handles WHERE chat_id=? AND handle=?", [chat_id, handle])
        if not r:
            return None
        st = Stored(ToolOut.model_validate_json(r[0]["result"]), json.loads(r[0]["rows"]))
        self.mem[(chat_id, handle)] = st
        return st

    def forget(self) -> None:
        self.mem.clear()


_STORES: dict[int, HandleStore] = {}


def store(db: Db | None = None) -> HandleStore:
    db = db or get_db()
    return _STORES.setdefault(id(db), HandleStore(db))


# --- result building -----------------------------------------------------------------------------

CITE_COLS = {"inspection_id": "inspection", "maintenance_id": "maintenance", "event_id": "reg_event",
             "note_id": "note", "ref": "note"}
KIND_BY_PREFIX = {"INS": "inspection", "MNT": "maintenance", "REG": "reg_event", "CTX": "note"}


def cite_kind(i: str) -> str:
    return KIND_BY_PREFIX.get(i[:3], "assertion" if i.startswith(("A-", "sample:", "test:")) or ":" in i else "well")


def harvest(rows: list[dict], extra_text: list[str] = ()) -> tuple[list[Citation], list[str], set[str]]:
    """Pull citations, applied overlay ids and entity ids out of rows (columns by naming convention)."""
    cites: dict[str, Citation] = {}
    overlays: list[str] = []
    ents: set[str] = set()
    for r in rows:
        for k, v in r.items():
            if v is None:
                continue
            if k.endswith("overlay") or k in ("assertion_id", "detection_correction", "threshold_overlay"):
                if isinstance(v, str) and v not in overlays:
                    overlays.append(v)
                if isinstance(v, str):
                    cites.setdefault(v, Citation(kind="assertion", id=v))
            if isinstance(v, str):
                for m in ID_RE.findall(v):
                    cites.setdefault(m, Citation(kind=cite_kind(m), id=m))
                    ents.add(m)
                for m in WELL_RE.findall(v):
                    ents.add(m)
            elif isinstance(v, list):
                for s in v:
                    if isinstance(s, (str, dict)):
                        t = json.dumps(s) if isinstance(s, dict) else s
                        for m in ID_RE.findall(t):
                            cites.setdefault(m, Citation(kind=cite_kind(m), id=m))
                            ents.add(m)
    for t in extra_text:
        for m in ID_RE.findall(t):
            cites.setdefault(m, Citation(kind=cite_kind(m), id=m))
    return list(cites.values()), overlays, ents


def dq_for(db: Db, deps: RunDeps, ents: set[str]) -> list[DQFlag]:
    if not ents:
        return []
    ents = set(sorted(ents)[:500])
    rows = db.rows(f"SELECT entity_id, kind, detail FROM dq_flags WHERE dataset_id=? AND entity_id IN {in_list(ents)}",
                   [deps.active_dataset])
    return [DQFlag(**r) for r in rows]


def finish(db: Db, deps: RunDeps, shape: str, columns: list[str], rows: list[dict], *, sql: str | None,
           notes: list[str], citations: list[Citation] | None = None, conflicts: list[Conflict] | None = None,
           overlays: list[str] | None = None, truncated: bool = False) -> ToolOut:
    cites, ov, ents = harvest(rows)
    ents |= {r["asset_id"] for r in rows if isinstance(r.get("asset_id"), str)}
    seen = {(c.id, c.span) for c in cites}
    for c in citations or []:
        if (c.id, c.span) not in seen:
            cites.append(c)
            seen.add((c.id, c.span))
    for o in overlays or []:
        if o not in ov:
            ov.append(o)
    out = ToolOut(handle="", shape=shape, columns=columns, preview=rows[:PREVIEW_ROWS], row_count=len(rows), sql=sql,
                  citations=cites, dq_flags=dq_for(db, deps, ents), applied_overlays=ov, conflicts=conflicts or [],
                  notes=[f"dataset={deps.active_dataset}", f"as-of {deps.asof}", *notes], rows_truncated=truncated)
    store(db).put(deps.chat_id, out, rows)
    return out


def query_result(db: Db, deps: RunDeps, shape: str, sql: str, notes: list[str], **kw) -> ToolOut:
    cols, rows = db.q(sql)
    return finish(db, deps, shape, cols, rows, sql=sql, notes=notes, **kw)


def month_add(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    import calendar
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def user_names(db: Db, deps: RunDeps) -> dict[str, str]:
    return {r["user_id"]: r["name"] for r in db.rows("SELECT user_id, name FROM users WHERE dataset_id=?",
                                                       [deps.active_dataset])}


__all__ = [n for n in dir() if not n.startswith("__")]
