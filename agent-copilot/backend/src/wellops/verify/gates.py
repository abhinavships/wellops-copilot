"""L4 verification: hard gates over an AnswerSpec. Pure functions plus an output_validator adapter."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ModelRequest, ToolReturnPart, UserPromptPart

from wellops.contracts import AnswerSpec, Claim, RunDeps
from wellops.tools.core import Db, ToolOut, get_db, store

MAX_RETRIES = 2
NUM_RE = re.compile(r"(?<![\w.-])-?\d[\d,]*(?:\.\d+)?")
ID_RE = re.compile(r"\b(?:[A-Z]{2,5}-\d{2,4}(?:-\d{2,4})*|A-[0-9a-f]{8}|[A-Z]{2}-\d\d)\b")

ALLOWED_VISUALS = {
    "scalar": {"none", "table", "status_card", "kpi", "gauge"},
    "entity": {"status_card", "table", "evidence_chain", "none"},
    "entity_list": {"table", "status_card", "map", "evidence_chain", "conflict_card", "correction_diff", "none"},
    "timeseries": {"line", "bar", "table", "timeline", "none"},
    "category_measure": {"bar", "table", "kpi", "line", "none"},
    "geo_points": {"map", "table", "none"},
    "matrix": {"heatmap", "table", "none"},
    "obligation_set": {"obligation_timeline", "table", "none"},
    "diff": {"correction_diff", "table", "none"},
    "text": {"none", "table", "evidence_chain", "image", "ingest_report"},
}


class GateResult(BaseModel):
    name: str
    ok: bool
    details: list[str] = []


class Verification(BaseModel):
    ok: bool
    attempt: int
    gates: list[GateResult]

    def diff(self) -> str:
        return " | ".join(f"[{g.name}] " + "; ".join(g.details[:5]) for g in self.gates if not g.ok)


VERIFICATIONS: dict[str, list[Verification]] = {}  # chat_id -> attempts of the latest run (read by the API)


def norm_num(tok: str) -> str:
    t = tok.replace(",", "")
    try:
        f = float(t)
    except ValueError:
        return t
    return str(int(f)) if f == int(f) else repr(round(f, 6))


def num_tokens(text: str) -> set[str]:
    return {norm_num(m) for m in NUM_RE.findall(text)}


def _flatten(obj: Any) -> str:
    return json.dumps(obj, default=str, ensure_ascii=False)


def collect_results(ctx_messages: list, chat_id: str, db: Db | None = None) -> tuple[list[ToolOut], str, str]:
    """Tool results of this conversation turn (full stored rows), the raw tool-return text, and the user text."""
    results, raw, user = [], [], []
    seen: set[str] = set()
    st = store(db)
    for m in ctx_messages:
        for p in getattr(m, "parts", []):
            if isinstance(p, UserPromptPart):
                user.append(p.content if isinstance(p.content, str) else _flatten(p.content))
            elif isinstance(p, ToolReturnPart):
                c = p.content
                raw.append(_flatten(c.model_dump() if isinstance(c, BaseModel) else c))
                h = c.get("handle") if isinstance(c, dict) else getattr(c, "handle", None)
                if h and h not in seen and (s := st.get(chat_id, h)):
                    seen.add(h)
                    results.append(s.result)
    return results, "\n".join(raw), "\n".join(user)


def _find_row(rows: list[dict], row_key: dict | None) -> dict | None:
    if not rows:
        return None
    if not row_key:
        return rows[0] if len(rows) == 1 else None
    for r in rows:
        if all(str(r.get(k)) == str(v) for k, v in row_key.items()):
            return r
    return None


def _equal(cell: Any, value: Any) -> bool:
    if isinstance(cell, bool) or isinstance(value, bool):
        return str(cell).lower() == str(value).lower()
    try:
        a, b = float(cell), float(str(value).replace(",", ""))
        dec = len(str(value).split(".")[1]) if "." in str(value) else 0
        return abs(a - b) <= max(0.5 * 10 ** -dec, 1e-9)
    except (TypeError, ValueError):
        return str(cell).strip().lower() == str(value).strip().lower() or str(value).lower() in str(cell).lower()


def check_claim(db: Db, chat_id: str, c: Claim, cache: dict) -> str | None:
    st = store(db).get(chat_id, c.handle)
    if not st:
        return f"claim '{c.text}': unknown handle {c.handle}"
    if c.column in ("row_count", "count", None) and not c.row_key and len(st.rows) != 1:  # "15 wells" = size of the result
        n = st.result.row_count
        return None if _equal(n, c.value) else f"claim '{c.text}': {c.handle} has {n} rows, not {c.value!r}"
    row = _find_row(st.rows, c.row_key)
    if row is None:
        return f"claim '{c.text}': no row {c.row_key} in {c.handle}"
    if c.column:
        if c.column not in row:
            return f"claim '{c.text}': column {c.column} not in {c.handle} ({st.result.columns})"
        if not _equal(row[c.column], c.value):
            return f"claim '{c.text}': {c.handle}.{c.column} is {row[c.column]!r}, not {c.value!r}"
    elif not any(_equal(v, c.value) for v in row.values()):
        return f"claim '{c.text}': value {c.value!r} not in row {c.row_key} of {c.handle}"
    sql = st.result.sql
    if sql:  # re-execute; only judged when the claimed cell is derivable from the SQL
        if sql not in cache:
            try:
                cache[sql] = db.q(sql)
            except Exception as e:
                cache[sql] = (None, [str(e)])
        cols, fresh = cache[sql]
        if cols is None:
            return f"{c.handle}: re-execution failed ({fresh[0][:80]})"
        if c.column in cols and (not c.row_key or all(k in cols for k in c.row_key)):
            fr = _find_row(fresh, c.row_key)
            if fr is not None and not _equal(fr[c.column], c.value):
                return f"claim '{c.text}': re-executing {c.handle} gives {c.column}={fr[c.column]!r}, not {c.value!r}"
    return None


def verify(deps: RunDeps, spec: AnswerSpec, results: list[ToolOut], raw_tools: str = "", user_text: str = "",
           attempt: int = 0, db: Db | None = None) -> Verification:
    db = db or get_db()
    st = store(db)
    gates: list[GateResult] = []
    used_h = {c.handle for c in spec.claims} | {v.handle for v in spec.visuals if v.handle}
    used = [r for r in results if r.handle in used_h] or results

    # 1. grounding
    allowed_nums = num_tokens(user_text) | num_tokens(raw_tools)
    allowed_ids = set(ID_RE.findall(user_text + raw_tools))
    for r in results:
        s = st.get(deps.chat_id, r.handle)
        blob = _flatten(s.rows) if s else ""
        allowed_nums |= num_tokens(blob) | num_tokens(_flatten([c.model_dump() for c in r.citations]))
        allowed_ids |= set(ID_RE.findall(blob)) | {c.id for c in r.citations}
    for c in spec.claims:
        allowed_nums |= num_tokens(f"{c.text} {c.value}")
        allowed_ids |= set(ID_RE.findall(f"{c.text} {c.value}"))
    allowed_ids |= {c.id for c in spec.citations} | set(spec.applied_overlays)
    md = re.sub(r"(?m)^\s*(?:[-*]|\d+[.)])\s+", "", spec.markdown)
    ids = set(ID_RE.findall(md))
    md_nums = NUM_RE.findall(ID_RE.sub(" ", md))
    bad_ids = sorted(i for i in ids if i not in allowed_ids)
    bad_nums = sorted({n for n in md_nums if norm_num(n) not in allowed_nums})
    det = [f"id not in any tool result/claim: {i}" for i in bad_ids] + \
          [f"number not in any tool result/claim: {n}" for n in bad_nums]
    gates.append(GateResult(name="grounding", ok=not det, details=det))

    # 2. claim binding + re-execution
    cache: dict = {}
    det = [x for x in (check_claim(db, deps.chat_id, c, cache) for c in spec.claims) if x]
    gates.append(GateResult(name="claims", ok=not det, details=det))

    # 3. disclosure of applied overlays (with author)
    det = []
    ov = list(dict.fromkeys(o for r in used for o in r.applied_overlays))
    if ov:
        auth = {r["assertion_id"]: r["who"] for r in db.rows(
            "SELECT a.assertion_id, coalesce(u.name, a.author_user_id) AS who FROM assertions a LEFT JOIN users u "
            "ON u.dataset_id=a.dataset_id AND u.user_id=a.author_user_id WHERE a.assertion_id IN (SELECT unnest(?::VARCHAR[]))",
            [ov])}
        low = spec.markdown.lower()
        for o in ov:
            if o not in spec.applied_overlays:
                det.append(f"overlay {o} was applied to the data but is not in applied_overlays")
            who = auth.get(o)
            if who and who.lower() not in low and who.lower().split()[-1] not in low:
                det.append(f"overlay {o}: say who made it ({who}) in the markdown")
    gates.append(GateResult(name="disclosure", ok=not det, details=det))

    # 4. conflicts surfaced
    have = {c.subject for c in spec.conflicts}
    det = [f"conflict not surfaced: {c.subject}" for c in
           {c.subject: c for r in results for c in r.conflicts}.values() if c.subject not in have]
    gates.append(GateResult(name="conflicts", ok=not det, details=det))

    # 5. visual fit
    det = []
    for v in spec.visuals:
        if not v.handle:
            continue
        s = st.get(deps.chat_id, v.handle)
        if not s:
            det.append(f"visual {v.type}: unknown handle {v.handle}")
        elif v.type not in ALLOWED_VISUALS[s.result.shape] and not v.requested_by_user:
            det.append(f"visual {v.type} does not fit shape {s.result.shape} (allowed: {sorted(ALLOWED_VISUALS[s.result.shape])}); "
                       "set requested_by_user only if the user asked for it")
    gates.append(GateResult(name="visual_fit", ok=not det, details=det))

    # 6. assumptions for windowed / as-of queries
    windowed = any(n.startswith("window") for r in results for n in r.notes)
    det = ["state the window, as-of and dataset in assumptions"] if windowed and not spec.assumptions else []
    gates.append(GateResult(name="assumptions", ok=not det, details=det))
    return Verification(ok=all(g.ok for g in gates), attempt=attempt, gates=gates)


def run_validator(ctx: RunContext[RunDeps], spec: AnswerSpec) -> AnswerSpec:
    """Adapter for Agent.output_validator: ModelRetry with a concise diff, at most MAX_RETRIES times."""
    results, raw, user = collect_results(ctx.messages, ctx.deps.chat_id)
    v = verify(ctx.deps, spec, results, raw, user, attempt=ctx.retry)
    if ctx.retry == 0:
        VERIFICATIONS[ctx.deps.chat_id] = []
    VERIFICATIONS.setdefault(ctx.deps.chat_id, []).append(v)
    if not v.ok and ctx.retry < MAX_RETRIES:
        raise ModelRetry("Verification failed. Fix and answer again: " + v.diff())
    return spec
