"""Text-to-speech proxy (ElevenLabs), built to spend as few credits as possible.

Credit guards: markdown is stripped, text is capped at MAX_CHARS (whole first sentences), the cheapest model and a
low-bitrate format are used, and every clip is cached on disk by content hash so replays cost nothing.
Speech-to-text is done in the browser (Web Speech API) and never reaches this server.
"""

from __future__ import annotations

import hashlib
import re

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from wellops.settings import ROOT, settings

router = APIRouter()

MODEL = settings.tts_model  # eleven_v3_conversational: the most natural, conversational delivery available on this key
VOICE = settings.tts_voice  # Jessica (warm, bright premade voice); free plans cannot use library voices via the API
FORMAT = "mp3_44100_128"  # full-quality audio; the low-bitrate format sounded robotic
VOICE_SETTINGS = {"stability": 0.4, "similarity_boost": 0.8, "style": 0.35, "use_speaker_boost": True}
MAX_CHARS = 400
CACHE = ROOT / "backend" / ".tts_cache"
BLOCKED: list[str] = []  # first auth/plan error from upstream; no further upstream calls while set


class TtsIn(BaseModel):
    text: str


SAY = [(r"\bWELL-0*(\d+)\b", r"well \1"), (r"\bINS-0*(\d+)-0*(\d+)\b", r"inspection \1 dash \2"),
       (r"\bMNT-0*(\d+)-0*(\d+)\b", r"repair report \1 dash \2"), (r"\bCTX-0*(\d+)-0*(\d+)\b", r"note \1 dash \2"),
       (r"\bREG-0*(\d+)-0*(\d+)\b", r"regulatory event \1 dash \2"), (r"\s?°C\b", " degrees"), (r"\bbbl\b", "barrels"),
       (r"\bUSD\s?", "$"), (r"\bΔ\s?", "a difference of "), (r"\bSR-0?(\d)", r"S R \1"), (r"\bRC-0?(\d)", r"R C \1")]


def speakable(md: str, limit: int = MAX_CHARS) -> str:
    """Markdown -> plain text, then whole leading sentences up to `limit` chars (hard cut if the first is longer)."""
    t = re.sub(r"```.*?```", " ", md, flags=re.S)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"(?m)^\s*(?:#{1,6}\s*|[-*+]\s+|\d+[.)]\s+|>\s*)", "", t)
    t = re.sub(r"(?m)^\s*\|?[\s:|-]+\|[\s:|-]*$", " ", t)  # table separator rows
    t = re.sub(r"[*_`~|]", " ", t)
    t = " ".join(t.split())
    t = t.split(" Data (")[0]  # never read the data appendix aloud
    for a, b in SAY:  # read ids and units like a person would
        t = re.sub(a, b, t)
    out = ""
    for s in re.split(r"(?<=[.!?])\s+", t):
        if len(out) + len(s) + 1 > limit:
            break
        out = f"{out} {s}".strip()
    return out or t[:limit].rstrip()


@router.post("/api/tts")
async def tts(body: TtsIn) -> Response:
    text = speakable(body.text)
    if not text:
        raise HTTPException(400, "nothing to say")
    key = hashlib.sha1(f"{MODEL}|{VOICE}|{FORMAT}|{text}".encode()).hexdigest()
    path = CACHE / f"{key}.mp3"
    if path.exists():
        return Response(path.read_bytes(), media_type="audio/mpeg", headers={"X-TTS-Cache": "hit"})
    if not settings.elevenlabs_api_key:
        raise HTTPException(503, "text-to-speech is not configured (ELEVENLABS_API_KEY missing)")
    if BLOCKED:  # an auth/plan error will not fix itself: stop calling upstream until restart
        raise HTTPException(503, f"text-to-speech unavailable: {BLOCKED[0]}")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"https://api.elevenlabs.io/v1/text-to-speech/{VOICE}",
                               params={"output_format": FORMAT},
                               headers={"xi-api-key": settings.elevenlabs_api_key, "Accept": "audio/mpeg"},
                               json={"text": text, "model_id": MODEL, "voice_settings": VOICE_SETTINGS})
    if r.status_code != 200:  # never echo request headers (the key); upstream body is a short JSON error
        msg = f"upstream error {r.status_code}: {r.text[:200]}"
        if r.status_code in (401, 402, 403):
            BLOCKED.append(msg)
        raise HTTPException(502, f"text-to-speech {msg}")
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    return Response(r.content, media_type="audio/mpeg", headers={"X-TTS-Cache": "miss"})
