"""Answer correctness: is this answer RIGHT or WRONG, judged against the data, before the user sees it.

Independent of the model and of the verifier gates: the facts the answer states are re-derived from this turn's tool
results and from the question itself, then compared.
- window   : the period the question names ("last 4 months", "in 2025") is resolved independently and compared with
             the start/end the agent actually queried (catches a 3-month window answering a 4-month question)
- status   : "WELL-X is (not) producing" must match the well's effective status
- pairs    : "WELL-0121 ... 49 days" must match a value in WELL-0121's row of the result
- set      : for "which wells ..." questions, the wells the answer names must be exactly the wells in the result
- count    : "3 wells", "6 spills" must match the number of rows in the result
Verdict: correct (every applicable check passed), incorrect (with each mismatch), or unverified (nothing checkable).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from wellops.contracts import RunDeps

WELL = re.compile(r"\bWELL-\d{4}\b")
NUM = re.compile(r"(?<![\w.-])(\d[\d,]*(?:\.\d+)?)\s*(days?|bbl|°C|C\b|USD|\$|hours?|h\b|spills?|wells?|repairs?|inspections?)?", re.I)
COUNT = re.compile(r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten)\s+(wells?|spills?|repairs?|inspections?|events?)\b", re.I)
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
NEG = re.compile(r"\b(no longer|not (?:listed|included|added)|excluded|dropped|removed|except|unlike|rather than|instead of)\b", re.I)
LIST_Q = re.compile(r"\bwhich\b.*\b(wells?|spills?)\b|\blist\b|\bshow (?:me )?(?:all|every)\b", re.I)
NOT_PROD = re.compile(r"\b(not producing|isn't producing|is not producing|not running|shut in|down)\b", re.I)
PROD = re.compile(r"\b(is producing|producing oil|yes,? .*producing|running fine|back online)\b", re.I)


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


@dataclass
class Verdict:
    verdict: str  # correct | incorrect | unverified
    checks: list[Check] = field(default_factory=list)

    def mismatches(self) -> str:
        return "; ".join(c.detail for c in self.checks if not c.ok)

    def as_dict(self) -> dict:
        return {"verdict": self.verdict, "checks": [c.__dict__ for c in self.checks]}


def _lead(md: str) -> str:
    return md.split("\n\n**Data**")[0].split("\n\n**Sources**")[0]


def _num(s: str) -> float | None:
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def _rows(deps: RunDeps, results: list) -> list[tuple[Any, list[dict]]]:
    from wellops.tools.core import store
    out = []
    for r in results:
        st = store().get(deps.chat_id, r.handle)
        if st:
            out.append((r, st.rows))
    return out


def _windows(deps: RunDeps, question: str) -> list[tuple[date | None, date | None]]:
    from wellops.tools.entity import parse_dates
    try:
        return [(_as_date(w.get("start")), _as_date(w.get("end"))) for w in parse_dates(question, deps.today)]
    except Exception:
        return []


def _as_date(v: Any) -> date | None:
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def check(deps: RunDeps, question: str, markdown: str, results: list, tool_args: list[dict]) -> Verdict:
    lead = _lead(markdown)
    checks: list[Check] = []
    data = _rows(deps, results)

    # window: the question's period vs the period actually queried
    wins = [w for w in _windows(deps, question) if w[0] or w[1]]
    queried = [(a.get("start"), a.get("end")) for a in tool_args if a.get("start") or a.get("end")]
    if wins and queried:
        ws, we = wins[0]
        qs, qe = _as_date(queried[-1][0]), _as_date(queried[-1][1]) or deps.today
        if ws and qs:
            off = abs((qs - ws).days)
            checks.append(Check("window", off <= 3, f"question period starts {ws}, but the data was queried from {qs}"
                                if off > 3 else f"period {ws}..{we or deps.today} matches the query"))

    # status: "WELL-X is (not) producing" vs the computed state
    status_rows = {r["asset_id"]: r for res, rows in data for r in rows
                   if isinstance(r.get("asset_id"), str) and "producing" in r}
    for sent in re.split(r"(?<=[.!?])\s+", lead):
        ids = WELL.findall(sent)
        if len(ids) != 1 or ids[0] not in status_rows or NEG.search(sent):
            continue
        actual = bool(status_rows[ids[0]]["producing"])
        said = False if NOT_PROD.search(sent) else True if PROD.search(sent) else None
        if said is not None:
            checks.append(Check("status", said == actual, f"{ids[0]} is {'producing' if actual else 'not producing'} in the data, "
                                f"but the answer says {'producing' if said else 'not producing'}" if said != actual
                                else f"{ids[0]} {'producing' if actual else 'not producing'} matches the data"))

    # pairs: "WELL-0121 ... 49 days" must match that well's row
    by_well: dict[str, list[dict]] = {}
    for res, rows in data:
        for r in rows:
            if isinstance(r.get("asset_id"), str):
                by_well.setdefault(r["asset_id"], []).append(r)
    for m in WELL.finditer(lead):
        w = m.group(0)
        if w not in by_well:
            continue
        window = lead[m.end(): m.end() + 70]
        nxt = WELL.search(window)
        window = window[: nxt.start()] if nxt else window
        n = NUM.search(window)
        if not n or not n.group(2):  # only unit-bearing numbers ("49 days", "5.4 bbl") are claims about the row
            continue
        v = _num(n.group(1))
        cells = [x for r in by_well[w] for x in r.values() if isinstance(x, (int, float)) and not isinstance(x, bool)]
        if v is None or not cells:
            continue
        ok = any(abs(c - v) <= max(0.05, abs(v) * 0.005) for c in cells)
        checks.append(Check("pair", ok, f"{w}: the answer says {n.group(0).strip()}, the data has {sorted(set(round(c, 2) for c in cells))[:6]}"
                            if not ok else f"{w} {n.group(0).strip()} matches the data"))

    # set: for list questions the named wells must be exactly the result's wells
    lists = [(res, rows) for res, rows in data if res.shape in ("entity_list", "category_measure", "geo_points")
             and rows and all(isinstance(r.get("asset_id"), str) for r in rows) and len(rows) <= 15]
    if LIST_Q.search(question) and lists:
        res, rows = lists[-1]
        expect = {r["asset_id"] for r in rows}
        named = {w for sent in re.split(r"(?<=[.!?])\s+|\n", lead) if not NEG.search(sent) for w in WELL.findall(sent)}
        if named:
            missing, extra = sorted(expect - named), sorted(named - expect)
            ok = not missing and not extra
            checks.append(Check("set", ok, f"wells in the data but not named: {missing}; named but not in the data: {extra}"
                                if not ok else f"names exactly the {len(expect)} wells in the data"))

    # count: "3 wells" / "6 spills" vs the size of the result it describes
    if len([d for d in data if d[0].shape in ("entity_list", "geo_points")]) == 1:
        res, rows = next(d for d in data if d[0].shape in ("entity_list", "geo_points"))
        for m in COUNT.finditer(lead):
            w0 = m.group(1).lower()
            k = int(w0) if w0.isdigit() else WORDS.get(w0)
            noun = m.group(2).lower().rstrip("s")
            if k is None or (noun == "well" and "asset_id" not in res.columns):
                continue
            if noun in ("well", "spill") and res.row_count <= 50:
                checks.append(Check("count", k == res.row_count, f"the answer says {m.group(0)}, the result has {res.row_count}"
                                    if k != res.row_count else f"{m.group(0)} matches the result"))
                break

    if not checks:
        return Verdict("unverified", [])
    return Verdict("correct" if all(c.ok for c in checks) else "incorrect", checks)


def log(deps: RunDeps, question: str, verdict: Verdict, attempt: int, model: str) -> None:
    """Internal QA record of every answer's verdict (SQLite next to the answer cache)."""
    try:
        from wellops import cache
        c = cache.con()
        c.execute("""CREATE TABLE IF NOT EXISTS qa_answers (at REAL, principal TEXT, chat_id TEXT, question TEXT,
                     verdict TEXT, checks TEXT, attempt INTEGER, model TEXT)""")
        import time
        c.execute("INSERT INTO qa_answers VALUES (?,?,?,?,?,?,?,?)", [time.time(), deps.principal, deps.chat_id, question,
                  verdict.verdict, json.dumps([x.__dict__ for x in verdict.checks]), attempt, model])
        c.commit()
    except Exception:
        pass


def summary(limit: int = 200) -> dict:
    from wellops import cache
    try:
        rows = cache.con().execute("SELECT verdict, count(*) FROM (SELECT verdict FROM qa_answers ORDER BY at DESC LIMIT ?) "
                                   "GROUP BY verdict", [limit]).fetchall()
        recent = cache.con().execute("SELECT at, question, verdict, checks, attempt FROM qa_answers ORDER BY at DESC LIMIT 30").fetchall()
    except Exception:
        return {"counts": {}, "recent": []}
    return {"counts": dict(rows), "recent": [{"at": a, "question": q, "verdict": v, "checks": json.loads(c), "attempt": t}
                                             for a, q, v, c, t in recent]}
