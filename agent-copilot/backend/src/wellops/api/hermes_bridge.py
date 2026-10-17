"""Hermes Agent behind the AG-UI endpoint.

Hermes runs the agent loop (its own process and venv: agent-copilot/hermes); WellOps tools reach it over MCP.
Around it sits the WellOps harness: the same verifier gates, with up to two corrective re-asks, the generative-UI
queue filled by `show_visual`, and the wellops.* events the UI already understands.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from pathlib import Path
from typing import AsyncIterator

from wellops import mcp_server
from wellops.agent import prompt as base_prompt
from wellops.agent.turn import record
from wellops.contracts import AnswerSpec, RunDeps, Visual
from wellops.settings import ROOT, settings
from wellops.tools.core import store
from wellops.verify.gates import MAX_RETRIES, verify

HERMES_DIR = ROOT / "hermes"
HERMES_PY = HERMES_DIR / ".venv" / "bin" / "python"
PROVIDERS = {  # provider -> (base_url, default Nemotron model id, settings attr holding the key)
    "nvidia": ("https://integrate.api.nvidia.com/v1", "nvidia/nemotron-3-super-120b-a12b", "nvidia_api_key"),
    "openrouter": ("https://openrouter.ai/api/v1", "nvidia/nemotron-3-super-120b-a12b:free", "openrouter_api_key"),
}
BASE_URL, _DEFAULT_MODEL, _KEY_ATTR = PROVIDERS[settings.hermes_provider]
MODEL = settings.hermes_model or _DEFAULT_MODEL
FAST = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}, "temperature": 0.2}  # Nemotron: no hidden reasoning
HISTORY: dict[str, list[dict]] = {}  # chat_id -> Hermes (OpenAI-format) message history

SYSTEM = """You are WellOps, an oil-field asset copilot. Today is {today}; the user is {principal}.
Rules:
- Answer ONLY what was asked, in 1-3 short sentences. No preamble, no lists of assumptions.
- Every number you state must come from a WellOps tool result in this turn. Never guess.
- Resolve areas and relative periods ("last 4 months") with resolve_entity first, then call the data tool.
- After getting data, call show_visual with the result handle and the visual that fits (status_card for one well,
  bar for rankings, line for time series, map for locations, heatmap for area x month, kpi for one number).
  If the user asks for a specific chart, use exactly that type.
- If the user says a value is WRONG (a reading, a status, a spill), call dispute_data first: it shows the proof
  and Correct/Keep buttons. Say in one sentence what the evidence shows and ask if they still want it corrected.
  Never correct data on the user's word alone. Rules or facts the user states (not disputes) go to remember_correction.
- If notes and sensor data disagree, say so plainly and name both sources.
- For "why" questions use the reason_summary from well_status or call evidence_chain on the inspection id.
- Mention the key record id (e.g. INS-0001-0203) once, inline.
- To create an image, call generate_image (it is displayed automatically).
"""


def _sse(ev: dict) -> str:
    return f"data: {json.dumps(ev, default=str)}\n\n"


async def _run_hermes(req: dict) -> AsyncIterator[dict]:
    proc = await asyncio.create_subprocess_exec(
        str(HERMES_PY), str(HERMES_DIR / "runner.py"), cwd=str(HERMES_DIR),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        limit=16 * 1024 * 1024)
    proc.stdin.write(json.dumps(req).encode())
    await proc.stdin.drain()
    proc.stdin.close()
    async for line in proc.stdout:
        try:
            yield json.loads(line)
        except ValueError:
            continue
    await proc.wait()


def _compose(deps: RunDeps, text: str) -> AnswerSpec:
    visuals = mcp_server.UI_QUEUE.get(deps.chat_id, [])
    results = mcp_server.TOOL_LOG.get(deps.chat_id, [])
    if not visuals:  # never leave a data answer without UI
        from .app import DEFAULT_VISUAL
        for r in reversed(results):
            if r.shape in DEFAULT_VISUAL and r.row_count:
                visuals = [Visual(type=DEFAULT_VISUAL[r.shape], handle=r.handle)]
                break
    cites = [c for r in results for c in r.citations][:12]
    return AnswerSpec(markdown=text.strip(), visuals=visuals, citations=cites,
                      assumptions=list(dict.fromkeys(n for r in results for n in r.notes))[:4])


async def _live_turn(deps: RunDeps, message: str, state: dict) -> AsyncIterator[dict]:
    history = HISTORY.get(deps.chat_id, [])
    system = SYSTEM.format(today=deps.today, principal=deps.principal)
    headers = {"X-User-Id": deps.principal, "X-Chat-Id": deps.chat_id, "X-Clock-Today": str(deps.today)}
    ask, attempts, spec, final_msgs = message, [], None, history
    mcp_server.UI_QUEUE.pop(deps.chat_id, None)
    mcp_server.TOOL_LOG.pop(deps.chat_id, None)
    for attempt in range(MAX_RETRIES + 1):
        req = dict(message=ask, history=final_msgs, system=system, headers=headers,
                   mcp_url="http://127.0.0.1:8000/mcp/", model=MODEL, api_key=getattr(settings, _KEY_ATTR), base_url=BASE_URL, provider=settings.hermes_provider,
                   request_overrides=FAST if settings.hermes_provider == "nvidia" else None,
                   home=str(HERMES_DIR / "home" / re.sub(r"\W", "_", deps.chat_id)))
        mid, text, err = f"m-{uuid.uuid4().hex[:8]}", "", None
        yield ({"type": "TEXT_MESSAGE_START", "messageId": mid, "role": "assistant"})
        async for ev in _run_hermes(req):
            k = ev.get("ev")
            if k == "tool_start":
                yield ({"type": "TOOL_CALL_START", "toolCallId": ev["id"], "toolCallName": ev["name"].removeprefix("mcp__wellops__"), "parentMessageId": mid})
                yield ({"type": "TOOL_CALL_ARGS", "toolCallId": ev["id"], "delta": json.dumps(ev.get("args"), default=str)})
                yield ({"type": "TOOL_CALL_END", "toolCallId": ev["id"]})
            elif k == "tool_end":
                yield ({"type": "TOOL_CALL_RESULT", "toolCallId": ev["id"], "messageId": mid, "content": ev.get("result", "")[:2000]})
            elif k == "delta":
                text += ev["text"]
                yield ({"type": "TEXT_MESSAGE_CONTENT", "messageId": mid, "delta": ev["text"]})
            elif k == "final":
                text = ev.get("text") or text
                final_msgs = ev.get("messages") or final_msgs
            elif k == "error":
                err = ev["message"]
        yield ({"type": "TEXT_MESSAGE_END", "messageId": mid})
        if not err and re.match(r"API call failed|HTTP 4\d\d|Rate limit", text.strip()):
            err = text.strip()[:300]  # provider failure surfaced by Hermes as text: never present it as an answer
        if err:
            yield ({"type": "RUN_ERROR", "message": f"Model provider: {err}" if "429" in err else f"Hermes: {err}"})
            state["error"] = True
            return
        spec = _compose(deps, text)
        results = mcp_server.TOOL_LOG.get(deps.chat_id, [])
        raw = " ".join(r.model_dump_json() for r in results) + " " + " ".join(
            json.dumps(st.rows, default=str) for r in results if (st := store().get(deps.chat_id, r.handle)))
        v = verify(deps, spec, results, raw_tools=raw, user_text=message, attempt=attempt)
        if not results and not text.rstrip().endswith("?"):  # an answer with no tool data is a guess (or a refusal)
            from wellops.verify.gates import GateResult
            v.gates.append(GateResult(name="tool_grounded", ok=False, details=["no WellOps tool was called"]))
            v.ok = False
        attempts.append(v)
        if v.ok or attempt == MAX_RETRIES:
            break
        ask = f"Verification failed, fix your answer and call tools again if needed: {v.diff()}"
    HISTORY[deps.chat_id] = final_msgs
    record(deps, message, spec)
    last = attempts[-1]
    yield ({"type": "CUSTOM", "name": "wellops.verification", "value": {
        "ok": last.ok, "retries": len(attempts) - 1, "harness": "hermes-agent",
        "gates": [{"name": g.name, "ok": g.ok, "detail": "; ".join(g.details) or None} for g in last.gates]}})
    yield ({"type": "CUSTOM", "name": "wellops.usage", "value": {"model": f"hermes-agent → {MODEL}"}})
    yield ({"type": "CUSTOM", "name": "wellops.answer", "value": spec.model_dump(mode="json")})
    state.update(ok=last.ok, spec=spec, retries=len(attempts) - 1)


def _rehandle(deps: RunDeps, payload: dict) -> list[dict]:
    """Register cached handle rows in this chat's store and rewrite handle ids in the replayed events."""
    from wellops.tools.core import ToolOut
    remap = {}
    for old, (res, rows) in payload["handles"].items():
        remap[old] = store().put(deps.chat_id, ToolOut.model_validate(res), rows)
    out = json.dumps(payload["events"])
    for old, new in sorted(remap.items(), key=lambda kv: -len(kv[0])):
        out = re.sub(rf'"{old}"', f'"{new}"', out)
    return json.loads(out)


async def stream_turn(deps: RunDeps, message: str, thread_id: str, run_id: str) -> AsyncIterator[str]:
    """AG-UI stream with a SQLite hot cache in front and a measured run trace (wellops.trace) for the UI."""
    import time
    from wellops import cache
    yield _sse({"type": "RUN_STARTED", "threadId": thread_id, "runId": run_id})
    t0 = time.perf_counter()
    key = cache.turn_key(message, deps.principal, deps.today)
    hit = cache.get_turn(key)
    if hit:
        events = _rehandle(deps, hit)
        for ev in events:
            yield _sse(ev)
        ms = int((time.perf_counter() - t0) * 1000)
        yield _sse({"type": "CUSTOM", "name": "wellops.cache", "value": {"hit": True, "ms": ms, "saved_ms": hit["ms"]}})
        spec = next((e["value"] for e in events if e.get("name") == "wellops.answer"), None)
        if spec:
            record(deps, message, AnswerSpec.model_validate(spec))
        yield _sse({"type": "RUN_FINISHED", "threadId": thread_id, "runId": run_id})
        return
    state: dict = {}
    events, steps, starts = [], [], {}
    async for ev in _live_turn(deps, message, state):
        now = time.perf_counter()
        if ev["type"] == "TOOL_CALL_START":
            starts[ev["toolCallId"]] = (ev["toolCallName"], now)
        elif ev["type"] == "TOOL_CALL_RESULT" and ev["toolCallId"] in starts:
            name, st = starts.pop(ev["toolCallId"])
            steps.append({"name": name, "ms": int((now - st) * 1000), "ok": '"error"' not in ev.get("content", "")[:40]})
        elif ev.get("name") == "wellops.verification":
            v = ev["value"]
            steps.append({"name": "verify", "ms": 0, "ok": v["ok"], "detail": f"{sum(g['ok'] for g in v['gates'])}/{len(v['gates'])} gates"
                          + (f", {v['retries']} retried" if v["retries"] else "")})
        events.append(ev)
        yield _sse(ev)
    ms = int((time.perf_counter() - t0) * 1000)
    trace = {"type": "CUSTOM", "name": "wellops.trace", "value": {"steps": steps, "total_ms": ms}}
    yield _sse(trace)
    actions = {"dispute_data", "remember_correction", "ingest_file", "generate_image", "recall_conversations"}
    if state.get("ok") and state.get("spec") and not any(st["name"] in actions for st in steps):  # actions never replay
        sp = state["spec"]
        handles = {h: (st.result.model_dump(mode="json"), st.rows) for h in
                   {v.handle for v in sp.visuals if v.handle} | {c.handle for c in sp.claims}
                   if (st := store().get(deps.chat_id, h))}
        cache.put_turn(key, message, {"events": [*events, trace], "handles": handles}, ms)
    yield _sse({"type": "RUN_FINISHED", "threadId": thread_id, "runId": run_id})
