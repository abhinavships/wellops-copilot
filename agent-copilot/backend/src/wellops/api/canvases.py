"""Saved canvases: named snapshots of the board (pinned figures and their layout), per principal.

A saved card carries a `snapshot` of its handle's data, so a canvas still renders after the chat that produced it
is gone (handles live per chat). Loading re-registers the snapshots under the chat "canvas-<id>" and rewrites each
card's chatId/handle to point there, so the UI fetches them through the ordinary /api/handles endpoint.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from wellops.tools.core import Db, Stored, ToolOut, get_db, store

router = APIRouter()
SCHEMA = """CREATE TABLE IF NOT EXISTS canvases (canvas_id VARCHAR PRIMARY KEY, principal VARCHAR, name VARCHAR,
            cards VARCHAR, created_at TIMESTAMP, updated_at TIMESTAMP)"""


class CanvasIn(BaseModel):
    name: str
    cards: list[dict[str, Any]] | None = None  # None on an update = keep the saved cards (rename)
    canvas_id: str | None = None


def _db() -> Db:
    db = get_db()
    db.run(SCHEMA)
    return db


def _principal(request: Request) -> str:
    return request.headers.get("x-user-id") or "U-201"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _snapshot(card: dict) -> dict:
    """Copy the card's handle data into it (keeps an existing snapshot when the handle no longer resolves)."""
    v = card.get("visual") or {}
    h, chat = v.get("handle"), card.get("chatId")
    st = store().get(chat, h) if h and chat else None
    if not st:
        return card
    r = st.result
    snap = {"columns": r.columns, "rows": st.rows, "shape": r.shape,
            "citations": [c.model_dump(mode="json") for c in r.citations],
            "sql": r.sql, "dq_flags": [f.model_dump() for f in r.dq_flags],
            "applied_overlays": r.applied_overlays, "notes": r.notes}
    return {**card, "snapshot": snap}


def _register(canvas_id: str, cards: list[dict]) -> list[dict]:
    """Put every snapshot into the handle store under chat canvas-<id>; return the cards rebound to it."""
    chat = f"canvas-{canvas_id}"
    hs = store()
    remap: dict[tuple[str, str], str] = {}
    out = []
    for card in cards:
        card = dict(card)
        snap = card.pop("snapshot", None)
        v = dict(card.get("visual") or {})
        old_chat, old_h = card.get("chatId") or "", v.get("handle")
        if old_h and snap:
            key = (old_chat, old_h)
            if key not in remap:
                remap[key] = nh = f"r{len(remap) + 1}"
                res = ToolOut(handle=nh, shape=snap.get("shape") or "entity_list", columns=snap.get("columns") or [],
                              preview=(snap.get("rows") or [])[:15], row_count=len(snap.get("rows") or []),
                              sql=snap.get("sql"), citations=snap.get("citations") or [],
                              dq_flags=snap.get("dq_flags") or [], applied_overlays=snap.get("applied_overlays") or [],
                              notes=snap.get("notes") or [])
                rows = snap.get("rows") or []
                hs.mem[(chat, nh)] = Stored(res, rows)
                hs.db.run("INSERT OR REPLACE INTO handles VALUES (?,?,?,?)",
                          [chat, nh, res.model_dump_json(), json.dumps(rows, default=str)])
            v["handle"] = remap[key]
            card["visual"] = v
        elif old_h:
            out.append(card)  # no snapshot (handle was already gone when saved): leave it pointing at its chat
            continue
        cid = str(card.get("id") or "")
        source = f"{v.get('type')}:{v['handle']}" if v.get("handle") else cid.split(":", 1)[-1]
        card["id"] = f"{chat}:{source}"
        card["chatId"] = chat
        out.append(card)
    return out


def _own(db: Db, canvas_id: str, principal: str) -> dict:
    r = db.rows("SELECT * FROM canvases WHERE canvas_id=? AND principal=?", [canvas_id, principal])
    if not r:
        raise HTTPException(404, f"no canvas {canvas_id}")
    return r[0]


@router.get("/api/canvases")
def list_canvases(request: Request) -> dict:
    rows = _db().rows("""SELECT canvas_id, name, json_array_length(cards::JSON) AS card_count, updated_at
                         FROM canvases WHERE principal=? ORDER BY updated_at DESC""", [_principal(request)])
    return {"canvases": rows}


@router.get("/api/canvases/{canvas_id}")
def get_canvas(canvas_id: str, request: Request) -> dict:
    r = _own(_db(), canvas_id, _principal(request))
    return {"canvas_id": canvas_id, "name": r["name"], "cards": _register(canvas_id, json.loads(r["cards"])),
            "created_at": r["created_at"], "updated_at": r["updated_at"]}


@router.post("/api/canvases")
def save_canvas(body: CanvasIn, request: Request) -> dict:
    db, who = _db(), _principal(request)
    name = body.name.strip() or "Untitled canvas"
    cards = None if body.cards is None else json.dumps([_snapshot(c) for c in body.cards], default=str)
    now = _now()
    if body.canvas_id:
        prev = db.rows("SELECT principal FROM canvases WHERE canvas_id=?", [body.canvas_id])
        if prev and prev[0]["principal"] != who:
            raise HTTPException(404, f"no canvas {body.canvas_id}")
        if prev:
            db.run("UPDATE canvases SET name=?, cards=coalesce(?, cards), updated_at=? WHERE canvas_id=?",
                   [name, cards, now, body.canvas_id])
            n = db.rows("SELECT json_array_length(cards::JSON) n FROM canvases WHERE canvas_id=?", [body.canvas_id])[0]["n"]
            return {"canvas_id": body.canvas_id, "name": name, "card_count": n}
    cid = body.canvas_id or uuid.uuid4().hex[:12]
    db.run("INSERT INTO canvases VALUES (?,?,?,?,?,?)", [cid, who, name, cards or "[]", now, now])
    return {"canvas_id": cid, "name": name, "card_count": len(body.cards or [])}


@router.delete("/api/canvases/{canvas_id}")
def delete_canvas(canvas_id: str, request: Request) -> dict:
    db = _db()
    _own(db, canvas_id, _principal(request))
    db.run("DELETE FROM canvases WHERE canvas_id=?", [canvas_id])
    db.run("DELETE FROM handles WHERE chat_id=?", [f"canvas-{canvas_id}"])
    hs = store()
    for k in [k for k in hs.mem if k[0] == f"canvas-{canvas_id}"]:
        del hs.mem[k]
    return {"deleted": canvas_id}
