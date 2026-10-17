"""search_knowledge: the RAG tool. Verbatim passages from manuals/SOPs, maintenance reports, notes and image captions,
each with the citation (kind, id, span) the verifier and the evidence drawer resolve."""

from __future__ import annotations

import re

from wellops.contracts import Citation, RunDeps
from wellops.tools.core import Db, ToolError, ToolOut, finish, get_db, parse_date

from .index import KINDS, reindex, retrieve

SNIPPET = 320
COLUMNS = ["rank", "score", "kind", "id", "title", "section", "asset_id", "date", "author", "span", "snippet", "same_text_count"]


def ensure_index(db: Db, ds: str) -> None:
    """Build the lexical index on first use (dense vectors come from ingest/upload, never from the question path)."""
    with db.lock:
        n = db.con.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='kb_chunks'").fetchone()[0]
        empty = not n or not db.con.execute("SELECT count(*) FROM kb_chunks WHERE dataset_id=?", [ds]).fetchone()[0]
    if empty:
        reindex(db, ds, embed=False)


def snippet(text: str, query: str) -> str:
    """~320 chars where the most distinct query terms meet (prefix match, so 'containment' finds 'contained')."""
    if len(text) <= SNIPPET:
        return text
    low = text.lower()
    terms = {t[:6] for t in re.findall(r"\w{3,}", query.lower())}
    starts = [m.start() for t in terms for m in re.finditer(re.escape(t), low)] or [100]
    best = max(starts, key=lambda p: (sum(t in low[max(0, p - 100):p + SNIPPET - 100] for t in terms), -p))
    s = max(0, best - 100)
    return ("…" if s else "") + text[s:s + SNIPPET].strip() + ("…" if s + SNIPPET < len(text) else "")


def _cite(h: dict) -> Citation:
    span = (h["span_start"], h["span_end"]) if h["span_start"] is not None else None
    if h["kind"] == "doc":
        return Citation(kind="doc", id=f"{h['src_id']} §{h['section']}", span=span, label=h["title"])
    return Citation(kind=h["cite_kind"], id=h["src_id"], span=span, label=h["title"])


def search_knowledge(deps: RunDeps, query: str, k: int = 6, asset_id: str | None = None, kind: str | None = None,
                     start: str | None = None, end: str | None = None) -> ToolOut:
    """Hybrid (BM25 + embeddings) passage search over manuals/SOPs, maintenance reports, notes and media captions."""
    db = get_db()
    if not query or not query.strip():
        raise ToolError("empty query")
    if kind and kind not in KINDS:
        raise ToolError(f"kind must be one of {list(KINDS)}")
    k = max(1, min(int(k or 6), 15))
    ensure_index(db, deps.active_dataset)
    hits, notes = retrieve(db, deps.active_dataset, query, k, asset_id.upper() if asset_id else None, kind,
                           parse_date(start), parse_date(end), deps.asof)
    rows = [dict(rank=i + 1, score=round(h["rrf"], 4), kind=h["kind"],
                 id=f"{h['src_id']} §{h['section']}" if h["kind"] == "doc" else h["src_id"], title=h["title"],
                 section=h["section"], asset_id=h["asset_id"], date=h["ts"].date().isoformat() if h["ts"] else None,
                 author=h["author"], span=[h["span_start"], h["span_end"]] if h["span_start"] is not None else None,
                 snippet=snippet(h["text"], query), same_text_count=h["same_text"])
            for i, h in enumerate(hits)]
    cites = [_cite(h) for h in hits]
    out = finish(db, deps, "text", COLUMNS, rows, sql=None, citations=cites,
                 notes=[*notes, "passages are verbatim; span = char offsets into the report/doc section",
                        "same_text_count = other records with identical text (only the newest is shown)",
                        "read_doc / evidence_chain give the full record"])
    first = {(c.id, c.span) for c in cites}  # passage citations first: the MCP payload keeps only the first 15
    out.citations = cites + [c for c in out.citations if (c.id, c.span) not in first]
    return out
