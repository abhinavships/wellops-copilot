"""Chunkers: turn manuals/SOPs, maintenance reports, notes and media captions into retrievable passages.

Every chunk keeps the ids the evidence drawer needs (doc_id + section, maintenance_id + char span, note/inspection id,
media id) and its text is an exact slice of the source, so a span highlights the passage verbatim.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from typing import Any

import duckdb

MAX_CHARS = 1100  # longer sections are split on blank lines, greedily packed up to this size
H2 = re.compile(r"(?m)^#{2,4}\s+(.+)$")
PARA = re.compile(r"\n\s*\n")


@dataclass
class Chunk:
    cid: str  # dataset-qualified unique key (FTS document id)
    dataset_id: str
    kind: str  # doc | report | note | media
    src_id: str  # doc_id / maintenance_id / note_id / inspection_id / media_id
    cite_kind: str  # Citation.kind for the source
    title: str
    section: str | None
    text: str
    span_start: int | None  # char span into the source text (reports, split doc sections)
    span_end: int | None
    asset_id: str | None = None
    author: str | None = None
    ts: Any = None  # TIMESTAMPTZ; None for timeless docs
    sha: str = ""  # sha1(text): the vector-cache key
    rev: str = ""  # sha1 of every field: what reindex diffs on

    def __post_init__(self) -> None:
        self.sha = sha1(self.text)
        self.rev = sha1(json.dumps(self.row(), default=str))

    def row(self) -> dict:
        d = asdict(self)
        d["ts"] = self.ts.isoformat() if hasattr(self.ts, "isoformat") else self.ts
        d.pop("rev")
        return d


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


def pack(text: str, base: int = 0, limit: int = MAX_CHARS) -> list[tuple[int, int]]:
    """Absolute (start, end) spans of blank-line paragraphs in text, packed up to `limit` chars; never splits a paragraph."""
    paras, pos = [], 0
    for m in PARA.finditer(text):
        paras.append((pos, m.start()))
        pos = m.end()
    paras.append((pos, len(text)))
    out: list[tuple[int, int]] = []
    for s, e in paras:
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e <= s:
            continue
        if out and e - out[-1][0] <= limit:
            out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return [(base + s, base + e) for s, e in out]


def report_spans(text: str) -> list[tuple[str, int, int]]:
    """(heading, start, end) per report section (the preamble is 'header'), long sections split into paragraphs."""
    heads = list(H2.finditer(text))
    cuts = [("header", 0)] + [(m.group(1).strip(), m.start()) for m in heads]
    out = []
    for i, (h, s) in enumerate(cuts):
        e = cuts[i + 1][1] if i + 1 < len(cuts) else len(text)
        out += [(h, a, b) for a, b in pack(text[s:e], s)]
    return out


def _rows(con: duckdb.DuckDBPyConnection, sql: str, params: list) -> list[dict]:
    cur = con.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _has(con: duckdb.DuckDBPyConnection, table: str) -> bool:
    return bool(con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name=?", [table]).fetchone()[0])


def doc_chunks(con, ds: str) -> list[Chunk]:
    """One chunk per manual/SOP section (uploaded UP-* docs included); oversized sections split with spans."""
    out = []
    for r in _rows(con, "SELECT doc_id, section, title, text FROM docs WHERE dataset_id=? ORDER BY doc_id, section", [ds]):
        t, title = r["text"] or "", f"{r['doc_id']} §{r['section']}" + (f" {r['title']}" if r["title"] else "")
        spans = pack(t) if len(t) > MAX_CHARS * 1.5 else [(0, len(t))]
        for a, b in spans:
            split = len(spans) > 1
            out.append(Chunk(f"{ds}/{r['doc_id']}§{r['section']}" + (f"#{a}" if split else ""), ds, "doc", r["doc_id"],
                             "doc", title, r["section"], t[a:b], a if split else None, b if split else None))
    return out


def report_chunks(con, ds: str) -> list[Chunk]:
    """Maintenance reports as section/paragraph chunks with char spans into reports.text."""
    out = []
    for r in _rows(con, """SELECT r.maintenance_id, r.asset_id, r.text, m.started_at, m.technician_user_id
                           FROM reports r LEFT JOIN maintenance m ON m.dataset_id=r.dataset_id AND m.maintenance_id=r.maintenance_id
                           WHERE r.dataset_id=? ORDER BY r.maintenance_id""", [ds]):
        for h, a, b in report_spans(r["text"] or ""):
            out.append(Chunk(f"{ds}/{r['maintenance_id']}#{a}", ds, "report", r["maintenance_id"], "report",
                             f"{r['maintenance_id']} · {h}", h, r["text"][a:b], a, b, r["asset_id"],
                             r["technician_user_id"], r["started_at"]))
    return out


def note_chunks(con, ds: str) -> list[Chunk]:
    """Operator notes/corrections (one chunk each) and free-text inspection notes (kind note, cited as the inspection)."""
    out = [Chunk(f"{ds}/{r['note_id']}", ds, "note", r["note_id"], "note",
                 f"{r['note_id']} {r['note_type'] or 'note'} on {r['asset_id']}", r["note_type"], r["text"], None, None,
                 r["asset_id"], r["author_user_id"], r["created_at"])
           for r in _rows(con, """SELECT note_id, asset_id, created_at, author_user_id, note_type, text FROM notes
                                  WHERE dataset_id=? AND coalesce(text,'')<>''""", [ds])]
    out += [Chunk(f"{ds}/{r['inspection_id']}", ds, "note", r["inspection_id"], "inspection",
                  f"{r['inspection_id']} inspection note on {r['asset_id']}", "inspection_note", r["notes"],
                  None, None, r["asset_id"], r["inspector_user_id"], r["inspected_at"])
            for r in _rows(con, """SELECT inspection_id, asset_id, inspected_at, inspector_user_id, notes FROM inspections
                                   WHERE dataset_id=? AND coalesce(notes,'')<>''""", [ds])]
    return out


def media_chunks(con, ds: str) -> list[Chunk]:
    """Uploaded image captions + legible text + tags (only if the media table exists)."""
    if not _has(con, "media"):
        return []
    out = []
    for r in _rows(con, "SELECT media_id, filename, caption, text, tags, asset_id, principal, created_at FROM media WHERE dataset_id=?", [ds]):
        body = "\n".join(x for x in [r["caption"], r["text"], ", ".join(r["tags"] or [])] if x)
        if body:
            out.append(Chunk(f"{ds}/{r['media_id']}", ds, "media", r["media_id"], "doc", f"{r['media_id']} image {r['filename']}",
                             None, body, None, None, r["asset_id"], r["principal"], r["created_at"]))
    return out


def all_chunks(con, ds: str) -> list[Chunk]:
    seen: dict[str, Chunk] = {}
    for fn in (doc_chunks, report_chunks, note_chunks, media_chunks):
        for c in fn(con, ds):
            seen.setdefault(c.cid, c)
    return list(seen.values())
