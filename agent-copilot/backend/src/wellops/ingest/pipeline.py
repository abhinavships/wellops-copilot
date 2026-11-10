"""Ingest any user file at runtime: tables, documents, images.

- Tables (CSV/TSV/XLSX/JSON/JSONL) are profiled. If their columns match a known table (inspections, maintenance,
  wells, reg_events, notes) they are appended under dataset 'sample' (rows whose id already exists are skipped).
  Otherwise they become a new table `user_<name>` that the read-only `sql` tool can query (listed in `user_tables`).
  Data-quality flags are recomputed afterwards.
- Documents (MD/TXT/PDF) are split into sections in `docs` (doc_id UP-<name>), so search_docs/read_doc find them.
  A well-type manual also gets its rules extracted (ingest.manual_rules, verbatim quotes), so they apply at once.
- Images are stored under backend/uploads/ and described once by a free OpenRouter vision model (`media` table).
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import httpx
from pydantic import BaseModel

from wellops.settings import ROOT, settings
from wellops.substrate.ingest import manual_rules, split_sections
from wellops.tools.core import Db, get_db, jsonable

DS = settings.active_dataset  # uploads join the dataset being served
UPLOADS = ROOT / "backend" / "uploads"
WELL_RE = re.compile(r"WELL-\d{4}", re.I)
TABLE_EXT = {".csv", ".tsv", ".xlsx", ".json", ".jsonl", ".ndjson"}
DOC_EXT = {".md", ".markdown", ".txt", ".pdf"}
IMAGE_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
# a file maps to a known table only if it carries that table's key columns and most of its columns are known
KNOWN = {"inspections": {"inspection_id", "asset_id", "inspected_at"}, "maintenance": {"maintenance_id", "asset_id"},
         "wells": {"asset_id", "well_type"}, "reg_events": {"event_id", "asset_id"}, "notes": {"note_id", "text"}}
ID_COL = {"inspections": "inspection_id", "maintenance": "maintenance_id", "wells": "asset_id",
          "reg_events": "event_id", "notes": "note_id"}
MIN_OVERLAP = 0.6
DQ_OWN = {"recorded_status_stale", "hot_idle", "borderline_delta", "inspection_gap", "repair_in_progress",
          "nonstandard_report_filename", "inspection_ref_only_in_text"}  # kinds dq.compute rewrites

DDL = """
CREATE TABLE IF NOT EXISTS user_tables (name VARCHAR PRIMARY KEY, source_file VARCHAR, columns VARCHAR, row_count INTEGER,
    principal VARCHAR, created_at TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS media (media_id VARCHAR PRIMARY KEY, dataset_id VARCHAR, path VARCHAR, filename VARCHAR,
    sha1 VARCHAR, caption VARCHAR, text VARCHAR, tags VARCHAR[], asset_id VARCHAR, model VARCHAR, principal VARCHAR,
    created_at TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS ingest_log (ingest_id VARCHAR PRIMARY KEY, ts TIMESTAMPTZ, principal VARCHAR, chat_id VARCHAR,
    file VARCHAR, kind VARCHAR, report VARCHAR);
"""


class IngestReport(BaseModel):
    file: str
    kind: str  # table | known_table | document | manual | image | unsupported
    tables: list[str] = []
    rows: int = 0
    docs: int = 0
    images: int = 0
    summary: str = ""
    preview: list[dict] = []
    warnings: list[str] = []


def ensure_schema(db: Db) -> None:
    with db.lock:
        db.con.execute(DDL)


def _rel(p: Path) -> str:
    """Path stored in the DB: relative to the repo when inside it (readers join with ROOT; absolute paths survive the join)."""
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p.resolve())


def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", Path(name).stem.lower()).strip("_")
    return (s if s and not s[0].isdigit() else f"f_{s}")[:40]


def ingest_file(path: str | Path, filename: str | None = None, principal: str = "U-201", chat_id: str | None = None,
                db: Db | None = None) -> IngestReport:
    db = db or get_db()
    ensure_schema(db)
    path = Path(path)
    filename = filename or path.name
    ext = Path(filename).suffix.lower()
    try:
        if ext in TABLE_EXT:
            rep = _ingest_table(db, path, filename, ext, principal)
        elif ext in DOC_EXT:
            rep = _ingest_doc(db, path, filename, ext)
        elif ext in IMAGE_EXT:
            rep = _ingest_image(db, path, filename, ext, principal)
        else:
            rep = IngestReport(file=filename, kind="unsupported", summary=f"Unsupported file type {ext or '(none)'}",
                               warnings=[f"supported: {sorted(TABLE_EXT | DOC_EXT | set(IMAGE_EXT))}"])
    except Exception as e:  # a bad upload is a report, never a crash
        rep = IngestReport(file=filename, kind="error", summary=f"Could not ingest {filename}", warnings=[f"{type(e).__name__}: {e}"[:400]])
    if rep.kind in ("known_table", "document", "manual", "image"):  # new docs/notes/captions become searchable now
        try:
            from wellops import rag
            rag.reindex(db, DS)
        except Exception as e:  # the upload itself succeeded; search catches up on the next reindex
            rep.warnings.append(f"knowledge index not refreshed: {type(e).__name__}: {e}"[:300])
    db.run("INSERT INTO ingest_log VALUES (?,?,?,?,?,?,?)", [uuid.uuid4().hex[:12], datetime.now(timezone.utc), principal,
                                                            chat_id, filename, rep.kind, rep.model_dump_json()])
    return rep


def history(db: Db | None = None, limit: int = 50) -> list[dict]:
    db = db or get_db()
    ensure_schema(db)
    rows = db.rows("SELECT ingest_id, ts, principal, chat_id, file, kind, report FROM ingest_log ORDER BY ts DESC LIMIT ?", [limit])
    for r in rows:
        r["report"] = json.loads(r["report"])
    return rows


def user_tables_catalog(db: Db | None = None) -> str:
    """Catalog lines for runtime tables, to append to the sql tool's catalog."""
    db = db or get_db()
    ensure_schema(db)
    rows = db.rows("SELECT name, columns, row_count, source_file FROM user_tables ORDER BY name")
    return "\n".join(f" {r['name']}({r['columns']})  {r['row_count']} rows from {r['source_file']}" for r in rows)


# ---------------------------------------------------------------- tables

def _read_rows(path: Path, ext: str) -> list[dict[str, Any]]:
    raw = path.read_bytes()
    if ext in (".csv", ".tsv"):
        text = raw.decode("utf-8-sig", errors="replace")
        delim = "\t" if ext == ".tsv" else csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
        return [dict(r) for r in csv.DictReader(io.StringIO(text), delimiter=delim)]
    if ext in (".jsonl", ".ndjson"):
        return [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    if ext == ".json":
        j = json.loads(raw)
        if isinstance(j, dict):  # {"items": [...]} or a single record
            lists = [v for v in j.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
            j = lists[0] if lists else [j]
        return [r for r in j if isinstance(r, dict)]
    if ext == ".xlsx":
        return _read_xlsx(raw)
    raise ValueError(ext)


def _read_xlsx(raw: bytes) -> list[dict[str, Any]]:
    """First sheet of an .xlsx with the stdlib (no openpyxl): shared strings + inline values, first row = header."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    z = zipfile.ZipFile(io.BytesIO(raw))
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        shared = ["".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t"))
                  for si in ElementTree.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns)]
    sheet = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet"))[0]
    grid = []
    for row in ElementTree.fromstring(z.read(sheet)).iter(f"{{{ns['m']}}}row"):
        cells = {}
        for c in row.findall("m:c", ns):
            col = re.sub(r"\d", "", c.get("r", ""))
            v = c.find("m:v", ns)
            val = "".join(t.text or "" for t in c.iter(f"{{{ns['m']}}}t")) if c.get("t") == "inlineStr" else (v.text if v is not None else None)
            if c.get("t") == "s" and val is not None:
                val = shared[int(val)]
            cells[col] = val
        grid.append(cells)
    if not grid:
        return []
    header = grid[0]
    return [{header[k]: r.get(k) for k in header if header[k]} for r in grid[1:] if any(v not in (None, "") for v in r.values())]


def _profile(rows: list[dict]) -> list[dict]:
    cols = list(dict.fromkeys(k for r in rows[:500] for k in r))
    out = []
    for c in cols:
        vals = [r.get(c) for r in rows if r.get(c) not in (None, "")]
        kind = "number" if vals and all(_num(v) is not None for v in vals[:200]) else "text"
        out.append(dict(column=c, type=kind, non_null=len(vals), distinct=len({str(v) for v in vals}),
                        example=str(vals[0])[:40] if vals else None))
    return out


def _num(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    try:
        return float(str(v).replace(",", ""))
    except ValueError:
        return None


def _match_table(db: Db, cols: set[str]) -> tuple[str | None, float]:
    best, score = None, 0.0
    for t, keys in KNOWN.items():
        if not keys <= cols:
            continue
        tcols = {r["column_name"] for r in db.rows(f"SELECT column_name FROM (DESCRIBE {t})")}
        s = len(cols & tcols) / len(cols)
        if s > score:
            best, score = t, s
    return (best, score) if score >= MIN_OVERLAP else (None, score)


def _ingest_table(db: Db, path: Path, filename: str, ext: str, principal: str) -> IngestReport:
    rows = _read_rows(path, ext)
    if not rows:
        return IngestReport(file=filename, kind="table", summary="No rows found", warnings=["empty file or no header"])
    prof = _profile(rows)
    cols = [p["column"] for p in prof]
    rep = IngestReport(file=filename, kind="table", preview=[jsonable(r) for r in rows[:5]])
    target, score = _match_table(db, set(cols))
    if target:
        added, skipped = _append_known(db, target, rows, cols)
        rep.kind, rep.tables, rep.rows = "known_table", [target], added
        if skipped:
            rep.warnings.append(f"{skipped} row(s) skipped: {ID_COL[target]} already present")
        unknown = sorted(set(cols) - {r["column_name"] for r in db.rows(f"SELECT column_name FROM (DESCRIBE {target})")})
        if unknown:
            rep.warnings.append(f"columns not in {target} were dropped: {unknown}")
        _refresh_dq(db)
        wells = sorted({r.get("asset_id") for r in rows if r.get("asset_id")})[:10]
        rep.summary = (f"Appended {added} row(s) to {target} (dataset {DS}; column overlap {score:.0%}). "
                       f"Data-quality flags recomputed. Wells: {', '.join(wells) or 'n/a'}.")
        return rep
    name = f"user_{slug(filename)}"
    _create_user_table(db, name, rows, prof)
    db.run("INSERT OR REPLACE INTO user_tables VALUES (?,?,?,?,?,?)",
           [name, filename, ", ".join(f"{p['column']} {p['type']}" for p in prof), len(rows), principal, datetime.now(timezone.utc)])
    rep.tables, rep.rows = [name], len(rows)
    rep.summary = (f"Created table {name} ({len(rows)} rows, {len(cols)} columns) — no known table matched "
                   f"(best overlap {score:.0%}). Query it with the sql tool: SELECT * FROM {name}.")
    rep.warnings += [f"profile: {p['column']} {p['type']} non_null={p['non_null']} distinct={p['distinct']}" for p in prof[:12]]
    return rep


def _append_known(db: Db, table: str, rows: list[dict], cols: list[str]) -> tuple[int, int]:
    tcols = [r["column_name"] for r in db.rows(f"SELECT column_name FROM (DESCRIBE {table})")]
    use = [c for c in cols if c in tcols and c != "dataset_id"]
    idc = ID_COL[table]
    with db.lock:
        db.con.execute("CREATE OR REPLACE TEMP TABLE _up AS SELECT * FROM read_json(?, format='array', records=true)",
                       [_tmp_json([{c: _cell(r.get(c)) for c in use} for r in rows])])
        before = db.con.execute(f"SELECT count(*) FROM {table} WHERE dataset_id=?", [DS]).fetchone()[0]
        sel = ", ".join(f'TRY_CAST(u."{c}" AS {_coltype(db, table, c)}) AS "{c}"' for c in use)
        db.con.execute(f"""INSERT INTO {table} BY NAME SELECT '{DS}' AS dataset_id, {sel} FROM _up u
                           WHERE NOT EXISTS (SELECT 1 FROM {table} t WHERE t.dataset_id='{DS}' AND t.{idc} = u."{idc}"::VARCHAR)""")
        added = db.con.execute(f"SELECT count(*) FROM {table} WHERE dataset_id=?", [DS]).fetchone()[0] - before
        db.con.execute("DROP TABLE IF EXISTS _up")
    return added, len(rows) - added


def _cell(v):
    return None if v == "" else v


def _coltype(db: Db, table: str, col: str) -> str:
    return db.rows(f"SELECT column_type FROM (DESCRIBE {table}) WHERE column_name=?", [col])[0]["column_type"]


def _tmp_json(rows: list[dict]) -> str:
    UPLOADS.mkdir(parents=True, exist_ok=True)
    p = UPLOADS / f".tmp-{uuid.uuid4().hex[:8]}.json"
    p.write_text(json.dumps(jsonable(rows), default=str))
    return str(p)


def _create_user_table(db: Db, name: str, rows: list[dict], prof: list[dict]) -> None:
    types = {p["column"]: "DOUBLE" if p["type"] == "number" else "VARCHAR" for p in prof}
    safe = {c: re.sub(r"[^a-z0-9_]+", "_", c.lower()).strip("_") or f"col{i}" for i, c in enumerate(types)}
    with db.lock:
        db.con.execute(f'CREATE OR REPLACE TABLE {name} (dataset_id VARCHAR, {", ".join(f"{safe[c]} {t}" for c, t in types.items())})')
        db.con.executemany(f"INSERT INTO {name} VALUES ({', '.join('?' * (len(types) + 1))})",
                           [[DS] + [(_num(r.get(c)) if t == "DOUBLE" else (None if r.get(c) in (None, "") else
                                    (json.dumps(r[c]) if isinstance(r[c], (dict, list)) else str(r[c]))))
                                    for c, t in types.items()] for r in rows])


def _refresh_dq(db: Db) -> None:
    """Recompute dq flags; keep flags written at ingest by other steps, refresh duplicate-id flags."""
    from wellops.trust import dq

    with db.lock:
        keep = db.con.execute(f"SELECT * FROM dq_flags WHERE dataset_id=? AND kind NOT IN {tuple(DQ_OWN | {'duplicate_inspection_id'})}",
                              [DS]).fetchall()
        dq.compute(db.con, DS)
        if keep:
            db.con.executemany("INSERT INTO dq_flags VALUES (?,?,?,?)", keep)
        db.con.execute("""INSERT INTO dq_flags SELECT dataset_id, inspection_id, 'duplicate_inspection_id',
                          'inspection id used by ' || count(DISTINCT asset_id) || ' wells: ' || string_agg(DISTINCT asset_id, ', ')
                          FROM inspections WHERE dataset_id=? GROUP BY dataset_id, inspection_id HAVING count(DISTINCT asset_id) > 1""", [DS])


# ---------------------------------------------------------------- documents

def _doc_text(path: Path, ext: str, warnings: list[str]) -> str:
    if ext != ".pdf":
        return path.read_text(errors="replace")
    try:
        from pypdf import PdfReader
    except ImportError:
        warnings.append("PDF text extraction needs pypdf (not installed): `uv add pypdf` in backend/")
        return ""
    return "\n\n".join(p.extract_text() or "" for p in PdfReader(str(path)).pages)


def _ingest_doc(db: Db, path: Path, filename: str, ext: str) -> IngestReport:
    rep = IngestReport(file=filename, kind="document")
    text = _doc_text(path, ext, rep.warnings)
    if not text.strip():
        rep.summary = "No text extracted"
        return rep
    if not re.search(r"(?m)^#{1,4}\s", text):  # plain text / PDF: one section per paragraph block of ~1500 chars
        text = f"# {Path(filename).stem}\n\n" + text
    secs = split_sections(text)
    doc_id = f"UP-{slug(filename)}"
    UPLOADS.mkdir(parents=True, exist_ok=True)
    stored = UPLOADS / f"{doc_id}{ext}"
    stored.write_bytes(path.read_bytes())
    with db.lock:
        db.con.execute("DELETE FROM docs WHERE dataset_id=? AND doc_id=?", [DS, doc_id])
        db.con.executemany("INSERT INTO docs VALUES (?,?,?,?,?,?)", [(DS, doc_id, _rel(stored), *s) for s in secs])
    rep.docs, rep.tables = len(secs), ["docs"]
    rep.preview = [dict(doc_id=doc_id, section=s[0], title=s[1], chars=len(s[2])) for s in secs[:8]]
    # a well-type manual: rules apply immediately (same extractor as dataset ingest; quotes are verbatim)
    wt = _well_type(filename, text)
    got, parent = manual_rules(wt, secs) if wt else ([], None)
    if got:
        with db.lock:
            db.con.execute("DELETE FROM rules WHERE scope_level='well_type' AND scope_key=? AND doc_id=?", [wt, doc_id])
            db.con.executemany("INSERT INTO rules VALUES (?,?,?,?,?,?,?,?)",
                               [(r["param"], r["scope_level"], r["scope_key"], r["value"], r["unit"], doc_id, r["section"], r["quote"]) for r in got])
            if parent and not db.con.execute("SELECT 1 FROM type_inherits WHERE dataset_id=? AND well_type=?", [DS, wt]).fetchone():
                db.con.execute("INSERT INTO type_inherits VALUES (?,?,?)", [DS, wt, parent])
            db.con.execute("DELETE FROM dq_flags WHERE dataset_id=? AND entity_id=? AND kind='well_type_without_rule'", [DS, wt])
            db.con.execute("INSERT INTO dq_flags VALUES (?,?,?,?)", [DS, wt, "rules_extracted_from_new_manual", f"{doc_id}: " + ", ".join(
                f"{r['param']}={r['value']:g} (§{r['section']})" for r in got) + (f"; inherits {parent}" if parent else "")])
        rep.kind, rep.tables = "manual", ["docs", "rules"]
        rep.summary = (f"Manual for well type {wt}: {len(secs)} section(s) as {doc_id}; rules now in effect: "
                       + ", ".join(f"{r['param']}={r['value']:g} (§{r['section']}, \"{r['quote']}\")" for r in got)
                       + (f"; other procedures inherit {parent}" if parent else "") + ".")
    else:
        rep.summary = f"Stored {len(secs)} section(s) as {doc_id}; search_docs and read_doc('{doc_id}') can find it."
    return rep


def _well_type(filename: str, text: str) -> str | None:
    m = re.search(r"\b(T\d{3})\b", Path(filename).stem, re.I) or re.search(r"(?im)^#.*\b(T\d{3})\b", text)
    return m.group(1).upper() if m else None


# ---------------------------------------------------------------- images

# Free vision models that answer a plain API call, best first. Excluded: harness-only and classifier models.
VISION_PREFERRED = ("nvidia/nemotron-3.5-super-vl", "google/gemma-4-31b-it", "google/gemma-4-26b-a4b-it", "qwen/", "meta-llama/",
                    "mistralai/")
VISION_EXCLUDED = ("thinkingmachines/", "content-safety")
VISION_REJECTED: set[str] = set()
QUOTA_EXHAUSTED: list[str] = []  # set on the first 429; vision() then fails fast without calling upstream  # models that answered 401/403/404 in this process: skipped for later images


def free_vision_model() -> str | None:
    """A ':free' OpenRouter model whose input modalities include image (live list; listing costs nothing)."""
    r = httpx.get("https://openrouter.ai/api/v1/models", timeout=20)
    r.raise_for_status()
    ids = [m["id"] for m in r.json().get("data", []) if m.get("id", "").endswith(":free")
           and "image" in ((m.get("architecture") or {}).get("input_modalities") or [])
           and not any(x in m["id"] for x in VISION_EXCLUDED) and m["id"] not in VISION_REJECTED]
    rank = lambda i: next((n for n, p in enumerate(VISION_PREFERRED) if i.startswith(p)), len(VISION_PREFERRED))  # noqa: E731
    return min(ids, key=rank) if ids else None


def vision(image: bytes, mime: str, prompt: str, model: str | None = None) -> tuple[str, str]:
    """ONE chat call with the image inline. Returns (model, text)."""
    if not settings.openrouter_api_key:
        raise RuntimeError("OPENROUTER_API_KEY missing")
    if QUOTA_EXHAUSTED:  # a 429 (daily free quota) will not clear this session: make no further calls
        raise RuntimeError(QUOTA_EXHAUSTED[0])
    model = model or free_vision_model()
    if not model:
        raise RuntimeError("no free OpenRouter vision model available right now")
    url = f"data:{mime};base64,{base64.b64encode(image).decode()}"
    r = httpx.post("https://openrouter.ai/api/v1/chat/completions", timeout=120,
                   headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
                   json={"model": model, "messages": [{"role": "user", "content": [
                       {"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": url}}]}]})
    if r.status_code != 200:
        if r.status_code in (401, 403, 404):
            VISION_REJECTED.add(model)
        if r.status_code == 429:
            QUOTA_EXHAUSTED.append(f"OpenRouter quota exhausted (HTTP 429): {r.text[:120]}")
        raise RuntimeError(f"vision call failed ({model}): HTTP {r.status_code} {r.text[:200]}")
    return model, (r.json()["choices"][0]["message"].get("content") or "").strip()


DESCRIBE = ("You are cataloguing a photo for an oil-field operations team. Reply with JSON only: "
            '{"caption": one sentence, "text": all legible text in the image (signs, labels, IDs) or "", '
            '"tags": up to 8 short lowercase tags (e.g. wellhead, spill, pump jack, corrosion, flare, drone)}')


def _parse_json(s: str) -> dict:
    m = re.search(r"\{.*\}", s, re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}


def _ingest_image(db: Db, path: Path, filename: str, ext: str, principal: str) -> IngestReport:
    data = path.read_bytes()
    sha = hashlib.sha1(data).hexdigest()
    rep = IngestReport(file=filename, kind="image", images=1, tables=["media"])
    hit = db.rows("SELECT * FROM media WHERE sha1=? LIMIT 1", [sha])
    if hit:  # same bytes already described: no second vision call
        m = hit[0]
        rep.summary = f"Already ingested as {m['media_id']}: {m['caption']}"
        rep.preview = [{k: m[k] for k in ("media_id", "caption", "text", "tags", "asset_id")}]
        return rep
    UPLOADS.mkdir(parents=True, exist_ok=True)
    mid = f"MED-{sha[:10]}"
    stored = UPLOADS / f"{mid}{ext}"
    stored.write_bytes(data)
    caption, text, tags, model = "", "", [], None
    try:
        model, out = vision(data, IMAGE_EXT[ext], DESCRIBE)
        j = _parse_json(out)
        caption, text = str(j.get("caption") or out[:300]), str(j.get("text") or "")
        tags = [str(t).lower() for t in (j.get("tags") or [])][:8]
    except Exception as e:  # stored even if the description fails; analyze_image can retry later
        caption = "Image " + re.sub(r"[_-]+", " ", Path(filename).stem).strip() + " (not described: vision unavailable)"
        rep.warnings.append(f"vision model unavailable: {e}"[:300])
    wells = WELL_RE.findall(f"{filename} {text} {caption}".upper())
    asset = wells[0] if wells else None
    db.run("INSERT INTO media VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [mid, DS, _rel(stored), filename, sha, caption,
                                                                  text, tags, asset, model, principal, datetime.now(timezone.utc)])
    rep.preview = [dict(media_id=mid, caption=caption, text=text, tags=tags, asset_id=asset, model=model)]
    rep.summary = f"Stored image {mid}" + (f" for {asset}" if asset else "") + (f": {caption}" if caption else "")
    return rep
