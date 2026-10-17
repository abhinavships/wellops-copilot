"""Failure taxonomy: a deterministic classifier over one scored turn (scorer details + tool trace + answer meta).

No LLM. Every category is derived from evidence already in the turn record, and each hit carries the evidence that
triggered it, so the explorer can show *why* a turn was put in a bucket. Only failed turns are classified; a passing
turn has no failure category. A turn may fall in several categories; `primary` picks the root cause by priority
(a run error explains everything after it, a wrong date window explains the wrong numbers it produced, and so on).
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta

from evals.metrics.scorers import answer_text, numbers_in


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    description: str


# priority order: earlier = more fundamental (used to pick the primary category)
CATEGORIES: list[Category] = [
    Category("run_error", "Run error / timeout", "the target crashed, timed out or ended without an answer event"),
    Category("memory_leak", "Memory leak across users", "the answer contains another principal's private data (hard fail)"),
    Category("ungrounded_claim", "Ungrounded claim", "the verifier rejected the answer, or it states numbers no tool returned"),
    Category("wrong_window", "Wrong window", "the tools were called with a date window that does not match the question"),
    Category("wrong_number", "Wrong number", "an expected number is missing or off by more than its tolerance"),
    Category("correction_not_applied", "Correction not applied", "a correction was not written to memory or not applied as an overlay"),
    Category("conflict_not_shown", "Conflict not shown", "a disagreement between sources, or an unconfirmed claim, was not surfaced"),
    Category("invented_history", "Invented history", "the agent did not disclaim a prior conversation that never happened"),
    Category("missing_entity", "Missing entity", "an expected id, set member, name, date or phrase is absent (or extra ids were listed)"),
    Category("correction_not_disclosed", "Correction not disclosed", "an applied correction's author was not named in the answer"),
    Category("wrong_visual", "Wrong visual", "the chosen visual type is not one the question allows"),
    Category("missing_evidence", "Missing evidence", "a required citation is missing, or records outside the question's scope were cited"),
    Category("trajectory", "Wrong tool path", "forbidden column, step budget exceeded or the expected tool was never used"),
    Category("other", "Other", "the turn failed but no specific signal was found"),
]
BY_KEY = {c.key: c for c in CATEGORIES}
PRIORITY = {c.key: i for i, c in enumerate(CATEGORIES)}

WINDOW_FREE_TOOLS = {"resolve_entity", "show_visual", "sql", "read_doc", "search_reports", "recall_episodes",
                     "recall_conversations", "load_skill", "list_skills", "read_skill_file", "search_docs"}
TIMEOUT_RE = re.compile(r"timeout|timed out|ReadTimeout", re.I)
TRUNCATED_AT = 2000  # backend/src/wellops/api/hermes_bridge.py caps TOOL_CALL_RESULT content
NAMED_NUM_RE = re.compile(r"\b(?:Well|Block|Area|Pad|Unit)\s+\d+\b", re.I)  # "NE-08 Well 172" is a name, not a figure
RETRIES_RE = re.compile(r"maximum output retries|verifier|retries exhausted", re.I)


@dataclass
class Hit:
    category: str
    evidence: dict = field(default_factory=dict)


@dataclass
class Classification:
    primary: str | None
    categories: list[str]
    hits: list[Hit]

    def to_dict(self) -> dict:
        return dict(primary=self.primary, categories=self.categories, hits=[asdict(h) for h in self.hits])


# ---------------------------------------------------------------- helpers
def _obj(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _score(turn: dict, name: str) -> tuple[float | None, dict]:
    s = (turn.get("scores") or {}).get(name) or {}
    return s.get("value"), s.get("details") or {}


def _meta(turn: dict) -> dict:
    a = turn.get("answer") or {}
    return a.get("_meta") or {} if isinstance(a, dict) else {}


def expected_window(truth: dict | None, clock: str) -> tuple[date, date | None] | None:
    """The [start, end] window implied by a case's truth args (since/until, days, weeks, months, years, year)."""
    args = (truth or {}).get("args") or {}
    try:
        today = date.fromisoformat(clock)
    except (TypeError, ValueError):
        return None
    if args.get("since"):
        return date.fromisoformat(str(args["since"])), date.fromisoformat(str(args.get("until") or clock))
    if args.get("year"):
        y = int(args["year"])  # a calendar year: ending at the clock or at 31 Dec are both right for the current year
        return date(y, 1, 1), None if y == today.year else date(y, 12, 31)
    days = {"days": 1, "weeks": 7, "months": 30.44, "years": 365.25}
    for k, mult in days.items():
        if args.get(k):
            return today - timedelta(days=round(float(args[k]) * mult)), today
    return None


def _call_window(args) -> tuple[date, date | None] | None:
    a = _obj(args)
    if not isinstance(a, dict):
        return None
    s = a.get("start") or a.get("since") or a.get("from")
    e = a.get("end") or a.get("until") or a.get("to")
    try:
        return date.fromisoformat(str(s)[:10]), (date.fromisoformat(str(e)[:10]) if e else None)
    except (TypeError, ValueError):
        return None


def window_mismatch(turn: dict, truth: dict | None) -> dict | None:
    """Evidence dict when no windowed tool call matches the expected window (tolerance max(3 d, 5 % of its length))."""
    exp = expected_window(truth, turn.get("clock"))
    if not exp:
        return None
    calls = [(c.get("name"), _call_window(c.get("args"))) for c in turn.get("trace") or []
             if c.get("name") not in WINDOW_FREE_TOOLS]
    calls = [(n, w) for n, w in calls if w]
    if not calls:
        return None
    span = ((exp[1] or date.fromisoformat(turn["clock"])) - exp[0]).days
    tol = max(3, round(0.05 * span))
    for _, (s, e) in calls:
        if abs((s - exp[0]).days) <= tol and (e is None or exp[1] is None or abs((e - exp[1]).days) <= tol):
            return None
    return dict(expected=[exp[0].isoformat(), exp[1].isoformat() if exp[1] else None], tolerance_days=tol,
                called=[dict(tool=n, start=s.isoformat(), end=e.isoformat() if e else None) for n, (s, e) in calls])


def _results_blob(trace: list[dict]) -> str:
    parts = []
    for c in trace or []:
        r = c.get("result")
        parts.append(r if isinstance(r, str) else json.dumps(r, default=str))
    return "\n".join(parts)


def ungrounded_numbers(turn: dict) -> list[float]:
    """Numbers stated in the markdown that appear in no tool result, the question or the expectations.

    Only runs when the trace carries real tool payloads (the fake target's traces are row counts only), and ignores
    small integers (counts, ordinals, clock times) which are too ambiguous to judge without claim provenance. The API
    bridge truncates streamed tool results at TRUNCATED_AT chars; a turn with a truncated result is not judged."""
    trace = turn.get("trace") or []
    if any(isinstance(c.get("result"), str) and len(c["result"]) >= TRUNCATED_AT for c in trace):
        return []
    blob = _results_blob(trace)
    if len(re.sub(r'"row_count":\s*\d+', "", blob)) < 200:
        return []
    have = numbers_in(blob) + numbers_in(turn.get("say") or "")
    have += [n.get("value") for n in (turn.get("expect") or {}).get("numbers", []) if isinstance(n.get("value"), (int, float))]
    md = NAMED_NUM_RE.sub(" ", (turn.get("answer") or {}).get("markdown") or "")
    out = []
    for x in numbers_in(md):
        if float(x).is_integer() and abs(x) < 100:
            continue
        if 1900 <= x <= 2100:
            continue
        places = len(str(x).split(".")[1]) if "." in str(x) and not float(x).is_integer() else 0
        if not any(isinstance(h, (int, float)) and abs(round(h, places) - x) < 10 ** -places / 2 + 1e-9 for h in have):
            out.append(x)
    return sorted(set(out))


# ---------------------------------------------------------------- classifier
def classify_turn(turn: dict, truth: dict | None = None) -> Classification:
    """Classify one scored turn record (as written by evals.runner.episode). `truth` is the turn's `truth` block from
    the case file when known (used for the window check)."""
    if turn.get("setup"):
        return Classification(None, [], [])
    agg = turn.get("agg") or {}
    if agg.get("passed"):
        return Classification(None, [], [])
    hits: list[Hit] = []
    meta = _meta(turn)
    err = turn.get("error")
    run_err = meta.get("run_error")
    if err or (meta.get("no_answer_event") and (run_err or not (turn.get("answer") or {}).get("markdown"))):
        msg = (err or run_err or "no answer event").strip().splitlines()[0][:300]
        hits.append(Hit("run_error", dict(message=msg, timeout=bool(TIMEOUT_RE.search(msg)))))
        if RETRIES_RE.search(msg):
            hits.append(Hit("ungrounded_claim", dict(source="verifier", message=msg)))

    v, d = _score(turn, "leak")
    if v == 0.0:
        hits.append(Hit("memory_leak", dict(hits=d.get("hits", []))))

    # verifier gates streamed by the API (wellops.verification): a final failing gate means an ungrounded answer
    for ver in (meta.get("events") or {}).get("wellops.verification", []) or []:
        bad = [g.get("name") for g in (ver or {}).get("gates", []) if g.get("ok") is False]
        if (ver or {}).get("ok") is False or bad:
            hits.append(Hit("ungrounded_claim", dict(source="verifier", failed_gates=bad)))
            break
    stray = ungrounded_numbers(turn)
    if stray:
        hits.append(Hit("ungrounded_claim", dict(source="numbers_not_in_tool_results", numbers=stray[:12])))

    win = window_mismatch(turn, truth)
    if win:
        hits.append(Hit("wrong_window", win))

    v, d = _score(turn, "facts")
    if v is not None and v < 0.999:
        if d.get("numbers_missing"):
            hits.append(Hit("wrong_number", dict(numbers_missing=d["numbers_missing"])))
        miss = {k: d[k] for k in ("ids_missing", "set_missing", "set_extra", "dates_missing", "text_missing") if d.get(k)}
        if miss:
            hits.append(Hit("missing_entity", miss))

    v, d = _score(turn, "memory")
    if v is not None and v < 0.999:
        hits.append(Hit("correction_not_applied", dict(source="memory_write", expected=(turn.get("expect") or {}).get("memory_write"))))
    v, _ = _score(turn, "overlay")
    if v is not None and v < 0.999:
        who = (turn.get("expect") or {}).get("must_disclose_overlay")
        a = turn.get("answer") or {}
        if not a.get("applied_overlays"):
            hits.append(Hit("correction_not_applied", dict(source="overlay", applied_overlays=[])))
        if who and who.lower() not in answer_text(a).lower():
            hits.append(Hit("correction_not_disclosed", dict(author=who)))

    v, d = _score(turn, "conflict")
    if v is not None and v < 0.999:
        hits.append(Hit("conflict_not_shown", dict(source="conflict", keywords_missing=d.get("missing"),
                                                   no_conflict_object=not (turn.get("answer") or {}).get("conflicts"))))
    v, _ = _score(turn, "unconfirmed")
    if v is not None and v < 0.999:
        hits.append(Hit("conflict_not_shown", dict(source="unconfirmed", detail="contradicted claim not marked unconfirmed")))
    v, _ = _score(turn, "no_prior")
    if v is not None and v < 0.999:
        hits.append(Hit("invented_history", {}))

    v, d = _score(turn, "visual")
    if v is not None and v < 0.999:
        hits.append(Hit("wrong_visual", dict(got=d.get("got"), allowed=(turn.get("expect") or {}).get("visual"))))
    v, d = _score(turn, "evidence")
    if v is not None and v < 0.999:
        hits.append(Hit("missing_evidence", {k: d[k] for k in ("missing", "off_target", "recall", "precision") if k in d}))
    v, d = _score(turn, "trajectory")
    if v is not None and v < 0.999:
        hits.append(Hit("trajectory", {k: d[k] for k in ("no_downtime_hours_col", "within_budget", "expected_tool_used",
                                                          "steps", "forbidden_calls") if k in d}))
    if not hits:
        hits.append(Hit("other", dict(score=agg.get("score"))))
    cats = sorted({h.category for h in hits}, key=PRIORITY.__getitem__)
    return Classification(cats[0], cats, hits)


def classify_case(turns: list[dict], truths: dict[str, dict] | None = None) -> Classification:
    """Union over a case run's turns; primary = highest-priority category of any failed turn."""
    hits: list[Hit] = []
    for t in turns:
        hits += classify_turn(t, (truths or {}).get(t.get("turn_id"))).hits
    cats = sorted({h.category for h in hits}, key=PRIORITY.__getitem__)
    return Classification(cats[0] if cats else None, cats, hits)


def describe() -> list[dict]:
    return [dict(key=c.key, label=c.label, description=c.description, priority=i) for i, c in enumerate(CATEGORIES)]
