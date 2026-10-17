"""Deterministic distillation: a verified tool path with literal arguments -> a reusable, templated skill.

Literals are generalized into slots: well ids -> {well}, areas -> {area} / {area_scope}, dates -> {window.start} /
{window.end}, relative periods -> {period}, record ids -> {record_id}, top_k -> {n}. Categorical choices that define
the method (group_by="well", show_visual type="bar") stay literal, so two questions that differ only in which area or
period they ask about distill to the same signature.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from typing import Any

META_TOOLS = {"find_skills", "get_skill", "list_skills", "record_outcome", "create_skill", "export_skill_md"}
NOT_LEARNED = {"generate_image", "ingest_file"}  # side effects / one-off content: never distilled
SETUP_TOOLS = {"resolve_entity"}
RENDER_TOOLS = {"show_visual"}
DROP_ARGS = {"handle", "title", "x", "y", "series"}  # per-turn plumbing, chosen from the result at run time

WELL = re.compile(r"\bWELL-\d{3,5}\b", re.I)
RECORD = re.compile(r"\b(?:INS|MNT|CTX|REG|UP|SR|NOV)-[\w-]*\d[\w-]*\b")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+Z?)?\b")
AREA_ID = re.compile(r"\b(?:NE|SW|NW|SE|N|S|E|W)-\d{2}\b")
WELL_TYPE = re.compile(r"\bT\d{3}\b")
PERIOD = re.compile(r"\b(?:(?:in\s+)?the\s+)?(?:last|past|previous|prior|next)\s+(?:\d+|one|two|three|four|five|six|"
                    r"seven|eight|nine|ten|twelve|few|several)?\s*(?:days?|weeks?|months?|quarters?|years?)\b|"
                    r"\b(?:this|last)\s+(?:week|month|quarter|year)\b|\b(?:yesterday|today)\b|"
                    r"\bsince\s+\{date\}|\bbetween\s+\{date\}\s+and\s+\{date\}", re.I)


@lru_cache(maxsize=4)
def _vocab(db_path: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Area names and ids from the substrate (longest names first so 'Northeast Block 8' beats 'Northeast')."""
    from wellops.tools import get_db
    try:
        rows = get_db().rows("SELECT DISTINCT name, area_id FROM areas")
    except Exception:  # noqa: BLE001 - no substrate (bare store tests): regexes still work
        return (), ()
    names = tuple(sorted({r["name"] for r in rows if r.get("name")}, key=len, reverse=True))
    return names, tuple(r["area_id"] for r in rows if r.get("area_id"))


def vocab() -> tuple[tuple[str, ...], tuple[str, ...]]:
    from wellops.tools import get_db
    return _vocab(get_db().path)


def is_area(s: str) -> bool:
    names, ids = vocab()
    s = s.strip().lower()
    return s in {n.lower() for n in names} or s in {i.lower() for i in ids} or bool(AREA_ID.fullmatch(s.upper()))


def normalize_text(text: str) -> str:
    """Replace entity mentions inside free text with slot names."""
    names, ids = vocab()
    out = text
    for n in names:
        out = re.sub(rf"\b{re.escape(n)}\b", "{area}", out, flags=re.I)
    for i in ids:
        out = re.sub(rf"\b{re.escape(i)}\b", "{area}", out, flags=re.I)
    out = AREA_ID.sub("{area}", out)
    out = WELL.sub("{well}", out)
    out = RECORD.sub("{record_id}", out)
    out = DATE.sub("{date}", out)
    out = WELL_TYPE.sub("{well_type}", out)
    out = PERIOD.sub("{period}", out)
    return re.sub(r"\s+", " ", out).strip()


def _scope(v: Any) -> Any:
    if isinstance(v, dict):
        kind, ids = str(v.get("kind") or ""), v.get("ids") or []
        if kind == "all":
            return v
        if kind == "area":
            return "{area_scope}"
        if kind == "well_type":
            return "{well_type_scope}"
        if kind in ("wells", "well"):
            return "{well_scope}" if len(ids) <= 1 else "{wells_scope}"
        return {k: _value("scope." + k, x) for k, x in v.items()}
    if isinstance(v, list):
        return "{wells_scope}" if v and all(isinstance(x, str) and WELL.fullmatch(x) for x in v) else [_value("scope", x) for x in v]
    if isinstance(v, str):
        if WELL.fullmatch(v.strip()):
            return "{well_scope}"
        if is_area(v):
            return "{area_scope}"
        if WELL_TYPE.fullmatch(v.strip()):
            return "{well_type_scope}"
        return normalize_text(v)
    return v


def _value(key: str, v: Any, tool: str = "") -> Any:
    k = key.lower()
    if k == "scope":
        return _scope(v)
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, int | float):
        return "{n}" if k in ("top_k", "limit", "n") else v
    if isinstance(v, dict):
        if k == "changes":
            return {ck: "{value}" for ck in v}
        return {ck: _value(ck, cv, tool) for ck, cv in v.items()}
    if isinstance(v, list):
        if v and all(isinstance(x, str) and WELL.fullmatch(x) for x in v):
            return "{wells}"
        return [_value(k, x, tool) for x in v]
    s = str(v)
    if DATE.fullmatch(s.strip()):
        return {"start": "{window.start}", "end": "{window.end}"}.get(k, "{date}")
    if k in ("query", "question", "prompt", "reason", "value_text") and tool not in ("sql",):
        return "{" + k + "}"
    if WELL.fullmatch(s.strip()):
        return "{well}"
    if RECORD.fullmatch(s.strip()):
        return "{record_id}"
    if is_area(s):
        return "{area}"
    if WELL_TYPE.fullmatch(s.strip()):
        return "{well_type}"
    return normalize_text(s)


def generalize(tool: str, args: dict[str, Any] | None) -> dict[str, Any]:
    args = {k: v for k, v in (args or {}).items() if v is not None and k not in ("ctx",)}
    if tool in RENDER_TOOLS:
        return {"type": args["type"]} if "type" in args else {}
    return {k: _value(k, v, tool) for k, v in args.items() if k not in DROP_ARGS}


def steps_from_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[{"name"/"tool", "args"}] in call order -> templated steps, meta tools removed, repeats collapsed."""
    out: list[dict[str, Any]] = []
    for c in calls:
        tool = str(c.get("tool") or c.get("name") or "").removeprefix("mcp__wellops__")
        if not tool or tool in META_TOOLS:
            continue
        args = c.get("args")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        step = {"tool": tool, "args": generalize(tool, args if isinstance(args, dict) else {})}
        if out and out[-1] == step:
            continue
        if tool in RENDER_TOOLS and out and out[-1]["tool"] in RENDER_TOOLS:
            out[-1] = step  # the last visual the model settled on
            continue
        out.append(step)
    return out


@lru_cache(maxsize=1)
def known_tools() -> frozenset[str]:
    from wellops import mcp_server
    return frozenset(t.name for t in mcp_server.mcp._tool_manager.list_tools())


def learnable(steps: list[dict[str, Any]]) -> bool:
    """A path is worth a skill if it has a real data step, uses only registered tools, and has no one-off actions."""
    tools = {s["tool"] for s in steps}
    return bool(tools - SETUP_TOOLS - RENDER_TOOLS) and tools <= known_tools() and not (tools & NOT_LEARNED)


def signature(steps: list[dict[str, Any]]) -> str:
    """Method identity: the tool sequence plus the arguments that define the method. resolve_entity's free text and
    repeated setup calls are ignored (models phrase and split them differently)."""
    sig = []
    for s in steps:
        if s["tool"] in SETUP_TOOLS:
            if not sig or sig[-1] != ["resolve_entity"]:
                sig.append(["resolve_entity"])
            continue
        sig.append([s["tool"], sorted((k, json.dumps(v, sort_keys=True)) for k, v in s["args"].items())])
    return json.dumps(sig)


def step_tokens(steps: list[dict[str, Any]]) -> set[str]:
    toks = set()
    for s in steps:
        toks.add(s["tool"])
        if s["tool"] not in SETUP_TOOLS:
            toks |= {f"{s['tool']}.{k}={json.dumps(v, sort_keys=True)}" for k, v in s["args"].items()}
    return toks


def similarity(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> float:
    ta, tb = step_tokens(a), step_tokens(b)
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


def primary(steps: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The data step the answer is built on: the last non-setup, non-render step."""
    data = [s for s in steps if s["tool"] not in SETUP_TOOLS | RENDER_TOOLS]
    return data[-1] if data else None


def render_step(s: dict[str, Any]) -> str:
    def fmt(v: Any) -> str:
        if isinstance(v, str) and re.fullmatch(r"\{[\w.]+\}", v):
            return v
        return json.dumps(v, default=str)
    return f"{s['tool']}(" + ", ".join(f"{k}={fmt(v)}" for k, v in s.get("args", {}).items()) + ")"


def parse_step(text: str) -> dict[str, Any]:
    """'downtime(scope={area_scope}, group_by="well")' -> {"tool": "downtime", "args": {...}} (for hand-written skills)."""
    m = re.match(r"\s*([\w.]+)\s*(?:\((.*)\))?\s*$", text, re.S)
    if not m:
        return {"tool": text.strip(), "args": {}}
    args: dict[str, Any] = {}
    body, depth, cur, parts = m.group(2) or "", 0, "", []
    for ch in body:
        if ch in "[{(":
            depth += 1
        elif ch in "]})":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur)
    for p in parts:
        if "=" not in p:
            continue
        k, v = p.split("=", 1)
        v = v.strip()
        if re.fullmatch(r"\{[\w.]+\}", v):
            args[k.strip()] = v
            continue
        try:
            args[k.strip()] = json.loads(v)
        except ValueError:
            args[k.strip()] = v.strip("'\"")
    return {"tool": m.group(1), "args": args}


# --- naming --------------------------------------------------------------------------------------------------------

MEASURE = {"downtime": "downtime", "spills": "spills", "repair_costs": "repair-costs", "well_status": "well-status",
           "inspection_series": "inspection-series", "obligations": "obligations", "rca_candidates": "rca-candidates",
           "evidence_chain": "evidence-chain", "read_doc": "doc-section", "search_docs": "doc-search",
           "search_knowledge": "knowledge", "sql": "sql-query", "propose_correction": "correction",
           "remember_correction": "feedback", "recall_conversations": "recall", "analyze_image": "image-analysis",
           "list_media": "media"}
VISUAL_WORD = {"bar": "bar chart", "line": "line chart", "table": "table", "map": "map", "heatmap": "heatmap",
               "status_card": "status card", "obligation_timeline": "obligation timeline", "kpi": "KPI tile",
               "gauge": "gauge", "evidence_chain": "evidence chain", "timeline": "timeline"}


def _scope_of(args: dict[str, Any]) -> str:
    blob = json.dumps(args)
    if "{area" in blob:
        return "area"
    if "{wells" in blob:
        return "wells"
    if "{well_type" in blob:
        return "well-type"
    if "{well" in blob or "{record_id}" in blob:
        return "well"
    return "fleet"


def name_for(steps: list[dict[str, Any]]) -> str:
    p = primary(steps) or {"tool": "lookup", "args": {}}
    measure = MEASURE.get(p["tool"], p["tool"].replace("_", "-"))
    a = p["args"]
    scope = {"area": "in-area", "wells": "for-wells", "well-type": "for-well-type", "well": "for-well",
             "fleet": "across-fleet"}[_scope_of(a)]
    gb = a.get("group_by")
    if a.get("top_k") is not None:
        head = f"rank-{measure}"
    elif gb in ("month", "year"):
        head = f"{measure}-trend"
    elif gb in ("area_month",):
        head = f"{measure}-area-by-month"
    elif isinstance(gb, str) and gb not in ("none",):
        head = f"{measure}-by-{gb.replace('_', '-')}"
    elif p["tool"] in ("well_status", "evidence_chain", "inspection_series", "read_doc", "search_knowledge",
                       "propose_correction", "remember_correction"):
        head = measure if p["tool"] != "well_status" else ("explain-well-status" if "{well" in json.dumps(a) else measure)
    else:
        head = f"total-{measure}" if gb == "none" else f"list-{measure}"
    return f"{head}-{scope}"


def describe(steps: list[dict[str, Any]], visual: str | None) -> str:
    p = primary(steps) or {"tool": "lookup", "args": {}}
    a = p["args"]
    measure = MEASURE.get(p["tool"], p["tool"]).replace("-", " ")
    where = {"area": "in one area", "wells": "for a set of wells", "well-type": "for one well type",
             "well": "for one well or record", "fleet": "across the fleet"}[_scope_of(a)]
    when = " over a resolved date window" if "{window" in json.dumps(a) else ""
    how = ("Rank the top {n} " + (str(a.get("group_by") or "item") + "s") + f" by {measure}") if a.get("top_k") is not None \
        else (f"{measure.capitalize()} grouped by {a['group_by']}" if a.get("group_by") not in (None, "none")
              else f"{measure.capitalize()}")
    tail = f", shown as a {VISUAL_WORD.get(visual, visual)}" if visual and visual != "none" else ""
    path = " -> ".join(dict.fromkeys(s["tool"] for s in steps))
    return f"{how} {where}{when}{tail}. Learned from a verified turn ({path})."
