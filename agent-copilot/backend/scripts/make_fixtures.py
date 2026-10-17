"""Build the frontend mock fixtures from real tool output (no LLM).

Each scenario replays what the agent would do for one example question in dataset/03-evaluation-queries.md:
resolve entities, call the data tools in backend/src/wellops/tools/, then compose an AnswerSpec whose prose,
claims and visuals are derived from the returned rows. Every AnswerSpec is checked by the real L4 verifier and
the gate results are written with it. No answer value is typed in by hand; the question text is the only input.

Run against a private copy of the database (the API server holds the lock on the default one):
    cd backend && DB_PATH=/tmp/wellops_7b.duckdb uv run python -m wellops.substrate.ingest
    DB_PATH=/tmp/wellops_7b.duckdb uv run python scripts/make_fixtures.py [out.json]
The correction in Q12 is written to that copy, never to the shared database.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from wellops import memory, tools
from wellops.contracts import AnswerSpec, AssertionIn, Citation, Claim, Conflict, RunDeps, Visual
from wellops.settings import ROOT, settings
from wellops.tools.core import ID_RE, ToolOut, cite_kind, get_db, store
from wellops.verify import verify

OUT = ROOT / "frontend" / "src" / "mock" / "fixtures.json"
CHAT = "fixtures"  # one chat for every scenario, so handles r1..rN are unique across the fixture set
RECORDS = {"inspection": ("inspections", "inspection_id"), "maintenance": ("maintenance", "maintenance_id"),
           "report": ("reports", "maintenance_id"), "reg_event": ("reg_events", "event_id"), "note": ("notes", "note_id"),
           "well": ("wells", "asset_id"), "doc": ("docs", "doc_id")}  # mirrors api.app.RECORDS


def deps(principal: str = "U-201") -> RunDeps:
    return RunDeps(principal=principal, chat_id=CHAT, today=settings.clock_today, active_dataset=settings.active_dataset)


def fmt_day(v: str) -> str:
    return str(v)[:10]  # ISO dates: the grounding gate checks every number in the prose, so "16 Jun" would not pass


def fmt_num(x: float | int | None) -> str:
    return "–" if x is None else (f"{x:g}" if isinstance(x, float) else str(x))


class Run:
    """One scenario: records tool calls as the agent would make them, then builds and verifies the answer."""

    def __init__(self, sid: str, label: str, question: str, keywords: list[str], skills: list[str], principal="U-201"):
        self.sid, self.label, self.question, self.keywords, self.skills = sid, label, question, keywords, skills
        self.d = deps(principal)
        self.calls: list[dict] = []
        self.results: list[ToolOut] = []
        self.raw: list[str] = []

    def call(self, name: str, fn, **args) -> Any:
        t0 = time.perf_counter()
        out = fn(self.d, **args)
        ms = max(60, round((time.perf_counter() - t0) * 1000))
        payload = out.model_dump(mode="json") if isinstance(out, BaseModel) else out
        self.raw.append(json.dumps(payload, default=str))
        if isinstance(out, ToolOut):
            self.results.append(out)
        jargs = {k: (v.model_dump(mode="json") if isinstance(v, BaseModel) else v) for k, v in args.items()}
        self.calls.append({"name": name, "args": jargs, "ms": ms, "handle": getattr(out, "handle", None), "result": payload})
        return out

    def rows(self, out: ToolOut) -> list[dict]:
        return store().get(CHAT, out.handle).rows

    def finish(self, spec: AnswerSpec) -> dict:
        found = {c.subject: c for r in self.results for c in r.conflicts}
        for c in spec.conflicts:
            found.pop(c.subject, None)
        spec.conflicts += list(found.values())  # surface every conflict a tool reported (conflicts gate)
        ov = list(dict.fromkeys(o for r in self.results for o in r.applied_overlays))
        spec.applied_overlays = list(dict.fromkeys(spec.applied_overlays + ov))
        v = verify(self.d, spec, self.results, "\n".join(self.raw), self.question)
        if not v.ok:
            print(f"  ! {self.sid} verification failed: {v.diff()}", file=sys.stderr)
        handles = {}
        for r in self.results:
            s = store().get(CHAT, r.handle)
            handles[r.handle] = {"handle": r.handle, "shape": r.shape, "columns": r.columns, "rows": s.rows, "sql": r.sql,
                                 "citations": [c.model_dump(mode="json") for c in r.citations],
                                 "dq_flags": [f.model_dump() for f in r.dq_flags],
                                 "applied_overlays": r.applied_overlays, "notes": r.notes}
        return {"id": self.sid, "label": self.label, "question": self.question, "keywords": self.keywords,
                "skills": self.skills, "tools": self.calls, "handles": handles,
                "answer": spec.model_dump(mode="json", exclude_none=True),
                "verification": {"ok": v.ok, "retries": 0,
                                 "gates": [{"name": g.name, "ok": g.ok, "detail": "; ".join(g.details) or None} for g in v.gates]},
                "usage": {"model": "none: tool fixtures (no LLM)"}}


def assumptions(out: ToolOut, extra: list[str] = ()) -> list[str]:
    return [n for n in out.notes if not n.startswith(("queries:", "dataset="))][:4] + list(extra) + \
        [f"dataset {settings.active_dataset}"]


def window_of(res: ToolOut, phrase: str) -> tuple[str, str]:
    r = next(x for x in store().get(CHAT, res.handle).rows if x["kind"] == "date_range" and phrase in (x["name"] or ""))
    return r["start"], r["end"]


def area_of(res: ToolOut) -> str:
    return next(x["name"] for x in store().get(CHAT, res.handle).rows if x["kind"] == "area")


# --- scenarios ---------------------------------------------------------------------------------------------------

def q1() -> dict:
    r = Run("q1", "One well right now", "Is WELL-0001 producing oil right now? When was it last inspected, and by whom? "
            "Show me its status card.", ["well-0001", "status card", "right now"], ["well-status", "visual-choice"])
    r.call("resolve_entity", tools.resolve_entity, text=r.question)
    st = r.call("well_status", tools.well_status, scope=tools.Scope(kind="wells", ids=["WELL-0001"]))
    w = r.rows(st)[0]
    reason = next((e for e in w["explanation"] if e["role"] == "reason"), None)
    md = (f"**{'Yes' if w['producing'] else 'No'}, {w['asset_id']} is {'' if w['producing'] else 'not '}producing oil.** "
          f"Its latest inspection, {w['inspection_id']} on {fmt_day(w['inspected_at'])} by {w['method']}, was done by "
          f"{w['inspector']}. The wellhead was only **{w['delta_c']} °C** warmer than the air, below the "
          f"{w['well_type']} limit of {fmt_num(w['threshold_c'])} °C.")
    if reason:
        md += f"\n\nReason: note {reason['ref']} by {reason['author']}: *“{reason['text']}”*"
    if w["recorded_status_stale"]:
        md += f"\n\nThe recorded status still says `{w['recorded_status']}`, which is out of date."
    return r.finish(AnswerSpec(
        markdown=md,
        claims=[Claim(text=f"{w['delta_c']} °C", value=w["delta_c"], handle=st.handle, column="delta_c"),
                Claim(text=w["inspection_id"], value=w["inspection_id"], handle=st.handle, column="inspection_id"),
                Claim(text=w["inspector"], value=w["inspector"], handle=st.handle, column="inspector")],
        visuals=[Visual(type="status_card", handle=st.handle, title=w["asset_id"], requested_by_user=True,
                        encodings={"title": "asset_id", "status": "producing", "value": "delta_c",
                                   "threshold": "threshold_c", "note": "reason_summary", "unit": "°C"})],
        citations=[Citation(kind="inspection", id=w["inspection_id"])] +
                  ([Citation(kind="note", id=reason["ref"])] if reason else []),
        assumptions=assumptions(st, ["“right now” = each well's latest inspection"]),
        follow_ups=["Show a line chart of WELL-0001's wellhead vs air temperature for the last 13 weeks"]))


def q2(sid="q2", label="Not producing in an area", question=None, after_correction=False) -> dict:
    question = question or "Which wells in Northeast Block 8 are not producing oil right now, and why? Show them in a table."
    r = Run(sid, label, question, ["northeast block 8", "not producing", "table"],
            ["well-status", "visual-choice"] + (["conflict-handling"] if after_correction else []))
    ent = r.call("resolve_entity", tools.resolve_entity, text=question)
    area = area_of(ent)
    st = r.call("well_status", tools.well_status, scope=tools.Scope(kind="area", ids=[area]), only="not_producing")
    rows = r.rows(st)
    unexplained = [w for w in rows if w["reason_summary"].startswith("No recorded reason")]
    lines = []
    for w in rows:
        why = w["reason_summary"]
        if why.startswith("No recorded reason"):
            why = "**No recorded reason**" + (f"; the inspector noted *“{w['inspection_note']}”*" if w["inspection_note"] else "")
        stale = " (recorded status out of date)" if w["recorded_status_stale"] else ""
        lines.append(f"- **{w['asset_id']}**: {w['delta_c']} °C above air{stale}. {why}")
    md = (f"**{len(rows)} wells** in {area} are not producing oil right now, judged by each well's latest inspection.\n\n"
          + "\n".join(lines))
    if unexplained:
        md += (f"\n\n{len(unexplained)} {'has' if len(unexplained) == 1 else 'have'} no recorded reason. Nothing in the data explains the stop, so I suggest a "
               "site check rather than guessing a cause.")
    claims = [Claim(text=f"{len(rows)} wells", value=len(rows), handle=st.handle)] + \
        [Claim(text=f"{w['delta_c']} °C", value=w["delta_c"], handle=st.handle, column="delta_c",
               row_key={"asset_id": w["asset_id"]}) for w in rows]
    return r, st, AnswerSpec(
        markdown=md, claims=claims,
        visuals=[Visual(type="table", handle=st.handle, title=f"{area}: not producing now", requested_by_user=True,
                        encodings={"columns": ["asset_id", "delta_c", "recorded_status", "reason_summary"]})],
        citations=[Citation(kind="inspection", id=w["inspection_id"]) for w in rows],
        assumptions=assumptions(st, ["“right now” = each well's latest inspection"]),
        follow_ups=[f"Schedule a site check for {', '.join(w['asset_id'] for w in unexplained)}"] if unexplained else [])


def q3() -> dict:
    q = ("Show a line chart of WELL-0001's wellhead temperature against air temperature for the last 13 weeks, "
         "and mark when it was not producing oil.")
    r = Run("q3", "A well over time", q, ["wellhead", "air temperature", "13 weeks", "line"],
            ["well-status", "visual-choice"])
    ent = r.call("resolve_entity", tools.resolve_entity, text=q)
    s, e = window_of(ent, "13 weeks")
    ser = r.call("inspection_series", tools.inspection_series, well="WELL-0001", start=s, end=e)
    rows = r.rows(ser)
    down = [x for x in rows if not x["producing"]]
    bands: list[dict] = []  # one shaded band per run of consecutive non-producing readings
    for i, x in enumerate(rows):
        if x["producing"]:
            continue
        if i and not rows[i - 1]["producing"] and bands:
            bands[-1]["to"] = x["date"]
        else:
            bands.append({"type": "band", "from": x["date"], "to": x["date"], "label": "Not producing"})
    md = (f"WELL-0001 has **{len(rows)} readings** from {fmt_day(rows[0]['date'])} to {fmt_day(rows[-1]['date'])}. "
          f"It counts as producing when the wellhead is at least {fmt_num(rows[0]['threshold_c'])} °C warmer than the air. "
          + (f"It was **not producing** on {' and '.join(fmt_day(x['date']) for x in down)} "
             f"(only {' and '.join(str(x['delta_c']) for x in down)} °C above air); those weeks are shaded."
             if down else "It was producing at every reading."))
    claims = [Claim(text=f"{len(rows)} readings", value=len(rows), handle=ser.handle)] + \
        [Claim(text=f"{x['delta_c']} °C", value=x["delta_c"], handle=ser.handle, column="delta_c",
               row_key={"inspection_id": x["inspection_id"]}) for x in down]
    return r.finish(AnswerSpec(
        markdown=md, claims=claims,
        visuals=[Visual(type="line", handle=ser.handle, title="WELL-0001 wellhead vs air (°C)", requested_by_user=True,
                        encodings={"x": "date", "y": ["wellhead_c", "ambient_c"]}, annotations=bands)],
        citations=[Citation(kind="inspection", id=x["inspection_id"]) for x in down],
        assumptions=assumptions(ser, ["producing ⇔ wellhead − air ≥ the well type's threshold"])))


def q5() -> dict:
    q = "How many days was WELL-0001 down in 2025?"
    r = Run("q5", "A number with its evidence", q, ["days", "down", "2025"], ["downtime-analysis", "visual-choice"])
    ent = r.call("resolve_entity", tools.resolve_entity, text=q)
    s, e = window_of(ent, "2025")
    dt = r.call("downtime", tools.downtime, scope=tools.Scope(kind="wells", ids=["WELL-0001"]), start=s, end=e)
    w = r.rows(dt)[0]
    md = (f"WELL-0001 was down **{w['downtime_days']} days** in 2025: {w['non_producing_inspections']} weekly inspections "
          f"found it not producing ({w['non_producing_dates']}).")
    return r.finish(AnswerSpec(
        markdown=md,
        claims=[Claim(text=f"{w['downtime_days']} days", value=w["downtime_days"], handle=dt.handle, column="downtime_days")],
        visuals=[Visual(type="kpi", handle=dt.handle, title="WELL-0001 downtime, 2025",
                        encodings={"values": ["downtime_days", "non_producing_inspections", "inspections"],
                                   "labels": {"downtime_days": "Days down", "non_producing_inspections": "Not-producing inspections",
                                              "inspections": "Inspections in 2025"},
                                   "units": {"downtime_days": "days"}})],
        assumptions=assumptions(dt)))


def q6() -> dict:
    q = "Which 3 wells in Northeast Block 7 had the most downtime in the last 4 months? Show a bar chart."
    r = Run("q6", "Compare wells", q, ["downtime", "northeast block 7", "bar"], ["downtime-analysis", "visual-choice"])
    ent = r.call("resolve_entity", tools.resolve_entity, text=q)
    s, e = window_of(ent, "4 months")
    area = area_of(ent)
    dt = r.call("downtime", tools.downtime, scope=tools.Scope(kind="area", ids=[area]), start=s, end=e,
                group_by="well", top_k=3)
    rows = r.rows(dt)
    md = (f"From {fmt_day(s)} to {fmt_day(e)}, the three wells in {area} with the most downtime were "
          + ", ".join(f"**{w['asset_id']}: {w['downtime_days']} days** ({w['well_type']})" for w in rows) + ". "
          "Downtime is counted as 7 days for each weekly inspection where the well was not producing.")
    if len(rows) > 2 and rows[1]["downtime_days"] == rows[2]["downtime_days"]:
        md += f" {rows[1]['asset_id']} and {rows[2]['asset_id']} tie."
    return r.finish(AnswerSpec(
        markdown=md,
        claims=[Claim(text=f"{w['downtime_days']} days", value=w["downtime_days"], handle=dt.handle,
                      column="downtime_days", row_key={"asset_id": w["asset_id"]}) for w in rows],
        visuals=[Visual(type="bar", handle=dt.handle, title=f"{area}: downtime days, {fmt_day(s)} – {fmt_day(e)}",
                        requested_by_user=True, encodings={"x": "asset_id", "y": "downtime_days", "sort": "desc"})],
        assumptions=assumptions(dt), follow_ups=["For these three wells, what have repairs cost this year?"]))


def q7() -> dict:
    q = "Show on a map every well in Northeast Block 8 with a real spill in the last 90 days."
    r = Run("q7", "Where things are", q, ["map", "spill", "northeast block 8"], ["spill-accounting", "visual-choice"])
    ent = r.call("resolve_entity", tools.resolve_entity, text=q)
    s, e = window_of(ent, "90 days")
    area = area_of(ent)
    sp = r.call("spills", tools.spills, scope=tools.Scope(kind="area", ids=[area]), start=s, end=e)
    rows = r.rows(sp)
    wells = list(dict.fromkeys(x["asset_id"] for x in rows))
    md = (f"**{len(wells)} wells** in {area} had a real spill between {fmt_day(s)} and {fmt_day(e)}:\n\n"
          + "\n".join(f"- **{x['asset_id']}**: {fmt_day(x['inspected_at'])}, {x['volume_bbl']} bbl "
                      f"({x['inspection_id']})" for x in rows)
          + "\n\nDetections later corrected as not a spill are excluded.")
    return r.finish(AnswerSpec(
        markdown=md,
        claims=[Claim(text=f"{len(wells)} wells", value=len(wells), handle=sp.handle)] +
               [Claim(text=f"{x['volume_bbl']} bbl", value=x["volume_bbl"], handle=sp.handle, column="volume_bbl",
                      row_key={"inspection_id": x["inspection_id"]}) for x in rows],
        visuals=[Visual(type="map", handle=sp.handle, title=f"{area}: real spills, last 90 days", requested_by_user=True,
                        encodings={"lat": "lat", "lon": "lon", "size": "volume_bbl", "label": "asset_id"})],
        citations=[Citation(kind="inspection", id=x["inspection_id"]) for x in rows],
        assumptions=assumptions(sp)))


def q8() -> dict:
    q = "Show a heatmap of 2026 spills by geographic area and month."
    r = Run("q8", "Two dimensions", q, ["heatmap", "area", "month"], ["spill-accounting", "visual-choice"])
    ent = r.call("resolve_entity", tools.resolve_entity, text=q)
    s, e = window_of(ent, "2026")
    hm = r.call("spills", tools.spills, scope=None, start=s, end=e, group_by="area_month")
    rows = r.rows(hm)
    by_area = r.call("spills", tools.spills, scope=None, start=s, end=e, group_by="area")
    tot = {x["area"]: x["spill_count"] for x in r.rows(by_area)}
    top = max(tot, key=tot.get)
    months = sorted({x["month"] for x in rows})
    empty = [(a, m) for a in tot for m in months if not any(x["area"] == a and x["month"] == m for x in rows)]
    md = (f"Real spills in 2026 by area and month ({months[0]} to {months[-1]}), with corrected detections removed. "
          f"**{top}** has the most, **{tot[top]}**. Totals: " + "; ".join(f"{a} {n}" for a, n in sorted(tot.items())) + ".")
    if empty:
        md += " Months with no spill: " + "; ".join(f"{a} {m}" for a, m in empty) + "."
    return r.finish(AnswerSpec(
        markdown=md, claims=[Claim(text=f"{a} {n}", value=n, handle=by_area.handle, column="spill_count",
                                   row_key={"area": a}) for a, n in tot.items()],
        visuals=[Visual(type="heatmap", handle=hm.handle, title="Real spills per area per month, 2026",
                        requested_by_user=True, encodings={"x": "month", "y": "area", "value": "spill_count", "fill": 0})],
        assumptions=assumptions(hm, ["a missing cell means no real spill that month (shown as 0)"])))


def q9() -> dict:
    q = "Which rules has WELL-0001 broken in the last 12 months, and what is still open?"
    r = Run("q9", "Records + documents", q, ["rules", "broken", "open"], ["sop-compliance-audit"])
    ent = r.call("resolve_entity", tools.resolve_entity, text=q)
    s, e = window_of(ent, "12 months")
    ob = r.call("obligations", tools.obligations, scope=tools.Scope(kind="wells", ids=["WELL-0001"]), start=s, end=e)
    rows = r.rows(ob)
    br = [x for x in rows if x["state"] == "breached"]
    pend = [x for x in rows if x["state"] == "pending"]
    md = (f"From {fmt_day(s)} to {fmt_day(e)}, WELL-0001 has **{len(br)} breaches**:\n\n"
          + "\n".join(f"- **{x['protocol']}** {x['rule']}: {x['detail']}" for x in br)
          + (("\n\nStill pending:\n\n" + "\n".join(f"- **{x['protocol']}** {x['detail']}" for x in pend)) if pend else ""))
    return r.finish(AnswerSpec(
        markdown=md, claims=[Claim(text=x["state"], value=x["state"], handle=ob.handle, column="state",
                                   row_key={"obligation_id": x["obligation_id"]}) for x in br],
        visuals=[Visual(type="obligation_timeline", handle=ob.handle, title="WELL-0001 obligations, last 12 months")],
        assumptions=assumptions(ob)))


def q12() -> tuple[dict, dict]:
    q = ("The 15 Oct reading for WELL-0164 is wrong. The handheld thermal gun used in that inspection was faulty; "
         "the field crew re-measured the wellhead at 41.0 °C.")
    r = Run("q12", "Correct raw data", q, ["wrong", "re-measured", "41"], ["feedback-intake", "conflict-handling"])
    ent = r.call("resolve_entity", tools.resolve_entity, text=q)
    ins = r.call("inspection_series", tools.inspection_series, well="WELL-0164", start=window_of(ent, "15 oct")[0],
                 end=window_of(ent, "15 oct")[1])
    iid = r.rows(ins)[0]["inspection_id"]
    a = r.call("assert_fact", memory.assert_fact, a=AssertionIn(
        kind="value_correction", scope_level="inspection", scope_key=iid, target_attr="wellhead_temp_c", value_num=41.0,
        reason="Handheld thermal gun faulty; field crew re-measured the wellhead at 41.0 °C"))
    diff = r.call("sql", tools.sql, shape="diff", query=(
        "SELECT a.assertion_id, a.scope_key AS inspection_id, i.asset_id, a.target_attr AS attribute, "
        "a.original_num AS original, a.value_num AS corrected, u.name AS author, a.reason, a.status "
        "FROM assertions a JOIN inspections i ON i.dataset_id = a.dataset_id AND i.inspection_id = a.scope_key "
        "LEFT JOIN users u ON u.dataset_id = a.dataset_id AND u.user_id = a.author_user_id "
        f"WHERE a.dataset_id = {{ds}} AND a.assertion_id = '{a.assertion_id}'"))
    st = r.call("well_status", tools.well_status, scope=tools.Scope(kind="wells", ids=["WELL-0164"]))
    d, w = r.rows(diff)[0], r.rows(st)[0]
    cf: list[Conflict] = list({c.subject: c for c in a.conflicts + st.conflicts}.values())
    other = next((s for c in cf for s in c.sides if s.get("source") == "inspection_note"), None)
    md = (f"Recorded. Inspection **{iid}** ({fmt_day(r.rows(ins)[0]['date'])}) wellhead temperature is corrected from "
          f"**{d['original']} °C** to **{d['corrected']} °C** by {d['author']}; the original value is kept and the "
          f"correction ({a.assertion_id}) applies to everyone now. You can undo it.\n\n"
          f"With {w['wellhead_c']} °C against {w['ambient_c']} °C air ({w['delta_c']} °C warmer, above the "
          f"{w['well_type']} limit of {fmt_num(w['threshold_c'])} °C), WELL-0164 **is "
          f"{'' if w['producing'] else 'not '}producing oil**.")
    if other:
        md += (f"\n\nThe same inspection has the note *“{other['says']}”* ({other['author']}), which disagrees with the "
               f"correction. {d['author'].split()[0]}, can you confirm the well was running?")
    spec = AnswerSpec(
        markdown=md,
        claims=[Claim(text=f"{d['original']} °C", value=d["original"], handle=diff.handle, column="original"),
                Claim(text=f"{d['corrected']} °C", value=d["corrected"], handle=diff.handle, column="corrected"),
                Claim(text=f"{w['delta_c']} °C warmer", value=w["delta_c"], handle=st.handle, column="delta_c")],
        visuals=[Visual(type="correction_diff", handle=diff.handle, title=f"Correction {a.assertion_id}",
                        encodings={"assertion_id": a.assertion_id, "before": "original", "after": "corrected"}),
                 Visual(type="conflict_card", title="The data and a note disagree", encodings={"conflict_index": 0})],
        citations=[Citation(kind="assertion", id=a.assertion_id), Citation(kind="inspection", id=iid)],
        conflicts=cf, applied_overlays=[a.assertion_id],
        assumptions=[f"correction scoped to inspection {iid}", "committed as confirmed (ADR-013); undo keeps history",
                     f"dataset {settings.active_dataset}"])
    first = r.finish(spec)
    # Then Michael asks Q2 again, in the same chat.
    r2, st2, spec2 = q2("q12b", "Q2 after the correction",
                        "After that correction, which wells in Northeast Block 8 are not producing oil right now, and why?",
                        after_correction=True)
    r2.results += [diff, st]
    r2.raw += r.raw
    now = {x["asset_id"] for x in r2.rows(st2)}
    if w["asset_id"] not in now:
        spec2.markdown += (f"\n\n{w['asset_id']} is no longer listed: its {fmt_day(r.rows(ins)[0]['date'])} reading was corrected to "
                           f"{d['corrected']} °C by {d['author']} ({a.assertion_id}), so it counts as producing.")
        spec2.applied_overlays = [a.assertion_id]
        spec2.citations.append(Citation(kind="assertion", id=a.assertion_id))
    return first, r2.finish(spec2)


def q18() -> dict:
    q = "Was the 20 Aug spill at WELL-0101 real oil?"
    r = Run("q18", "Evidence chain", q, ["real oil", "well-0101", "20 aug"], ["spill-accounting", "conflict-handling"])
    ent = r.call("resolve_entity", tools.resolve_entity, text=q)
    s, e = window_of(ent, "20 aug")
    ser = r.call("inspection_series", tools.inspection_series, well="WELL-0101", start=s, end=e)
    iid = r.rows(ser)[0]["inspection_id"]
    ch = r.call("evidence_chain", tools.evidence_chain, id=iid)
    rows = r.rows(ch)
    ins = r.rows(ser)[0]
    notes = [x for x in rows if x["kind"] == "note"]
    reps = [x for x in rows if x["kind"] == "maintenance"]
    md = (f"**{'Yes' if ins['spill_real'] else 'No'}.** Inspection {iid} flagged a spill"
          + (f" (*“{ins['notes']}”*)" if ins["notes"] else "") + ". "
          + (f"{len(notes)} notes disagree about it:\n\n" + "\n".join(f"- {x['id']}: {x['summary']}" for x in notes)
             if notes else "")
          + "\n\nNeither note is a formal correction"
          + (f", and a repair ({', '.join(x['id'] for x in reps)}) was done" if reps else "")
          + ", so the detection stands.")
    return r.finish(AnswerSpec(
        markdown=md,
        claims=[Claim(text=iid, value=iid, handle=ser.handle, column="inspection_id")],
        visuals=[Visual(type="evidence_chain", handle=ch.handle, title=f"Evidence chain for {iid}")],
        citations=[Citation(kind="inspection", id=iid)] + [Citation(kind="note", id=x["id"]) for x in notes],
        assumptions=assumptions(ch)))


def _q2() -> dict:
    r, _, spec = q2()
    return r.finish(spec)


def records(scenarios: list[dict]) -> dict[str, dict]:
    """Raw records behind every cited id, in the GET /api/records/{kind}/{id} shape."""
    db, ids = get_db(), {}
    for s in scenarios:
        blob = json.dumps(s, default=str)
        for c in s["answer"].get("citations", []) + [c for h in s["handles"].values() for c in h["citations"]]:
            ids[(c["kind"], c["id"])] = None
        for m in ID_RE.findall(blob):
            ids[(cite_kind(m), m)] = None
        for m in set(__import__("re").findall(r"\bA-[0-9a-f]{8}\b", blob)):
            ids[("assertion", m)] = None
    out = {}
    for kind, rid in ids:
        if kind == "assertion":
            rows = db.rows("SELECT * FROM assertions WHERE assertion_id=?", [rid])
        elif kind in RECORDS:
            table, col = RECORDS[kind]
            rows = db.rows(f"SELECT * FROM {table} WHERE dataset_id=? AND {col}=?", [settings.active_dataset, rid])
            if kind == "maintenance" and rows:  # a maintenance citation also resolves to its report text
                rep = db.rows("SELECT text FROM reports WHERE dataset_id=? AND maintenance_id=?", [settings.active_dataset, rid])
                if rep:
                    out[f"report:{rid}"] = {"kind": "report", "id": rid, "record": {"maintenance_id": rid, **rep[0]}}
        else:
            continue
        if rows:
            out[f"{kind}:{rid}"] = {"kind": kind, "id": rid, "record": rows[0] if len(rows) == 1 else rows}
    return out


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    if Path(settings.db_path).resolve() == (ROOT / "backend" / "wellops.duckdb").resolve():
        sys.exit("refusing to write the Q12 correction into the shared database; set DB_PATH to a private copy")
    db = get_db()
    db.run("DELETE FROM handles WHERE chat_id=?", [CHAT])
    db.run("DELETE FROM assertions WHERE source='chat' AND source_ref=?", [CHAT])
    scenarios = [q1(), _q2(), q3(), q5(), q6(), q7(), q8(), q9(), q18(), *q12()]  # q12 last: its correction changes later answers
    data = {"generated_by": "backend/scripts/make_fixtures.py", "today": str(settings.clock_today),
            "dataset": settings.active_dataset, "scenarios": scenarios,
            "users": db.rows("SELECT user_id, name, role, team_id FROM users WHERE dataset_id=? ORDER BY user_id",
                             [settings.active_dataset]), "records": records(scenarios)}
    out.write_text(json.dumps(data, indent=1, default=str, ensure_ascii=False) + "\n")
    for s in scenarios:
        print(f"{s['id']:5} verified={s['verification']['ok']!s:5} tools={[t['name'] for t in s['tools']]}")
    print(f"wrote {out} ({out.stat().st_size // 1024} KB, {len(data['records'])} records)")


if __name__ == "__main__":
    main()
