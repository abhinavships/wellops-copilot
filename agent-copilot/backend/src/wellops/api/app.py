"""FastAPI surface: AG-UI run endpoint plus the small REST API the UI and evals use.

Session context (principal, chat, clock) comes from headers, never from the model or the message body.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from ag_ui.core import CustomEvent, EventType
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import ValidationError
from pydantic_ai import ModelMessage
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.ui.ag_ui import AGUIAdapter

from wellops import memory
from wellops.agent import get_agent
from wellops.agent.turn import record
from wellops.contracts import AnswerSpec, RunDeps, Visual
from wellops.settings import settings
from wellops.tools.core import ToolError, get_db, store
from wellops.verify import VERIFICATIONS

app = FastAPI(title="wellops")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
from .voice import router; app.include_router(router)  # noqa: E402, E702
from .ingest_api import router as ingest_router; app.include_router(ingest_router)  # noqa: E402, E702
from .media_gen_api import router as media_gen_router; app.include_router(media_gen_router)  # noqa: E402, E702

HISTORY: dict[str, list[ModelMessage]] = {}  # chat_id -> full pydantic-ai message history (server owns the transcript)
AGENT_OVERRIDE: dict[str, Any] = {}  # tests set {"agent": Agent} to run without a network model

RECORDS = {  # citation kind -> (table, id column)
    "inspection": ("inspections", "inspection_id"), "maintenance": ("maintenance", "maintenance_id"),
    "report": ("reports", "maintenance_id"), "reg_event": ("reg_events", "event_id"), "note": ("notes", "note_id"),
    "well": ("wells", "asset_id"), "doc": ("docs", "doc_id"),
}


def deps_from(request: Request, chat_id: str | None = None) -> RunDeps:
    h = request.headers
    try:
        today = date.fromisoformat(h.get("x-clock-today") or str(settings.clock_today))
    except ValueError as e:
        raise HTTPException(400, f"bad X-Clock-Today: {e}") from e
    return RunDeps(principal=h.get("x-user-id") or "U-201", chat_id=chat_id or h.get("x-chat-id") or "default",
                   today=today, active_dataset=settings.active_dataset)


def _custom(name: str, value: Any) -> CustomEvent:
    return CustomEvent(type=EventType.CUSTOM, name=name, value=value)


def _verification(chat_id: str) -> dict | None:
    attempts = VERIFICATIONS.get(chat_id) or []
    if not attempts:
        return None
    last = attempts[-1]
    return {"ok": last.ok, "retries": len(attempts) - 1,
            "gates": [{"name": g.name, "ok": g.ok, "detail": "; ".join(g.details) or None} for g in last.gates]}


DEFAULT_VISUAL = {"entity": "status_card", "entity_list": "table", "timeseries": "line", "category_measure": "bar",
                  "geo_points": "map", "matrix": "heatmap", "obligation_set": "obligation_timeline", "diff": "correction_diff"}


def ensure_visual(spec: AnswerSpec, chat_id: str) -> AnswerSpec:
    """Generative UI must not depend on the model remembering a visual: default one from the result shape."""
    if spec.visuals:
        return spec
    for h in dict.fromkeys(c.handle for c in spec.claims):
        st = store().get(chat_id, h)
        if st and st.result.shape in DEFAULT_VISUAL and st.rows:
            spec.visuals = [Visual(type=DEFAULT_VISUAL[st.result.shape], handle=h)]
            break
    return spec


def _capabilities(messages: list[ModelMessage]) -> list[dict]:
    out = []
    for m in messages:
        if isinstance(m, ModelResponse):
            for p in m.parts:
                if isinstance(p, ToolCallPart) and p.tool_name in ("load_skill", "read_skill_file"):
                    args = p.args_as_dict()
                    out.append({"kind": "skill", "name": args.get("name", "?"), "action": "loaded",
                                "path": args.get("path")})
    return out


@app.post("/api/agui")
async def agui(request: Request) -> Response:
    try:
        run_input = AGUIAdapter.build_run_input(await request.body())
    except ValidationError as e:
        raise HTTPException(422, str(e)) from e
    deps = deps_from(request, request.headers.get("x-chat-id") or run_input.thread_id)
    if settings.harness == "hermes" and not AGENT_OVERRIDE:
        from fastapi.responses import StreamingResponse
        from .hermes_bridge import stream_turn
        users = [m for m in run_input.messages if getattr(m, "role", None) == "user"]
        text = str(users[-1].content) if users else ""
        return StreamingResponse(stream_turn(deps, text, run_input.thread_id, run_input.run_id),
                                 media_type="text/event-stream")
    history = HISTORY.get(deps.chat_id)
    if history:  # the server already holds the transcript: only take the new user message from the client
        users = [m for m in run_input.messages if getattr(m, "role", None) == "user"]
        run_input.messages = users[-1:]
    prompt = next((str(m.content) for m in reversed(run_input.messages) if getattr(m, "role", None) == "user"), "")
    agent = AGENT_OVERRIDE.get("agent") or get_agent()
    adapter = AGUIAdapter(agent=agent, run_input=run_input, accept=request.headers.get("accept"))
    VERIFICATIONS.pop(deps.chat_id, None)

    async def on_complete(result):
        spec: AnswerSpec = ensure_visual(result.output, deps.chat_id)
        HISTORY[deps.chat_id] = result.all_messages()
        record(deps, prompt, spec)
        for c in _capabilities(result.new_messages()):
            yield _custom("wellops.capability", c)
        for c in spec.conflicts:
            yield _custom("wellops.conflict", c.model_dump(mode="json"))
        v = _verification(deps.chat_id)
        if v:
            yield _custom("wellops.verification", v)
        u = result.usage
        yield _custom("wellops.usage", {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                                        "model": getattr(agent.model, "model_name", None)})
        yield _custom("wellops.answer", spec.model_dump(mode="json"))

    return adapter.streaming_response(adapter.run_stream(deps=deps, message_history=history, on_complete=on_complete))


@app.get("/api/handles/{handle}")
def get_handle(handle: str, chat_id: str) -> dict:
    s = store().get(chat_id, handle)
    if not s:
        raise HTTPException(404, f"no handle {handle} in chat {chat_id}")
    r = s.result
    return {"handle": handle, "shape": r.shape, "columns": r.columns, "rows": s.rows, "sql": r.sql,
            "citations": [c.model_dump(mode="json") for c in r.citations],
            "dq_flags": [f.model_dump() for f in r.dq_flags], "applied_overlays": r.applied_overlays, "notes": r.notes}


@app.get("/api/users")
def users() -> dict:
    rows = get_db().rows("SELECT user_id, name, role, team_id FROM users WHERE dataset_id=? ORDER BY user_id",
                         [settings.active_dataset])
    return {"users": rows}


@app.get("/api/chats")
def chats(principal: str) -> dict:
    rows = get_db().rows("""SELECT chat_id, arg_min(utterance, ts) AS title, max(ts) AS updated_at FROM episodes
                            WHERE principal=? AND NOT forgotten GROUP BY chat_id ORDER BY updated_at DESC""", [principal])
    return {"chats": rows}


@app.post("/api/assertions/{assertion_id}/undo")
def undo(assertion_id: str, request: Request) -> dict:
    try:
        return memory.undo_assertion(deps_from(request), assertion_id).model_dump(mode="json")
    except ToolError as e:
        raise HTTPException(404, str(e)) from e


@app.get("/api/records/{kind}/{rid}")
def get_record(kind: str, rid: str) -> dict:
    if kind == "assertion":
        rows = get_db().rows("SELECT * FROM assertions WHERE assertion_id=?", [rid])
    elif kind in RECORDS:
        table, col = RECORDS[kind]
        rows = get_db().rows(f"SELECT * FROM {table} WHERE dataset_id=? AND {col}=?", [settings.active_dataset, rid])
    else:
        raise HTTPException(400, f"unknown record kind {kind}")
    if not rows:
        raise HTTPException(404, f"{kind} {rid} not found")
    return {"kind": kind, "id": rid, "record": rows[0] if len(rows) == 1 else rows}


@app.post("/api/disputes/{dispute_id}/resolve")
async def resolve(dispute_id: str, request: Request) -> dict:
    """Human-in-the-loop decision on a proof card: {action: correct|keep, value?}. No model call."""
    from wellops.memory.disputes import resolve_dispute
    body = await request.json()
    try:
        return resolve_dispute(deps_from(request), dispute_id, body.get("action", ""), body.get("value"))
    except ToolError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/health")
def health() -> dict:
    n = get_db().rows("SELECT count(*) n FROM wells WHERE dataset_id=?", [settings.active_dataset])[0]["n"]
    return {"ok": True, "dataset": settings.active_dataset, "wells": n, "harness": settings.harness, "model": __import__("wellops.api.hermes_bridge", fromlist=["MODEL"]).MODEL,
            "instance": INSTANCE}


INSTANCE = uuid.uuid4().hex[:8]

from wellops import mcp_server  # noqa: E402

mcp_server.install(app)
