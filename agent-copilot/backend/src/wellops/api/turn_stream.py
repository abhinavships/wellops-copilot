"""One AG-UI turn: SQLite hot cache -> native agent (Gemini, WellOps tool registry) -> verifier gates with up to two
corrective re-asks -> one focused generative-UI element -> wellops.* events and a measured run trace."""

from __future__ import annotations

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

from .native_agent import run_native
from wellops.qa import correctness

PROVIDERS = {  # provider -> (OpenAI-compatible base_url, default model, settings attr holding the key)
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-3.8-flash", "gemini_api_key"),  # LLM_MODEL=gemini-4-argon for max quality (45-53 s/turn)
}
BASE_URL, _DEFAULT_MODEL, _KEY_ATTR = PROVIDERS[settings.llm_provider]
MODEL = settings.llm_model or _DEFAULT_MODEL
FAST = {"reasoning_effort": "none", "temperature": 0.2}  # no hidden reasoning: answers are short and tool-grounded
HISTORY: dict[str, list] = {}  # chat_id -> agent message history

SYSTEM = """You are WellOps, an oil-field asset copilot. Today is {today}; the user is {principal}.
Rules:
- Lead with the direct answer in one sentence. Then, in at most 2 short sentences, the facts that support it:
  names, IDs (WELL-, INS-, MNT-, CTX-, REG-), dates and numbers exactly as the tools return them. No preamble.
  Refer to wells by ID and to areas, teams and people by name.
- Every number you state must come from a WellOps tool result in this turn. Never guess.
- Resolve areas and relative periods ("last 4 months") with resolve_entity first, then call the data tool.
- After getting data, call show_visual with the result handle. If the question names a visual (card, table, chart,
  map, heatmap) use exactly that. Otherwise: one well right now -> status_card; a list of wells, events, spills,
  repairs, rules or obligations -> table; a value over time -> line; comparing a few wells or areas -> bar;
  where things are -> map; two dimensions such as area x month -> heatmap; a single total -> no visual (or table).
- If the user says recorded data is wrong or gives corrected values, call propose_correction ONCE with every field
  they changed and their reason verbatim. Then report per field: applied / needs confirmation / rejected (why), and
  name any note that disagrees. After an applied correction, re-run the affected query (well_status or obligations)
  and state the consequence (e.g. "WELL-0164 is now producing", "now a reportable spill: SR-01 notice overdue").
- Status statements about a well ("it's running", "shut in since 14 Oct") also go to propose_correction as
  {{"producing": ...}}; planned work, shared facts and rules go to remember_correction.
- If notes and sensor data disagree, say so plainly and name both sources.
- For what a manual, SOP, repair report or note says, call search_knowledge and quote the passage with its source.
- For "why" questions use the reason_summary from well_status or call evidence_chain on the inspection id, and
  name the repair or note behind the reason (MNT-/CTX- id).
- Mention the key record id (e.g. INS-0001-0203) once, inline.
- To create an image, call generate_image (it is displayed automatically).
"""


def _sse(ev: dict) -> str:
    return f"data: {json.dumps(ev, default=str)}\n\n"


def _focus(deps: RunDeps, visuals: list[Visual]) -> list[Visual]:
    """Only what was asked: a proof card stands alone; otherwise one primary figure (plus a second only if it is a
    different kind of view). Visuals pointing at handles that don't exist are dropped."""
    disputes = [v for v in visuals if v.encodings.get("dispute_id")]
    if disputes:
        return disputes[-1:]
    seen, out = set(), []
    for v in visuals:
        st = store().get(deps.chat_id, v.handle) if v.handle else None
        if v.type in ("dispute", "none") or (v.handle and not st):
            continue
        if st and "snippet" in st.result.columns:  # retrieved passages read as source cards, never a wide table
            v = v.model_copy(update={"type": "sources", "title": v.title or "Sources"})
        k = (v.type, v.handle)
        if k in seen or any(o.type == v.type for o in out):
            continue
        seen.add(k)
        out.append(v)
    return out[:2]


ID_COLS = ("asset_id", "inspection_id", "maintenance_id", "event_id", "note_id", "ref", "obligation_id")
DATE_COLS = ("inspected_at", "date", "started_at", "completed_at", "due_at", "trigger_at", "created_at", "month")
SKIP_COLS = {"span", "same_text_count", "rank", "score", "snippet", "lat", "lon", "evidence_ids", "threshold_source", "sql", "explanation", "label", "obligation_id", "trigger_ref"}


def _cell(v) -> str:
    s = "" if v is None else str(v)
    return (s[:10] if len(s) >= 19 and s[4] == "-" and s[10] in "T " else s).replace("|", "/")[:80]


def _appendix(deps: RunDeps, visuals: list[Visual]) -> tuple[str, list]:
    """The rows behind the figure, as text, straight from the tool result (not model-written): every ID, date and
    number the figure shows is also in the answer, where the user (Show work), the verifier and graders can read it."""
    from wellops.contracts import Citation
    from wellops.tools.core import cite_kind
    for v in visuals:
        st = store().get(deps.chat_id, v.handle) if v.handle else None
        if not st or not st.rows:
            continue
        if "snippet" in st.result.columns:  # passages: list the sources, not a table of long text
            lines = [f"- {r.get('title') or r.get('id')} ({r.get('id')}): {str(r.get('snippet') or '')[:160]}" for r in st.rows[:6]]
            return "\n\n**Sources**\n\n" + "\n".join(lines), list(st.result.citations)
        cols = [c for c in st.result.columns if c not in SKIP_COLS and any(r.get(c) not in (None, "") for r in st.rows[:50])]
        cols = sorted(cols, key=lambda c: (c not in ID_COLS, c not in DATE_COLS))[:8]
        rows = st.rows[:40]
        table = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n" + "\n".join(
            "| " + " | ".join(_cell(r.get(c)) for c in cols) + " |" for r in rows)
        more = f"\n\n_{st.result.row_count - len(rows)} more rows in the figure._" if st.result.row_count > len(rows) else ""
        ids = {str(r[c]) for r in rows for c in ID_COLS if isinstance(r.get(c), str)}
        cites = [Citation(kind=cite_kind(i), id=i) for i in sorted(ids) if not i.startswith("WELL-")] + list(st.result.citations)
        return f"\n\n**Data** ({st.result.row_count} rows)\n\n{table}{more}", cites
    return "", []


def _compose(deps: RunDeps, text: str) -> AnswerSpec:
    visuals = mcp_server.UI_QUEUE.get(deps.chat_id, [])
    results = mcp_server.TOOL_LOG.get(deps.chat_id, [])
    if not visuals:  # never leave a data answer without UI
        from .app import DEFAULT_VISUAL
        for r in reversed(results):
            if r.shape in DEFAULT_VISUAL and r.row_count:
                visuals = [Visual(type=DEFAULT_VISUAL[r.shape], handle=r.handle)]
                break
    visuals = _typed(_focus(deps, visuals))
    appendix, shown = _appendix(deps, visuals)
    md = text.strip()
    # citations: records the figure shows, plus any record id the prose names (from this turn's results only)
    named = set(re.findall(r"\b(?:INS|MNT|CTX|REG)-\d{4}-\d{2,4}\b", md))
    pool = {c.id: c for r in results for c in r.citations}
    cites = list({c.id: c for c in [*shown, *(pool[i] for i in named if i in pool)]}.values())[:20] or list(pool.values())[:12]
    for v in visuals:  # a correction card's record and evidence are citations too
        if v.encodings.get("dispute_id"):
            from wellops.contracts import Citation
            cites += [Citation(kind=e["kind"] if e.get("kind") in ("inspection", "note", "assertion") else "inspection", id=e["id"])
                      for e in v.encodings.get("evidence", []) if e.get("id")]
    return AnswerSpec(markdown=md + appendix, visuals=visuals, citations=list({c.id: c for c in cites}.values()),
                      assumptions=list(dict.fromkeys(n for r in results for n in r.notes))[:4])


def _typed(visuals: list[Visual]) -> list[Visual]:
    """A correction card is a correction_diff once something was applied, a conflict_card when a claim or the change
    conflicts with the data, else a proof card (dispute) awaiting the user's decision."""
    out = []
    for v in visuals:
        e = v.encodings
        if v.type == "dispute" and e.get("dispute_id"):
            st = {c.get("status") for c in e.get("changes", [])}
            claim_unconfirmed = any(c.get("claim_status") == "unconfirmed" for c in e.get("changes", []))
            t = "conflict_card" if claim_unconfirmed or ("applied" not in st and e.get("conflicts")) else \
                "correction_diff" if "applied" in st else "dispute"
            v = v.model_copy(update={"type": t})
        out.append(v)
    return out


async def _live_turn(deps: RunDeps, message: str, state: dict) -> AsyncIterator[dict]:
    history = HISTORY.get(deps.chat_id, [])
    system = SYSTEM.format(today=deps.today, principal=deps.principal)
    headers = {"X-User-Id": deps.principal, "X-Chat-Id": deps.chat_id, "X-Clock-Today": str(deps.today)}
    ask, attempts, spec, final_msgs = message, [], None, history
    tool_texts: list[str] = []  # every raw tool result this turn, so evidence numbers count as grounded
    tool_args: list[dict] = []  # what the agent actually queried (the correctness check compares the window)
    verdict = None
    mcp_server.UI_QUEUE.pop(deps.chat_id, None)
    mcp_server.TOOL_LOG.pop(deps.chat_id, None)
    for attempt in range(MAX_RETRIES + 1):
        req = dict(message=ask, history=final_msgs, system=system, headers=headers,
                   model=MODEL, api_key=getattr(settings, _KEY_ATTR), base_url=BASE_URL, provider=settings.llm_provider,
                   request_overrides=FAST)
        mid, text, err = f"m-{uuid.uuid4().hex[:8]}", "", None
        yield ({"type": "TEXT_MESSAGE_START", "messageId": mid, "role": "assistant"})
        async for ev in run_native(deps, req):
            k = ev.get("ev")
            if k == "tool_start":
                tool_args.append(ev.get("args") or {})
                yield ({"type": "TOOL_CALL_START", "toolCallId": ev["id"], "toolCallName": ev["name"].removeprefix("mcp__wellops__"), "parentMessageId": mid})
                yield ({"type": "TOOL_CALL_ARGS", "toolCallId": ev["id"], "delta": json.dumps(ev.get("args"), default=str)})
                yield ({"type": "TOOL_CALL_END", "toolCallId": ev["id"]})
            elif k == "tool_end":
                tool_texts.append(str(ev.get("result", "")))
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
            err = text.strip()[:300]  # provider failure surfaced as text: never present it as an answer
        if err:
            yield ({"type": "RUN_ERROR", "message": f"Model provider: {err}" if "429" in err else f"Agent: {err}"})
            state["error"] = True
            return
        spec = _compose(deps, text)
        results = mcp_server.TOOL_LOG.get(deps.chat_id, [])
        raw = " ".join(r.model_dump_json() for r in results) + " " + " ".join(
            json.dumps(st.rows, default=str) for r in results if (st := store().get(deps.chat_id, r.handle))) + " " + " ".join(tool_texts)
        v = verify(deps, spec, results, raw_tools=raw, user_text=message, attempt=attempt)
        if not results and not text.rstrip().endswith("?"):  # an answer with no tool data is a guess (or a refusal)
            from wellops.verify.gates import GateResult
            v.gates.append(GateResult(name="tool_grounded", ok=False, details=["no WellOps tool was called"]))
            v.ok = False
        verdict = correctness.check(deps, message, spec.markdown, results, tool_args)  # right or wrong vs the data
        correctness.log(deps, message, verdict, attempt, MODEL)
        attempts.append(v)
        if (v.ok and verdict.verdict != "incorrect") or attempt == MAX_RETRIES:
            break
        problems = [p for p in (v.diff() if not v.ok else "", verdict.mismatches() if verdict.verdict == "incorrect" else "") if p]
        ask = ("Your answer is wrong or unsupported. Fix exactly these, calling tools again if needed (for a period like "
               "'last 4 months' use resolve_entity's window): " + " | ".join(problems))
    HISTORY[deps.chat_id] = final_msgs
    record(deps, message, spec)
    last = attempts[-1]
    yield ({"type": "CUSTOM", "name": "wellops.verification", "value": {
        "ok": last.ok, "retries": len(attempts) - 1, "harness": "wellops-native",
        "gates": [{"name": g.name, "ok": g.ok, "detail": "; ".join(g.details) or None} for g in last.gates]}})
    if verdict is not None:
        yield ({"type": "CUSTOM", "name": "wellops.correctness", "value": verdict.as_dict()})
    yield ({"type": "CUSTOM", "name": "wellops.usage", "value": {"model": MODEL}})
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
    key = cache.turn_key(message, deps.principal, deps.today, MODEL)
    hit = cache.get_turn(key) if settings.cache_enabled else None
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
        elif ev.get("name") == "wellops.correctness":
            c = ev["value"]
            bad = [x for x in c["checks"] if not x["ok"]]
            steps.append({"name": "correctness", "ms": 0, "ok": c["verdict"] != "incorrect",
                          "detail": {"correct": f"✓ right · {len(c['checks'])} checks", "incorrect": f"× wrong · {len(bad)} mismatch",
                                     "unverified": "not checkable"}[c["verdict"]]})
        elif ev.get("name") == "wellops.verification":
            v = ev["value"]
            steps.append({"name": "verify", "ms": 0, "ok": v["ok"], "detail": f"{sum(g['ok'] for g in v['gates'])}/{len(v['gates'])} gates"
                          + (f", {v['retries']} retried" if v["retries"] else "")})
        events.append(ev)
        yield _sse(ev)
    ms = int((time.perf_counter() - t0) * 1000)
    trace = {"type": "CUSTOM", "name": "wellops.trace", "value": {"steps": steps, "total_ms": ms}}
    yield _sse(trace)
    actions = {"propose_correction", "dispute_data", "remember_correction", "ingest_file", "generate_image", "recall_conversations"}
    if settings.cache_enabled and state.get("ok") and state.get("spec") and not any(st["name"] in actions for st in steps):  # actions never replay
        sp = state["spec"]
        handles = {h: (st.result.model_dump(mode="json"), st.rows) for h in
                   {v.handle for v in sp.visuals if v.handle} | {c.handle for c in sp.claims}
                   if (st := store().get(deps.chat_id, h))}
        cache.put_turn(key, message, {"events": [*events, trace], "handles": handles}, ms)
    yield _sse({"type": "RUN_FINISHED", "threadId": thread_id, "runId": run_id})
