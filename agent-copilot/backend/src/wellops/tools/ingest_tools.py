"""Ingestion tools as plain functions returning dicts (registered as WellOps MCP tools)."""

from __future__ import annotations

import json
from pathlib import Path

from wellops.contracts import RunDeps
from wellops.ingest import pipeline
from wellops.settings import ROOT

from .core import ToolError, get_db


def ingest_file_tool(deps: RunDeps, path: str) -> dict:
    """Ingest a local file (table, document or image) and return the IngestReport as a dict."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise ToolError(f"no such file: {path}")
    return pipeline.ingest_file(p, p.name, principal=deps.principal, chat_id=deps.chat_id).model_dump()


def _media(ref: str) -> dict | None:
    db = get_db()
    pipeline.ensure_schema(db)
    rows = db.rows("SELECT * FROM media WHERE media_id=? OR path=? OR filename=? LIMIT 1", [ref, ref, Path(ref).name])
    return rows[0] if rows else None


def analyze_image(deps: RunDeps, media_id_or_path: str, question: str | None = None) -> dict:
    """Answer a question about an ingested image (one vision call). Without a question, return its stored description.
    A path that is not ingested yet is ingested first (which describes it with one call)."""
    m = _media(media_id_or_path)
    if m is None:
        p = Path(media_id_or_path).expanduser()
        if not p.is_file():
            raise ToolError(f"no media or file {media_id_or_path!r}; list_media shows what exists")
        rep = pipeline.ingest_file(p, p.name, principal=deps.principal, chat_id=deps.chat_id)
        m = _media(rep.preview[0]["media_id"]) if rep.preview else None
        if m is None:
            raise ToolError("; ".join(rep.warnings) or "image could not be ingested")
        if not question:
            return dict(media_id=m["media_id"], caption=m["caption"], text=m["text"], tags=m["tags"], asset_id=m["asset_id"])
    if not question:
        return dict(media_id=m["media_id"], caption=m["caption"], text=m["text"], tags=m["tags"], asset_id=m["asset_id"])
    path = ROOT / m["path"]
    mime = pipeline.IMAGE_EXT.get(path.suffix.lower(), "image/png")
    model, answer = pipeline.vision(path.read_bytes(), mime, question + "\nAnswer briefly; say if the image does not show it.",
                                    model=m.get("model"))
    return dict(media_id=m["media_id"], question=question, answer=answer, model=model, asset_id=m["asset_id"],
                citation=dict(kind="media", id=m["media_id"]))


def list_media(deps: RunDeps, asset_id: str | None = None) -> dict:
    """Ingested images (optionally for one well), newest first, with captions and tags."""
    db = get_db()
    pipeline.ensure_schema(db)
    sql = ("SELECT media_id, filename, caption, text, tags, asset_id, created_at FROM media WHERE dataset_id=?"
           + (" AND asset_id=?" if asset_id else "") + " ORDER BY created_at DESC LIMIT 100")
    rows = db.rows(sql, [deps.active_dataset] + ([asset_id.upper()] if asset_id else []))
    return dict(count=len(rows), media=json.loads(json.dumps(rows, default=str)))
