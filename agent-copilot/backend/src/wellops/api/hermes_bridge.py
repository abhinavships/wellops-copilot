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
MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
HISTORY: dict[str, list[dict]] = {}  # chat_id -> Hermes (OpenAI-format) message history

SYSTEM = """You are WellOps, an oil-field asset copilot. Today is {today}; the user is {principal}.
Rules:
- Answer ONLY what was asked, in 1-3 short sentences. No preamble, no lists of assumptions.
- Every number you state must come from a WellOps tool result in this turn. Never guess.
- Resolve areas and relative periods ("last 4 months") with resolve_entity first, then call the data tool.
- After getting data, call show_visual with the result handle and the visual that fits (status_card for one well,
  bar for rankings, line for time series, map for locations, heatmap for area x month, kpi for one number).
  If the user asks for a specific chart, use exactly that type.
- If the user corrects data or states a rule, call remember_correction, then re-run the affected query.
- If notes and sensor data disagree, say so plainly and name both sources.
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


async def stream_turn(deps: RunDeps, message: str, thread_id: str, run_id: str) -> AsyncIterator[str]:
    yield _sse({"type": "RUN_STARTED", "threadId": thread_id, "runId": run_id})
    history = HISTORY.get(deps.chat_id, [])
    system = SYSTEM.format(today=deps.today, principal=deps.principal)
    headers = {"X-User-Id": deps.principal, "X-Chat-Id": deps.chat_id, "X-Clock-Today": str(deps.today)}
    ask, attempts, spec, final_msgs = message, [], None, history
    mcp_server.UI_QUEUE.pop(deps.chat_id, None)
    mcp_server.TOOL_LOG.pop(deps.chat_id, None)
    for attempt in range(MAX_RETRIES + 1):
        req = dict(message=ask, history=final_msgs, system=system, headers=headers,
                   mcp_url="http://127.0.0.1:8000/mcp/", model=MODEL, api_key=settings.openrouter_api_key,
                   home=str(HERMES_DIR / "home" / re.sub(r"\W", "_", deps.chat_id)))
        mid, text, err = f"m-{uuid.uuid4().hex[:8]}", "", None
        yield _sse({"type": "TEXT_MESSAGE_START", "messageId": mid, "role": "assistant"})
        async for ev in _run_hermes(req):
            k = ev.get("ev")
            if k == "tool_start":
                yield _sse({"type": "TOOL_CALL_START", "toolCallId": ev["id"], "toolCallName": ev["name"].removeprefix("mcp__wellops__"), "parentMessageId": mid})
                yield _sse({"type": "TOOL_CALL_ARGS", "toolCallId": ev["id"], "delta": json.dumps(ev.get("args"), default=str)})
                yield _sse({"type": "TOOL_CALL_END", "toolCallId": ev["id"]})
            elif k == "tool_end":
                yield _sse({"type": "TOOL_CALL_RESULT", "toolCallId": ev["id"], "messageId": mid, "content": ev.get("result", "")[:2000]})
            elif k == "delta":
                text += ev["text"]
                yield _sse({"type": "TEXT_MESSAGE_CONTENT", "messageId": mid, "delta": ev["text"]})
            elif k == "final":
                text = ev.get("text") or text
                final_msgs = ev.get("messages") or final_msgs
            elif k == "error":
                err = ev["message"]
        yield _sse({"type": "TEXT_MESSAGE_END", "messageId": mid})
        if err:
            yield _sse({"type": "RUN_ERROR", "message": f"Hermes: {err}"})
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
    yield _sse({"type": "CUSTOM", "name": "wellops.verification", "value": {
        "ok": last.ok, "retries": len(attempts) - 1, "harness": "hermes-agent",
        "gates": [{"name": g.name, "ok": g.ok, "detail": "; ".join(g.details) or None} for g in last.gates]}})
    yield _sse({"type": "CUSTOM", "name": "wellops.usage", "value": {"model": f"hermes-agent → {MODEL}"}})
    yield _sse({"type": "CUSTOM", "name": "wellops.answer", "value": spec.model_dump(mode="json")})
    yield _sse({"type": "RUN_FINISHED", "threadId": thread_id, "runId": run_id})
