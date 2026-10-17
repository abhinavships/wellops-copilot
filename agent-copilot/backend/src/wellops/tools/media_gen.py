"""Image generation via Pollinations (free, keyless). Cached by prompt+size: the same request is never fetched twice."""

from __future__ import annotations

import hashlib
import urllib.parse
import urllib.request
from pathlib import Path

from wellops.contracts import RunDeps
from wellops.settings import ROOT

from .core import ToolError

GEN_DIR = ROOT / "backend" / "uploads" / "gen"
POLLINATIONS = "https://image.pollinations.ai/prompt/{prompt}?width={width}&height={height}&nologo=true"


def gen_path(media_id: str) -> Path:
    return GEN_DIR / f"{media_id}.png"


def generate_image(deps: RunDeps, prompt: str, width: int = 768, height: int = 512) -> dict:
    prompt = (prompt or "").strip()
    if not prompt:
        raise ToolError("prompt is empty")
    if not (64 <= width <= 2048 and 64 <= height <= 2048):
        raise ToolError("width and height must be between 64 and 2048")
    media_id = hashlib.sha1(f"{prompt}|{width}x{height}".encode()).hexdigest()
    path = gen_path(media_id)
    if not path.exists():
        url = POLLINATIONS.format(prompt=urllib.parse.quote(prompt, safe=""), width=width, height=height)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "wellops"}), timeout=120) as r:
                data = r.read()
        except OSError as e:
            raise ToolError(f"image generation failed: {e}") from e
        if not data.startswith((b"\x89PNG", b"\xff\xd8")):
            raise ToolError("image generation returned no image")
        GEN_DIR.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(path)
    return {"media_id": media_id, "url": f"/api/media/gen/{media_id}.png", "prompt": prompt}
