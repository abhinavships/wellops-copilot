"""Serves generated images: GET /api/media/gen/{sha1}.png."""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from wellops.tools.media_gen import gen_path

router = APIRouter()


@router.get("/api/media/gen/{name}")
def media_gen(name: str) -> FileResponse:
    m = re.fullmatch(r"([0-9a-f]{40})\.png", name)
    if not m or not gen_path(m.group(1)).exists():
        raise HTTPException(404, "no such generated image")
    data = gen_path(m.group(1)).read_bytes()[:4]
    return FileResponse(gen_path(m.group(1)), media_type="image/png" if data.startswith(b"\x89PNG") else "image/jpeg",
                        headers={"Cache-Control": "public, max-age=31536000, immutable"})
