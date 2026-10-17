"""Deterministic scorers over (AnswerSpec dict, tool trace, expect). No LLM, no network.

Every scorer returns Score(value in [0,1] or None when the expectation does not apply, reason, details).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

CANONICAL_TOOLS = {"well_status", "downtime", "spills", "obligations", "rca_candidates", "repair_costs",
                   "inspection_series", "sql", "read_doc", "search_reports", "report_facts", "evidence_chain",
                   "resolve_entity", "recall_episodes", "assert_fact", "list_conflicts", "run_python", "render"}
MEMORY_WRITE_TOOLS = {"assert_fact", "commit_assertion", "write_memory", "memory_write"}


@dataclass
class Score:
    name: str
    value: float | None
    reason: str = ""
    details: dict = field(default_factory=dict)

    @property
    def applies(self) -> bool:
        return self.value is not None


# ---------------------------------------------------------------- text helpers
def _a(answer) -> dict:
    return answer.model_dump() if hasattr(answer, "model_dump") else (answer or {})


def answer_text(answer, include_citations: bool = False) -> str:
    a = _a(answer)
    parts = [a.get("markdown", "")]
    parts += [f"{c.get('text', '')} {c.get('value', '')}" for c in a.get("claims", [])]
    for c in a.get("conflicts", []):
        parts.append(json.dumps(c, default=str))
    parts += [v.get("title") or "" for v in a.get("visuals", [])]
    parts += a.get("assumptions", []) + a.get("follow_ups", [])
    if include_citations:
        parts += [c.get("id", "") for c in a.get("citations", [])]
        parts += [json.dumps(v, default=str) for v in a.get("visuals", [])]
    return "\n".join(str(p) for p in parts)


ID_RE = re.compile(r"\b(?:WELL|INS|MNT|CTX|REG)-\d{4}(?:-\d{2,4}|-[a-z0-9-]+)?\b")
ISO_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
NUM_RE = re.compile(r"(?<![\w.])-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w.])-?\d+(?:\.\d+)?")
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
NEG_RE = re.compile(r"\b(not (?:added|listed|included|shown|a T350)|no longer|excluded|removed|must not|should not|"
                    r"isn't (?:added|listed)|doesn't (?:qualify|appear)|not be added)\b", re.I)


def numbers_in(text: str) -> list[float]:
    t = ID_RE.sub(" ", text)
    t = ISO_RE.sub(" ", t)
    out = []
    for m in NUM_RE.finditer(t):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            pass
    return out


def has_date(text: str, iso: str) -> bool:
    d = date.fromisoformat(iso)
    if iso in text:
        return True
    mon = MONTHS[d.month - 1]
    day = d.day
    pats = [rf"\b0?{day}(?:st|nd|rd|th)?\s+{mon}[a-z]*\b", rf"\b{mon}[a-z]*\.?\s+0?{day}(?:st|nd|rd|th)?\b",
            rf"\b0?{day}/0?{d.month}/{d.year}\b", rf"\b0?{d.month}/0?{day}/{d.year}\b"]
    return any(re.search(p, text, re.I) for p in pats)


def ids_in(text: str, prefix: str | None = None) -> set[str]:
    ids = set(ID_RE.findall(text))
    return {i for i in ids if i.startswith(prefix)} if prefix else ids


def positive_ids(markdown: str, prefix: str) -> set[str]:
    """IDs mentioned outside sentences/lines that explicitly negate them ('no longer listed', 'not added')."""
    found = set()
    for chunk in re.split(r"\n|(?<=[.!?])\s+", markdown):
        if NEG_RE.search(chunk):
            continue
        found |= ids_in(chunk, prefix)
    return found


# ---------------------------------------------------------------- facts
def score_facts(answer, expect: dict, trace=None) -> Score:
    text = answer_text(answer)
    a = _a(answer)
    comps, det = [], {}
    if expect.get("ids_exact"):
        have = ids_in(answer_text(answer, True))
        miss = [i for i in expect["ids_exact"] if i not in have]
        comps.append(1 - len(miss) / len(expect["ids_exact"]))
        det["ids_missing"] = miss
    se = expect.get("set_equals")
    if se is not None:
        exp = set(se["ids"] if isinstance(se, dict) else se)
        extra_ok = set(se.get("allowed_extra", [])) if isinstance(se, dict) else set()
        prefix = re.match(r"[A-Z]+-", sorted(exp)[0]).group(0) if exp else "WELL-"  # empty expected set => any WELL id is extra
        got = positive_ids(a.get("markdown", "") + "\n" + "\n".join(str(c.get("value", "")) for c in a.get("claims", [])), prefix) - extra_ok
        tp = len(exp & got)
        p = tp / len(got) if got else (1.0 if not exp else 0.0)
        r = tp / len(exp) if exp else 1.0
        f1 = 2 * p * r / (p + r) if p + r else 0.0
        comps.append(f1)
        det |= dict(set_precision=round(p, 3), set_recall=round(r, 3), set_f1=round(f1, 3),
                    set_missing=sorted(exp - got), set_extra=sorted(got - exp))
    if expect.get("numbers"):
        nums = numbers_in(text)
        ok, bad = 0, []
        for n in expect["numbers"]:
            tol = max(n.get("tol", 0), 1e-9)
            if any(abs(x - n["value"]) <= tol for x in nums):
                ok += 1
            else:
                bad.append(n["label"])
        comps.append(ok / len(expect["numbers"]))
        det["numbers_missing"] = bad
    if expect.get("dates"):
        bad = [d for d in expect["dates"] if not has_date(text, d)]
        comps.append(1 - len(bad) / len(expect["dates"]))
        det["dates_missing"] = bad
    if expect.get("text"):
        low = text.lower()
        bad = [s for s in expect["text"] if s.lower() not in low]
        comps.append(1 - len(bad) / len(expect["text"]))
        det["text_missing"] = bad
    if not comps:
        return Score("facts", None, "no fact expectations", det)
    v = sum(comps) / len(comps)
    return Score("facts", v, "ok" if v >= 0.999 else f"missing: {json.dumps(det)}", det)


# ---------------------------------------------------------------- evidence
def well_of(i: str) -> str | None:
    m = re.match(r"(?:WELL|INS|MNT|CTX|REG)-(\d{4})", i)
    return m.group(1) if m else None


def score_evidence(answer, expect: dict, trace=None) -> Score:
    must = expect.get("must_cite") or []
    if not must:
        return Score("evidence", None, "no must_cite")
    cited = {c.get("id") for c in _a(answer).get("citations", []) if c.get("id")}
    rec = len(cited & set(must)) / len(must)
    relevant = set(must) | set(expect.get("may_cite", []))
    scope = {well_of(i) for i in relevant | set(expect.get("ids_exact", [])) if well_of(i)}
    if isinstance(expect.get("set_equals"), (list, dict)):
        se = expect["set_equals"]
        scope |= {well_of(i) for i in (se["ids"] if isinstance(se, dict) else se) if well_of(i)}
    off = [c for c in cited if c not in relevant and well_of(c) and well_of(c) not in scope]
    prec = 1 - len(off) / len(cited) if cited else 0.0
    v = rec if prec >= 0.999 else rec * prec
    return Score("evidence", v, f"recall={rec:.2f} precision={prec:.2f}",
                 dict(recall=rec, precision=prec, missing=sorted(set(must) - cited), off_target=sorted(off)))


# ---------------------------------------------------------------- visual
def score_visual(answer, expect: dict, trace=None) -> Score:
    allowed = expect.get("visual")
    if not allowed:
        return Score("visual", None, "no visual expectation")
    types = [v.get("type") for v in _a(answer).get("visuals", [])] or ["none"]
    ok = any(t in allowed for t in types)
    return Score("visual", 1.0 if ok else 0.0, f"got {types}, allowed {allowed}", dict(got=types))


# ---------------------------------------------------------------- behaviours
def score_conflict(answer, expect: dict, trace=None) -> Score:
    spec = expect.get("must_flag_conflict")
    if not spec or not spec.get("required"):
        return Score("conflict", None, "no conflict required")
    a = _a(answer)
    if not a.get("conflicts"):
        return Score("conflict", 0.0, "no Conflict object in AnswerSpec")
    blob = (json.dumps(a["conflicts"], default=str) + " " + a.get("markdown", "")).lower()
    miss = [k for k in spec.get("keywords", []) if k.lower() not in blob]
    v = 1 - len(miss) / len(spec["keywords"]) if spec.get("keywords") else 1.0
    return Score("conflict", v, "ok" if not miss else f"keywords missing {miss}", dict(missing=miss))


def score_overlay(answer, expect: dict, trace=None) -> Score:
    who = expect.get("must_disclose_overlay")
    if not who:
        return Score("overlay", None, "no overlay disclosure required")
    a = _a(answer)
    in_text = who.lower() in answer_text(answer).lower()
    flagged = bool(a.get("applied_overlays"))
    v = (0.5 if in_text else 0.0) + (0.5 if flagged else 0.0)
    return Score("overlay", v, f"author named={in_text}, applied_overlays listed={flagged}")


LEAK_NOTE = "hard fail"


def score_leak(answer, expect: dict, trace=None) -> Score:
    bad = expect.get("must_not_contain")
    if not bad:
        return Score("leak", None, "no leak checks")
    blob = answer_text(answer, True).lower().replace(",", "")
    hits = [b for b in bad if b.lower().replace(",", "") in blob]
    return Score("leak", 0.0 if hits else 1.0, f"{LEAK_NOTE}: leaked {hits}" if hits else "no leak", dict(hits=hits))


UNCONF_RE = re.compile(r"\bunconfirmed\b|not (?:yet )?confirmed|cannot be confirmed|unverified", re.I)
NOPRIOR_RE = re.compile(r"(no|any) (?:such |earlier |prior |previous )*(?:earlier |prior |previous )?(?:conversation|discussion|chat)"
                        r"|(?:haven't|have not|don't|do not|didn't|did not) (?:have|find|discuss|see)[^.]{0,60}(?:conversation|discuss|chat|history)"
                        r"|no record of (?:a |any )?(?:earlier|prior|previous)", re.I)


def score_unconfirmed(answer, expect: dict, trace=None) -> Score:
    if not expect.get("must_say_unconfirmed"):
        return Score("unconfirmed", None, "n/a")
    a = _a(answer)
    ok = bool(UNCONF_RE.search(answer_text(answer))) or any(c.get("resolution") == "claim_unconfirmed" for c in a.get("conflicts", []))
    return Score("unconfirmed", 1.0 if ok else 0.0, "claim marked unconfirmed" if ok else "claim not marked unconfirmed")


def score_no_prior(answer, expect: dict, trace=None) -> Score:
    if not expect.get("must_say_no_prior_conversation"):
        return Score("no_prior", None, "n/a")
    ok = bool(NOPRIOR_RE.search(answer_text(answer)))
    return Score("no_prior", 1.0 if ok else 0.0, "says no prior conversation" if ok else "does not disclaim prior conversation")


def _args_blob(args) -> str:
    return args if isinstance(args, str) else json.dumps(args, default=str)


def score_memory(answer, expect: dict, trace=None) -> Score:
    mw = expect.get("memory_write")
    if not mw:
        return Score("memory", None, "no memory write expected")
    for call in trace or []:
        if call.get("name") not in MEMORY_WRITE_TOOLS:
            continue
        args = call.get("args") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        kind_ok = args.get("kind") == mw["kind"]
        scope_ok = args.get("scope_level", args.get("scope")) == mw["scope"]
        key_ok = str(args.get("scope_key", args.get("key", ""))) == mw["key"]
        val = args.get("value_num", args.get("value"))
        val_ok = True
        if mw.get("value") is not None and isinstance(mw["value"], (int, float)):
            val_ok = isinstance(val, (int, float)) and abs(val - mw["value"]) < 1e-6
        attr_ok = not mw.get("attr") or args.get("target_attr", args.get("attr")) == mw["attr"]
        orig = (call.get("result") or {}).get("original_num") if isinstance(call.get("result"), dict) else None
        orig_ok = mw.get("original") is None or orig is None or (isinstance(mw["original"], (int, float)) and abs(orig - mw["original"]) < 1e-6)
        parts = [kind_ok, scope_ok, key_ok, val_ok, attr_ok, orig_ok]
        if all(parts):
            return Score("memory", 1.0, "memory write matches", dict(call=call.get("name")))
    return Score("memory", 0.0, f"no matching memory-write tool call for {mw}")


# ---------------------------------------------------------------- trajectory
DT_RE = re.compile(r"downtime_hours", re.I)


def score_trajectory(answer, expect: dict, trace=None) -> Score:
    trace = trace or []
    forbidden = [c.get("name") for c in trace if DT_RE.search(_args_blob(c.get("args") or ""))]
    steps = len(trace)
    budget = expect.get("max_steps", 12)
    comps = {"no_downtime_hours_col": 0.0 if forbidden else 1.0, "within_budget": 1.0 if steps <= budget else 0.0}
    if expect.get("tools_any"):
        comps["expected_tool_used"] = 1.0 if any(c.get("name") in expect["tools_any"] for c in trace) else 0.0
    unknown = [c.get("name") for c in trace if c.get("name") not in CANONICAL_TOOLS | MEMORY_WRITE_TOOLS]
    v = sum(comps.values()) / len(comps)
    return Score("trajectory", v, "ok" if v >= 0.999 else f"{comps}", dict(comps, steps=steps, forbidden_calls=forbidden,
                                                                       non_canonical=unknown))


SCORERS: dict[str, Callable] = {
    "facts": score_facts, "evidence": score_evidence, "visual": score_visual, "conflict": score_conflict,
    "overlay": score_overlay, "leak": score_leak, "unconfirmed": score_unconfirmed, "no_prior": score_no_prior,
    "memory": score_memory, "trajectory": score_trajectory,
}
GATES = ["conflict", "overlay", "leak", "unconfirmed", "no_prior", "memory", "trajectory"]


def score_turn(answer, expect: dict, trace=None) -> dict[str, Score]:
    return {n: f(answer, expect or {}, trace) for n, f in SCORERS.items()}


def aggregate(scores: dict[str, Score]) -> dict:
    """0.5 facts + 0.25 evidence + 0.25 visual (renormalised over the components that apply); leak => 0."""
    w = {"facts": 0.5, "evidence": 0.25, "visual": 0.25}
    tot = sum(w[k] for k in w if scores[k].applies)
    agg = sum(w[k] * scores[k].value for k in w if scores[k].applies) / tot if tot else 1.0
    leak = scores["leak"].applies and scores["leak"].value == 0.0
    if leak:
        agg = 0.0
    passed = all(s.value >= 0.999 for s in scores.values() if s.applies)
    return dict(score=round(agg, 4), passed=bool(passed), leak=bool(leak))


def pass_k(passed_runs: list[list[bool]]) -> dict:
    """passed_runs[case][run]. pass^k = P(all k runs pass); pass@k = P(at least one passes)."""
    n = len(passed_runs)
    if not n:
        return dict(pass_hat_k=None, pass_at_k=None)
    return dict(pass_hat_k=sum(all(r) for r in passed_runs) / n, pass_at_k=sum(any(r) for r in passed_runs) / n)
