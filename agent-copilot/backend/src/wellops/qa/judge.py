"""LLM-as-a-judge: three strict pass/fail evaluators on EVERY live answer.

The deterministic checker (wellops.qa.correctness) proves a few claim types right or wrong with regexes and row
lookups; the golden evals score a fixed key offline. This judge covers what neither reaches: any stated fact, any
hidden conflict, any half-answered question, on production traffic, with a written reason.

Evaluators (one rubric each, binary, "when in doubt FAIL"; reasoning is written BEFORE the verdict):
- correctness      every number / ID / date / status / name in the answer agrees with, and appears in, the tool results
- faithfulness     no fact beyond the tool results/evidence; no overstated certainty (a conflicting note is not hidden)
- task_completion  the answer answers what was asked: right entity, right period, requested visual, every list item

Judge model: settings.judge_model (default gemini-4-argon, stronger than the agent's gemini-3.8-flash), falling back
through settings.judge_fallbacks; temperature 0, JSON output. Judgments are cached by sha1(inputs) and persisted in
the QA SQLite store next to the answer cache (table `judgments`, one row per evaluator).

Online: on_answer() after the answer is composed. mode "async" (default) schedules a background task and returns []
at once; mode "blocking" awaits the judge and returns the CORRECTNESS / TASK_COMPLETION issues (once per turn) so the
turn loop re-asks. apply() turns those issues into a failing "judge" gate on the verifier result.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from wellops.settings import settings

EVALUATORS = ("correctness", "faithfulness", "task_completion")
BLOCKING = ("correctness", "task_completion")  # what a blocking judge re-asks on (faithfulness is reported only)
PROMPT_VERSION = "judge-v1"
BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai"
MAX_ROWS = 12  # rows per tool result shown to the judge
MAX_CELL = 240
TOOL_BUDGET = 16000  # chars of tool context in the prompt
TURN_WINDOW_S = 300.0  # attempts of the same question in the same chat within this window are one turn
NOISY = {"span", "same_text_count", "rank", "score", "lat", "lon", "sql", "threshold_source", "label"}

SYSTEM = """You are a strict QA judge for WellOps, an oil-field asset copilot. You grade ONE answer.
The ONLY source of truth is the TOOL RESULTS and EVIDENCE in the input (what the agent's tools returned this turn),
plus the question and today's date. Do not use outside knowledge. Apply three rubrics independently.
Each rubric is binary: PASS or FAIL. When in doubt, FAIL. For each rubric write "reasoning" FIRST: name the specific
values you checked and the tool row they were checked against. Then the "verdict". Then "issues": one concrete,
fixable sentence per problem, naming the value in the answer and what the data says. issues is [] if and only if PASS.

CORRECTNESS: FAIL if any number, record ID (WELL-, INS-, MNT-, CTX-, REG-, SR-, ...), date, producing status or
person/area name stated in the answer contradicts the tool results, or is absent from the tool results, the evidence
and the question. Allowed: rounding to the precision shown; simple arithmetic on tool values done correctly (sums,
differences, counts of listed rows); dates reformatted. The "**Data**" / "**Sources**" appendix at the end of the
answer is copied verbatim from the tool by code: check the prose above it, and use the appendix only as data.

FAITHFULNESS: FAIL if the answer asserts a fact, cause, reason, event or attribution that the tool results/evidence
do not support, or overstates certainty: presents a recorded status or a user claim as fact when the data disagree
or the claim is unconfirmed; omits a conflict the tool results flag (e.g. recorded_status_stale, conflicts, a note
that disagrees with sensor data, an unconfirmed claim); invents a reason where the data records none; states a guess
as a finding. Saying "no recorded reason" when none is recorded is faithful.

TASK_COMPLETION: FAIL if the answer does not answer what was asked: wrong entity (well, area, person, record); wrong
period (the window queried or reported differs from the period the question names, resolved against today);
a visual the question explicitly names (status card, table, line/chart, bar, map, heatmap, timeline) is missing
from VISUALS; a part of a multi-part question is left unanswered; for "which/list" questions, items present in the
tool results are left out of the answer or its figure. A clarifying question PASSES only if the request is genuinely
ambiguous and the question names the ambiguity. A refusal or "no data" PASSES only if the tool results are empty.

Return ONLY this JSON object (keys in this order inside each rubric):
{"correctness": {"reasoning": "...", "verdict": "PASS", "issues": []},
 "faithfulness": {"reasoning": "...", "verdict": "PASS", "issues": []},
 "task_completion": {"reasoning": "...", "verdict": "PASS", "issues": []}}"""


# ------------------------------------------------------------------------------------------------ data types
@dataclass
class Evaluation:
    reasoning: str
    verdict: str  # PASS | FAIL | ERROR (unparseable or judge unavailable: never counted as a pass or a fail)
    issues: list[str] = field(default_factory=list)


@dataclass
class Judgment:
    evaluations: dict[str, Evaluation]
    judge_model: str = ""
    latency_ms: int = 0
    cached: bool = False
    error: str | None = None

    @property
    def passed(self) -> bool:
        return all(e.verdict == "PASS" for e in self.evaluations.values())

    def failed(self, names=EVALUATORS) -> list[str]:
        return [n for n in names if self.evaluations.get(n) and self.evaluations[n].verdict == "FAIL"]

    def as_dict(self) -> dict:
        return {"evaluations": {k: asdict(v) for k, v in self.evaluations.items()}, "judge_model": self.judge_model,
                "latency_ms": self.latency_ms, "cached": self.cached, "error": self.error, "passed": self.passed}

    @classmethod
    def from_dict(cls, d: dict) -> "Judgment":
        return cls({k: Evaluation(**v) for k, v in d["evaluations"].items()}, d.get("judge_model", ""),
                   d.get("latency_ms", 0), d.get("cached", False), d.get("error"))


@dataclass
class JudgeInput:
    question: str
    answer: str
    tools: list[dict]  # compact: [{tool, args, shape, row_count, columns, rows, notes, conflicts, citations} | {tool, text}]
    visuals: list[dict]
    today: str = ""
    principal: str = ""

    def prompt(self) -> str:
        return "\n\n".join([
            f"TODAY: {self.today}    USER: {self.principal}",
            f"QUESTION:\n{self.question}",
            f"VISUALS (rendered next to the answer):\n{json.dumps(self.visuals, default=str)}",
            f"ANSWER (markdown):\n{self.answer}",
            "TOOL RESULTS AND EVIDENCE (rows trimmed; row_count is the full size):\n" + _fit(self.tools)])

    def key(self, model: str) -> str:
        raw = json.dumps([PROMPT_VERSION, model, SYSTEM, self.prompt()], default=str)
        return hashlib.sha1(raw.encode()).hexdigest()


def _fit(tools: list[dict]) -> str:
    out, used = [], 0
    for t in tools:
        s = json.dumps(t, default=str)
        if used + len(s) > TOOL_BUDGET:
            s = s[: max(400, TOOL_BUDGET - used)] + " …(truncated)"
        out.append(s)
        used += len(s)
        if used >= TOOL_BUDGET:
            out.append(f"…{len(tools) - len(out)} more tool results omitted")
            break
    return "\n".join(out) or "(no tool was called)"


def _cell(v: Any) -> Any:
    if isinstance(v, str) and len(v) > MAX_CELL:
        return v[:MAX_CELL] + "…"
    return v


def _rows(rows: list[dict], n: int = MAX_ROWS) -> list[dict]:
    return [{k: _cell(v) for k, v in r.items() if k not in NOISY and v not in (None, "")} for r in rows[:n]]


def compact_tool_text(name: str, args: Any, text: str) -> dict:
    """One raw tool output (the JSON string the agent saw, possibly truncated) as compact judge input."""
    try:
        d = json.loads(text)
    except (TypeError, ValueError):
        d = None
    if isinstance(d, dict) and ("preview" in d or "rows" in d):
        return {"tool": name, "args": args, "shape": d.get("shape"), "row_count": d.get("row_count"),
                "columns": [c for c in d.get("columns", []) if c not in NOISY],
                "rows": _rows(d.get("preview") or d.get("rows") or []), "notes": d.get("notes") or [],
                "conflicts": d.get("conflicts") or [], "citations": [c.get("id") for c in d.get("citations") or [] if isinstance(c, dict)][:20]}
    return {"tool": name, "args": args, "text": str(text)[:1500]}


def compact_live(deps, results: list, tool_texts: list[str], calls: list[dict] | None = None) -> list[dict]:
    """This turn's ToolOut results (full rows from the handle store, trimmed) plus the raw outputs of tools that
    return no handle (resolve_entity, search_knowledge, corrections ...)."""
    from wellops.tools.core import store
    out, handles = [], set()
    for r in results:
        st = store().get(deps.chat_id, r.handle) if getattr(r, "handle", None) else None
        rows = st.rows if st else list(r.preview)
        handles.add(r.handle)
        out.append({"handle": r.handle, "shape": r.shape, "row_count": r.row_count,
                    "columns": [c for c in r.columns if c not in NOISY], "rows": _rows(rows),
                    "notes": list(r.notes), "conflicts": [c.model_dump(mode="json") for c in getattr(r, "conflicts", [])],
                    "applied_overlays": list(r.applied_overlays),
                    "citations": [c.id for c in r.citations][:20]})
    names = [c.get("name") for c in (calls or [])]
    for i, t in enumerate(tool_texts):
        try:
            h = json.loads(t).get("handle")
        except (TypeError, ValueError, AttributeError):
            h = None
        if h and h in handles:
            continue
        name = names[-len(tool_texts):][i] if len(names) >= len(tool_texts) else "tool"
        out.append(compact_tool_text(name, None, t))
    return out


def compact_visuals(visuals: list) -> list[dict]:
    out = []
    for v in visuals:
        d = v.model_dump(mode="json") if hasattr(v, "model_dump") else dict(v)
        out.append({k: d.get(k) for k in ("type", "handle", "title") if d.get(k)})
    return out


# ------------------------------------------------------------------------------------------------ parsing
def _obj(text: str) -> dict | None:
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", (text or "").strip())
    try:
        d = json.loads(s)
        return d if isinstance(d, dict) else None
    except ValueError:
        pass
    a, b = s.find("{"), s.rfind("}")
    if a >= 0 and b > a:
        try:
            d = json.loads(s[a: b + 1])
            return d if isinstance(d, dict) else None
        except ValueError:
            return None
    return None


def parse(text: str) -> dict[str, Evaluation]:
    """Strict: a rubric whose verdict is not exactly PASS or FAIL is ERROR. A FAIL without issues gets its reasoning
    as the issue (so a re-ask always has something to fix)."""
    d = _obj(text) or {}
    norm = {re.sub(r"[^a-z]", "", k.lower()): v for k, v in d.items()}
    out = {}
    for name in EVALUATORS:
        e = norm.get(name.replace("_", ""))
        if not isinstance(e, dict):
            out[name] = Evaluation("judge output missing this rubric", "ERROR", [])
            continue
        verdict = str(e.get("verdict", "")).strip().upper()
        verdict = verdict if verdict in ("PASS", "FAIL") else "ERROR"
        issues = e.get("issues") or []
        issues = [issues] if isinstance(issues, str) else [str(i) for i in issues if str(i).strip()]
        reasoning = str(e.get("reasoning", "")).strip()
        if verdict == "FAIL" and not issues:
            issues = [reasoning[:300] or f"{name} failed"]
        out[name] = Evaluation(reasoning, verdict, issues)
    return out


# ------------------------------------------------------------------------------------------------ client
class JudgeClient(Protocol):
    async def complete(self, model: str, system: str, user: str) -> str: ...


class ModelUnavailable(RuntimeError):
    pass


class GeminiJudgeClient:
    """Google's OpenAI-compatible endpoint, JSON mode, temperature 0."""

    def __init__(self, api_key: str | None = None, base_url: str = BASE_URL, timeout: float | None = None):
        self.api_key = api_key if api_key is not None else settings.gemini_api_key
        self.base_url, self.timeout = base_url, timeout or settings.judge_timeout_s

    async def complete(self, model: str, system: str, user: str) -> str:
        import httpx
        if not self.api_key:
            raise ModelUnavailable("no GEMINI_API_KEY")
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {self.api_key}"},
                             json={"model": model, "temperature": 0, "response_format": {"type": "json_object"},
                                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
        if r.status_code in (400, 404) and "model" in r.text.lower():
            raise ModelUnavailable(f"{model}: HTTP {r.status_code}")
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"] or ""


_CLIENT: JudgeClient | None = None


def set_client(c: JudgeClient | None) -> None:
    """Tests (and offline tools) inject a fake client here."""
    global _CLIENT
    _CLIENT = c


def client() -> JudgeClient:
    return _CLIENT or GeminiJudgeClient()


def models() -> list[str]:
    chain = [settings.judge_model, *settings.judge_fallbacks.split(",")]
    return list(dict.fromkeys(m.strip() for m in chain if m.strip()))


# ------------------------------------------------------------------------------------------------ store
_LOCK = threading.Lock()
SCHEMA = [
    """CREATE TABLE IF NOT EXISTS judgments (at REAL, chat TEXT, principal TEXT, question TEXT, evaluator TEXT,
       verdict TEXT, reasoning TEXT, issues TEXT, judge_model TEXT, agent_model TEXT, latency_ms INTEGER,
       turn_id TEXT, attempt INTEGER, clock TEXT, mode TEXT, cached INTEGER, answer TEXT)""",
    "CREATE INDEX IF NOT EXISTS judgments_at ON judgments(at)",
    "CREATE INDEX IF NOT EXISTS judgments_turn ON judgments(turn_id)",
    "CREATE TABLE IF NOT EXISTS judge_cache (k TEXT PRIMARY KEY, payload TEXT, at REAL)",
]


def con():
    from wellops import cache
    c = cache.con()
    for s in SCHEMA:
        c.execute(s)
    return c


def cache_get(k: str) -> Judgment | None:
    try:
        with _LOCK:
            r = con().execute("SELECT payload FROM judge_cache WHERE k=?", [k]).fetchone()
        return Judgment.from_dict(json.loads(r[0])) if r else None
    except Exception:  # noqa: BLE001
        return None


def cache_put(k: str, j: Judgment) -> None:
    try:
        with _LOCK:
            c = con()
            c.execute("INSERT OR REPLACE INTO judge_cache VALUES (?,?,?)", [k, json.dumps(j.as_dict()), time.time()])
            c.commit()
    except Exception:  # noqa: BLE001
        pass


def persist(j: Judgment, *, chat: str, principal: str, question: str, agent_model: str, turn_id: str = "",
            attempt: int = 0, clock: str = "", mode: str = "", answer: str = "") -> None:
    now = time.time()
    with _LOCK:
        c = con()
        c.executemany("INSERT INTO judgments VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            [now, chat, principal, question, name, e.verdict, e.reasoning, json.dumps(e.issues), j.judge_model,
             agent_model, j.latency_ms, turn_id, attempt, clock, mode, int(j.cached), answer[:6000]]
            for name, e in j.evaluations.items()])
        c.commit()


def judgments(limit: int = 100, verdict: str | None = None, evaluator: str | None = None, chat: str | None = None) -> list[dict]:
    q, a = "SELECT * FROM judgments WHERE 1=1", []
    for col, v in (("verdict", verdict), ("evaluator", evaluator), ("chat", chat)):
        if v:
            q += f" AND {col}=?"
            a.append(v.upper() if col == "verdict" else v)
    q += " ORDER BY at DESC, rowid DESC LIMIT ?"
    with _LOCK:
        cur = con().execute(q, [*a, limit])
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["issues"] = json.loads(r["issues"] or "[]")
    return rows


def summary(last: int = 200) -> dict:
    """Pass rates per evaluator over the last `last` judged answers (turn attempts), plus all-three pass rate."""
    with _LOCK:
        c = con()
        keys = c.execute("SELECT turn_id, attempt, max(at) t FROM judgments GROUP BY turn_id, attempt ORDER BY t DESC LIMIT ?",
                         [last]).fetchall()
        if not keys:
            return {"n": 0, "evaluators": {}, "all_pass_rate": None, "recent_failures": []}
        rows = c.execute(f"SELECT turn_id, attempt, evaluator, verdict, latency_ms FROM judgments WHERE (turn_id, attempt) IN "
                         f"(VALUES {','.join('(?,?)' for _ in keys)})", [x for k in keys for x in k[:2]]).fetchall()
    per: dict[str, dict] = {}
    turns: dict[tuple, list[str]] = {}
    lat = {}
    for tid, att, ev, verdict, ms in rows:
        p = per.setdefault(ev, {"pass": 0, "fail": 0, "error": 0})
        p[{"PASS": "pass", "FAIL": "fail"}.get(verdict, "error")] += 1
        turns.setdefault((tid, att), []).append(verdict)
        lat[(tid, att)] = ms
    for p in per.values():
        n = p["pass"] + p["fail"]
        p["pass_rate"] = round(p["pass"] / n, 3) if n else None
    graded = [v for v in turns.values() if "ERROR" not in v]
    ms = sorted(x for x in lat.values() if x)
    return {"n": len(keys), "evaluators": per,
            "all_pass_rate": round(sum(all(x == "PASS" for x in v) for v in graded) / len(graded), 3) if graded else None,
            "judge_latency_ms_p50": ms[len(ms) // 2] if ms else None,
            "recent_failures": [{k: r[k] for k in ("at", "chat", "principal", "question", "evaluator", "issues", "judge_model")}
                                for r in judgments(limit=10, verdict="FAIL")]}


# ------------------------------------------------------------------------------------------------ judging
async def judge(inp: JudgeInput, c: JudgeClient | None = None, use_cache: bool = True) -> Judgment:
    """Run the three rubrics (one call, independent sections) on the first judge model that answers."""
    c = c or client()
    chain = models()
    k = inp.key(chain[0])
    if use_cache and (hit := cache_get(k)):
        hit.cached = True
        return hit
    t0, err = time.perf_counter(), None
    for m in chain:
        try:
            text = await c.complete(m, SYSTEM, inp.prompt())
        except ModelUnavailable as e:
            err = str(e)
            continue
        except Exception as e:  # noqa: BLE001  timeout / 429 / 5xx: try the next model
            err = f"{m}: {type(e).__name__}: {e}"[:300]
            continue
        j = Judgment(parse(text), m, int((time.perf_counter() - t0) * 1000))
        if all(e.verdict == "ERROR" for e in j.evaluations.values()):
            err = f"{m}: unparseable judge output"
            continue
        if use_cache:
            cache_put(k, j)
        return j
    return Judgment({n: Evaluation(err or "judge unavailable", "ERROR", []) for n in EVALUATORS}, "",
                    int((time.perf_counter() - t0) * 1000), error=err or "judge unavailable")


def build_input(deps, question: str, spec, results: list, tool_texts: list[str]) -> JudgeInput:
    from wellops import mcp_server
    calls = [c for c in mcp_server.CALL_LOG.get(deps.chat_id, [])]
    return JudgeInput(question=question, answer=spec.markdown, tools=compact_live(deps, results, tool_texts, calls),
                      visuals=compact_visuals(spec.visuals), today=str(deps.today), principal=deps.principal)


def agent_model() -> str:
    ts = sys.modules.get("wellops.api.turn_stream")
    return getattr(ts, "MODEL", None) or settings.llm_model or "gemini-3.8-flash"


_TURNS: dict[tuple[str, str], dict] = {}
_TASKS: set[asyncio.Task] = set()


def _turn(chat: str, question: str) -> dict:
    now, k = time.time(), (chat, question)
    t = _TURNS.get(k)
    if not t or now - t["at"] > TURN_WINDOW_S:
        t = _TURNS[k] = {"id": uuid.uuid4().hex, "at": now, "attempt": -1, "reasked": False}
        if len(_TURNS) > 2000:
            for old in sorted(_TURNS, key=lambda x: _TURNS[x]["at"])[:1000]:
                _TURNS.pop(old, None)
    t["attempt"] += 1
    t["at"] = now
    return t


async def judge_turn(deps, question: str, inp: JudgeInput, *, mode: str, turn: dict, c: JudgeClient | None = None) -> Judgment:
    j = await judge(inp, c)
    try:
        persist(j, chat=deps.chat_id, principal=deps.principal, question=question, agent_model=agent_model(),
                turn_id=turn["id"], attempt=turn["attempt"], clock=str(deps.today), mode=mode, answer=inp.answer)
    except Exception:  # noqa: BLE001  QA must never break a turn
        pass
    try:
        await export_langfuse(deps, question, inp, j, turn)
    except Exception:  # noqa: BLE001
        pass
    return j


def enabled() -> bool:
    return settings.judge_enabled and (_CLIENT is not None or bool(settings.gemini_api_key))


async def on_answer(deps, question: str, spec, results: list, tool_texts: list[str], mode: str | None = None) -> list[str]:
    """Judge one composed answer. async: schedule and return [] now. blocking: await; return the CORRECTNESS /
    TASK_COMPLETION issues (prefixed with the evaluator) the first time they fail in this turn, else []."""
    if not enabled() or spec is None:
        return []
    mode = (mode or settings.judge_mode or "async").lower()
    turn = _turn(deps.chat_id, question)
    try:
        inp = build_input(deps, question, spec, results, tool_texts)  # snapshot now: a re-ask mutates the stores
    except Exception:  # noqa: BLE001
        return []
    if mode != "blocking":
        task = asyncio.get_running_loop().create_task(judge_turn(deps, question, inp, mode=mode, turn=turn))
        _TASKS.add(task)
        task.add_done_callback(_TASKS.discard)
        return []
    j = await judge_turn(deps, question, inp, mode=mode, turn=turn)
    if turn["reasked"]:
        return []
    issues = blocking_issues(j)
    if issues:
        turn["reasked"] = True
    return issues


def blocking_issues(j: Judgment) -> list[str]:
    return [f"[judge:{n}] {i}" for n in j.failed(BLOCKING) for i in j.evaluations[n].issues][:8]


def apply(v, issues: list[str]) -> None:
    """Blocking FAIL -> a failing "judge" gate on the verifier result, so the existing loop re-asks with the issues."""
    if not issues:
        return
    from wellops.verify.gates import GateResult
    v.gates.append(GateResult(name="judge", ok=False, details=list(issues)))
    v.ok = False


async def drain(timeout: float = 30.0) -> None:
    """Wait for background judgments (tests, shutdown)."""
    if _TASKS:
        await asyncio.wait(list(_TASKS), timeout=timeout)


# ------------------------------------------------------------------------------------------------ Langfuse
def langfuse_enabled() -> bool:
    return bool(settings.langfuse_public_key and settings.langfuse_secret_key and settings.langfuse_host)


def langfuse_batch(deps, question: str, inp: JudgeInput, j: Judgment, turn: dict) -> list[dict]:
    """Ingestion events: one trace per turn (id = turn id, upserted per attempt), the judge as a generation, and one
    score per evaluator (id fixed per turn, so the last attempt's verdict wins)."""
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    tid = turn["id"]

    def ev(kind: str, body: dict) -> dict:
        return {"id": uuid.uuid4().hex, "timestamp": now, "type": kind, "body": body}

    out = [ev("trace-create", {"id": tid, "timestamp": now, "name": "wellops.turn", "userId": deps.principal,
                               "sessionId": deps.chat_id, "input": question, "output": inp.answer[:8000],
                               "tags": ["wellops", "judge"], "metadata": {"agent_model": agent_model(), "attempt": turn["attempt"],
                                                                         "clock": str(deps.today), "visuals": inp.visuals}}),
           ev("generation-create", {"id": f"{tid}-judge-{turn['attempt']}", "traceId": tid, "name": "judge",
                                    "model": j.judge_model, "input": {"prompt_version": PROMPT_VERSION},
                                    "output": j.as_dict(), "metadata": {"cached": j.cached, "latency_ms": j.latency_ms}})]
    for n, e in j.evaluations.items():
        if e.verdict in ("PASS", "FAIL"):
            out.append(ev("score-create", {"id": f"{tid}-{n}", "traceId": tid, "name": f"judge.{n}",
                                           "value": 1.0 if e.verdict == "PASS" else 0.0, "dataType": "NUMERIC",
                                           "comment": (e.reasoning + (" | " + "; ".join(e.issues) if e.issues else ""))[:2000]}))
    return out


async def export_langfuse(deps, question: str, inp: JudgeInput, j: Judgment, turn: dict) -> int | None:
    if not langfuse_enabled():
        return None
    import httpx
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(settings.langfuse_host.rstrip("/") + "/api/public/ingestion",
                         auth=(settings.langfuse_public_key, settings.langfuse_secret_key),
                         json={"batch": langfuse_batch(deps, question, inp, j, turn)})
    return r.status_code
