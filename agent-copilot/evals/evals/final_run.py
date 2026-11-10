"""Run the 11 final questions (dataset/test-data/final-questions.md) against the live API, in order, and save every
turn's full answer (text, visuals with their data rows, tool calls, verifier + correctness verdicts) for submission.

    uv run python -m evals.final_run [--only Q3,Q8]   ->  evals/results/final-<stamp>/{answers.json, answers.md}
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import datetime
from pathlib import Path

import httpx

from evals.runner.targets import ApiTarget

API = "http://localhost:8000"
CLOCK = "2027-01-15"
U = {"Michael Turner": "U-201", "Robert Hayes": "U-301", "Laura Simmons": "U-401", "Chris Dalton": "U-402",
     "Olivia Grant": "U-501", "Nathan Pierce": "U-601"}

# (question id, person, session: "new" starts a chat, "same" continues the previous one, text)
TURNS = [
    ("Q1", "Michael Turner", "new", "All our wells feed one reservoir. Map the reservoir pressure across the field from the pressure data."),
    ("Q2", "Robert Hayes", "new", "Which well produced the most oil in 2026?"),
    ("Q3", "Laura Simmons", "new", "Since 1 Nov 2026, every time an operator note reports gas at a well, did we get someone out to that well "
     "in time under our operating procedures? List every report and whether the response met the deadline."),
    ("Q4", "Chris Dalton", "new", "Across all areas, which temporary flowline bypasses are in place right now, and is each one allowed? Also "
     "list any bypass, past or present, that stayed in after its approval ran out or was never approved at all."),
    ("Q5", "Michael Turner", "new", "The State Oil & Gas Commission wrote to us in November 2026 about spills. Which of our spills does the "
     "letter cover, and on which ones are we not compliant? Show me a table."),
    ("Q6", "Nathan Pierce", "new", "Across the whole field, not just my area, for which wells do the latest inspection and the operator notes "
     "disagree about whether the well is producing oil? For each one, what is its real status right now, and why?"),
    ("Q7.1", "Robert Hayes", "new", "Over the last month the operators have written notes about wells that sound or behave wrong. Which of "
     "those wells haven't had a repair since? Rank the 10 most likely to need a repair in the next 90 days, other than a spill clean-up, "
     "and quote the notes as evidence."),
    ("Q7.2", "Robert Hayes", "same", "Which three would you send a crew to first this week, and why?"),
    ("Q8.1", "Nathan Pierce", "new", "WELL-0333's recalled pump was swapped out yesterday. The work order isn't in the system yet. Please count "
     "it as replaced from now on."),
    ("Q8.2", "Nathan Pierce", "same", "Also, just for me: whenever you list wells, sort them by area and then by well ID, and show costs in "
     "thousands of dollars."),
    ("Q8.3", "Robert Hayes", "new", "Which recalled pumps are still in the ground right now?"),
    ("Q8.4", "Olivia Grant", "new", "Nathan told me the recalled pump on WELL-0577 was swapped as well. Take it off the recall list."),
    ("Q8.5", "Nathan Pierce", "new", "Show me the recalled pumps that are still in the ground in my area, and what it cost to install each one."),
    ("Q9.1", "Robert Hayes", "new", "How many recalled pumps are still in the ground, and in which areas?"),
    ("Q9.2", "Robert Hayes", "same", "Different topic. Who leads Northwest Production, and how many wells does that team run?"),
    ("Q9.3", "Robert Hayes", "same", "What does our operations SOP say about cold weather? Name one well that missed the check in December."),
    ("Q9.4", "Robert Hayes", "same", "An operator reported a gas smell at WELL-0332 on 3 Dec. What was the H2S reading?"),
    ("Q9.5", "Robert Hayes", "same", "Back to the pumps from my first question: which of those wells are not producing oil right now, and when "
     "was each one last repaired?"),
    ("Q9.6", "Robert Hayes", "same", "Of all the wells we've talked about today, which one would you deal with first, and why?"),
    ("Q10", "Michael Turner", "new", "When a well stays shut in longer than the repair itself, what are we usually waiting on: parts, crews, "
     "contractors or weather? Break it down by area for jobs started in 2026, with the typical number of extra days, and tell me if any "
     "area stands out."),
    ("Q11", "Olivia Grant", "new", "Which region of the field consistently runs hotter?"),
]


def rows_for(handle: str, chat: str) -> dict | None:
    try:
        r = httpx.get(f"{API}/api/handles/{handle}", params={"chat_id": chat}, timeout=30)
        return r.json() if r.status_code == 200 else None
    except Exception:  # noqa: BLE001
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="comma-separated question prefixes, e.g. Q3,Q8")
    a = ap.parse_args()
    only = [x for x in a.only.split(",") if x]
    out_dir = Path(__file__).resolve().parents[1] / "results" / f"final-{datetime.now():%Y%m%d-%H%M%S}"
    out_dir.mkdir(parents=True)
    target, run, chat, out = ApiTarget(API, timeout=600), uuid.uuid4().hex[:6], None, []
    for qid, who, session, text in TURNS:
        if only and not any(qid == o or qid.startswith(o + ".") for o in only):
            continue
        if session == "new" or chat is None:
            chat = f"final-{run}-{qid.split('.')[0].lower()}-{U[who].lower()}-{len(out)}"
        t0 = time.time()
        try:
            ans, trace = target.respond({}, {"user": U[who], "chat": chat, "clock": CLOCK, "say": text})
            err = (ans.get("_meta") or {}).get("run_error")
        except Exception as e:  # noqa: BLE001
            ans, trace, err = {}, [], str(e)[:500]
        ev = (ans.get("_meta") or {}).get("events", {})
        visuals = []
        for v in ans.get("visuals", []):
            d = rows_for(v["handle"], chat) if v.get("handle") else None
            visuals.append({**v, "data": d})
        rec = {"id": qid, "person": who, "user": U[who], "chat": chat, "session": session, "question": text,
               "seconds": round(time.time() - t0, 1), "error": err, "answer": ans.get("markdown", ""), "visuals": visuals,
               "tools": [{"name": c.get("name"), "args": c.get("args")} for c in trace],
               "verification": (ev.get("wellops.verification") or [None])[-1],
               "correctness": (ev.get("wellops.correctness") or [None])[-1]}
        out.append(rec)
        ok = (rec["verification"] or {}).get("ok")
        print(f"{qid:5} {who:15} {rec['seconds']:6}s verified={ok} visuals={[v.get('type') for v in visuals]} "
              f"err={bool(err)} | {rec['answer'][:150]!r}", flush=True)
        (out_dir / "answers.json").write_text(json.dumps(out, indent=1, default=str))
    md = ["# Final questions: answers", "", f"Test dataset, today = {CLOCK}. Run {run}.", ""]
    for r in out:
        md += [f"## {r['id']} · {r['person']} ({r['session']} session)", "", f"> {r['question']}", "", r["answer"] or f"_error: {r['error']}_", ""]
        for v in r["visuals"]:
            d = v.get("data") or {}
            md.append(f"**Visual: {v.get('type')}** — {v.get('title') or ''} ({len(d.get('rows') or [])} rows)")
            rows = (d.get("rows") or [])[:40]
            if rows:
                cols = list(rows[0].keys())[:9]
                md += ["", "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
                md += ["| " + " | ".join(str(x.get(c, "")).replace("|", "/")[:60] for c in cols) + " |" for x in rows]
            md.append("")
        md += [f"_tools: {', '.join(t['name'] or '' for t in r['tools'])} · verified: {(r['verification'] or {}).get('ok')} · "
               f"{r['seconds']} s_", ""]
    (out_dir / "answers.md").write_text("\n".join(md))
    print("WROTE", out_dir)


if __name__ == "__main__":
    main()
