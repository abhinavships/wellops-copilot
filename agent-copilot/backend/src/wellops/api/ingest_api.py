"""Upload endpoint: POST /api/ingest (multipart field `file`, or a raw body with X-Filename), media and history.

Multipart is parsed with the stdlib email parser so no extra dependency (python-multipart) is needed.
"""

from __future__ import annotations

import email.parser
import email.policy
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from wellops.ingest import pipeline
from wellops.settings import ROOT
from wellops.tools.core import get_db

router = APIRouter()
MAX_BYTES = 25 * 1024 * 1024


def _upload(body: bytes, content_type: str, request: Request) -> tuple[str, bytes]:
    if content_type.startswith("multipart/form-data"):
        msg = email.parser.BytesParser(policy=email.policy.HTTP).parsebytes(
            f"Content-Type: {content_type}\r\n\r\n".encode() + body)
        for part in msg.iter_parts():
            if part.get_param("name", header="content-disposition") == "file":
                return Path(part.get_filename() or "upload.bin").name, part.get_payload(decode=True) or b""
        raise HTTPException(400, "multipart body has no 'file' field")
    name = request.headers.get("x-filename")
    if not name:
        raise HTTPException(400, "send multipart/form-data with a 'file' field, or a raw body with an X-Filename header")
    return Path(name).name, body


@router.post("/api/ingest")
async def ingest(request: Request) -> dict:
    body = await request.body()
    if len(body) > MAX_BYTES:
        raise HTTPException(413, f"file larger than {MAX_BYTES // 2**20} MB")
    name, data = _upload(body, request.headers.get("content-type", ""), request)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / name
        p.write_bytes(data)
        rep = pipeline.ingest_file(p, name, principal=request.headers.get("x-user-id") or "U-201",
                                   chat_id=request.headers.get("x-chat-id"))
    return rep.model_dump()


@router.get("/api/media/{media_id}")
def media(media_id: str) -> FileResponse:
    pipeline.ensure_schema(get_db())
    rows = get_db().rows("SELECT path FROM media WHERE media_id=?", [media_id])
    if not rows:
        raise HTTPException(404, f"no media {media_id}")
    return FileResponse(ROOT / rows[0]["path"])


@router.get("/api/ingest/history")
def ingest_history(limit: int = 50) -> dict:
    return {"items": pipeline.history(limit=limit)}
