"""obligations(): SOP/manual duties as met | breached | pending, with due dates and evidence ids.

All thresholds are read from params() (runtime override > manual > SOP), never hard-coded here.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from wellops.contracts import Citation, RunDeps

from .core import ToolOut, coerce_scope, ds, finish, get_db, lit, mac, month_add, in_list, win_pred, window, well_pred, scope_text
from .data import rca_sql

COLS = ["asset_id", "protocol", "rule", "trigger_ref", "trigger_at", "due_at", "state", "detail", "evidence_ids",
        "fine_usd", "threshold_source"]
PLANNED_RE = re.compile(r"planned|scheduled", re.I)
SHUTIN_RE = re.compile(r"shut[- ]?in|workover|rod string|replacement", re.I)


def _ts(v) -> datetime:
    t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _fmt(t: datetime) -> str:
    return t.strftime("%Y-%m-%d %H:%M")


def obligations(deps: RunDeps, scope=None, start: str | None = None, end: str | None = None) -> ToolOut:
    """Evaluate SR-01/02/03, RC-01/02/04, manual §10 (RCA) and §4 (planned shut-in recorded) for triggers in the window."""
    db = get_db()
    sc = coerce_scope(db, deps, scope)
    s, e = window(deps, start, end)
    d, now = ds(deps), _ts(deps.asof.replace(" ", "T"))
    pred = lambda col: well_pred(db, deps, sc, col)
    P = {(r["asset_id"], r["param"]): r for r in db.rows(
        f"SELECT * FROM {mac(deps, 'params')} WHERE {pred('asset_id')}")}
    pv = lambda a, k: P[(a, k)]["value"]
    src = lambda a, k: P[(a, k)]["source"]
    out: list[dict] = []

    def row(a, proto, rule, ref, trig, due, state, detail, ev, thr, fine=None):
        out.append({"asset_id": a, "protocol": proto, "rule": rule, "trigger_ref": ref,
                    "trigger_at": _fmt(trig) if isinstance(trig, datetime) else trig,
                    "due_at": _fmt(due) if isinstance(due, datetime) else due, "state": state, "detail": detail,
                    "evidence_ids": ", ".join(dict.fromkeys(x for x in ev if x)), "fine_usd": fine, "threshold_source": thr})

    spills_sql = f"""SELECT e.inspection_id, e.asset_id, e.inspected_at, e.volume_eff, e.spill_class, e.volume_overlay
FROM {mac(deps, 'spills_real')} e WHERE {pred('e.asset_id')} AND {win_pred('e.inspected_at', s, e)} ORDER BY e.inspected_at"""
    regs = db.rows(f"SELECT * FROM reg_events WHERE dataset_id={d} AND {pred('asset_id')}")
    facts = {(r["inspection_id"], r["field"]): r for r in db.rows(
        f"""SELECT m.triggered_by_inspection_id inspection_id, f.field, f.num, f.value, m.maintenance_id, f.span_start, f.span_end
            FROM report_facts f JOIN maintenance m USING (dataset_id, maintenance_id)
            WHERE f.dataset_id={d} AND f.field='containment_hours' AND m.triggered_by_inspection_id IS NOT NULL""")}
    cites: list[Citation] = []
    for sp in db.rows(spills_sql):
        a, ins, t, cls = sp["asset_id"], sp["inspection_id"], _ts(sp["inspected_at"]), sp["spill_class"]
        ov = [sp["volume_overlay"]]
        related = [g for g in regs if g["related_inspection_id"] == ins]
        vol = f"{sp['volume_eff']} bbl {cls}" + (" (volume corrected by overlay)" if sp["volume_overlay"] else "")
        reportable = cls in ("reportable", "major")
        if reportable:  # SR-01
            k = "spill_report_hours_major" if cls == "major" else "spill_report_hours"
            lim = pv(a, k)
            due = t + timedelta(hours=lim)
            filed = next((g for g in related if g["event_type"] == "spill_report_filed"), None)
            nov = [g for g in related if g["event_type"] == "notice_of_violation"]
            nov_txt = "".join(f"; NOV {g['event_id']} [{g['status']}] fine ${g['fine_usd']:,.0f}" for g in nov)
            if filed:
                hrs = filed["hours_to_report"] if filed["hours_to_report"] is not None else \
                    (_ts(filed["date"]) - t).total_seconds() / 3600
                row(a, "SR-01", "written notice within limit of discovery", ins, t, due,
                    "met" if hrs <= lim else "breached",
                    f"{vol}; written notice filed {hrs:g} h after detection (limit {lim:g} h), {filed['event_id']}{nov_txt}",
                    [ins, filed["event_id"], *[g["event_id"] for g in nov], *ov], src(a, k), sum(g["fine_usd"] or 0 for g in nov) or None)
            else:
                row(a, "SR-01", "written notice within limit of discovery", ins, t, due,
                    "breached" if now > due else "pending",
                    f"{vol}; no written notice recorded; due {_fmt(due)} ({lim:g} h after the inspection)"
                    + (" - overdue" if now > due else ""), [ins, *ov], src(a, k))
        # SR-02 containment (any real spill) from report text
        f = facts.get((ins, "containment_hours"))
        lim2 = pv(a, "containment_hours")
        if f:
            row(a, "SR-02", "contain within limit of discovery", ins, t, t + timedelta(hours=lim2),
                "breached" if f["num"] > lim2 else "met",
                f"{vol}; contained after about {f['num']:g} h (limit {lim2:g} h), only stated in report {f['maintenance_id']}",
                [ins, f["maintenance_id"]], src(a, "containment_hours"))
            cites.append(Citation(kind="report", id=f["maintenance_id"], span=(f["span_start"], f["span_end"]),
                                  label="containment_hours"))
        else:
            row(a, "SR-02", "contain within limit of discovery", ins, t, t + timedelta(hours=lim2), "pending",
                f"{vol}; no containment time found in any report", [ins], src(a, "containment_hours"))
        if reportable:  # SR-03
            days = pv(a, "remediation_verify_days")
            due = t + timedelta(days=days)
            si = next((g for g in related if g["event_type"] == "site_inspection"), None)
            if si and si["status"] == "closed":
                ok = _ts(si["date"]) <= due
                row(a, "SR-03", "agency verification within limit", ins, t, due, "met" if ok else "breached",
                    f"{vol}; site inspection {si['event_id']} closed on {si['date']}" + ("" if ok else " (after the deadline)"),
                    [ins, si["event_id"]], src(a, "remediation_verify_days"))
            else:
                late = now > due
                row(a, "SR-03", "agency verification within limit", ins, t, due, "breached" if late else "pending",
                    f"{vol}; verification due {_fmt(due)}" + (f"; site inspection {si['event_id']} still open"
                                                              if si else "; no site inspection recorded"),
                    [ins, si and si["event_id"]], src(a, "remediation_verify_days"))
    # RC-04: every NOV in the window
    for g in (g for g in regs if g["event_type"] == "notice_of_violation" and
              win_ok(g["date"], s, e)):
        a = g["asset_id"]
        days = pv(a, "nov_response_days")
        t = _ts(g["date"])
        due = t + timedelta(days=days)
        resp = next((r for r in sorted(regs, key=lambda r: str(r["date"])) if r["asset_id"] == a and
                     r["event_type"] == "nov_response_filed" and r["related_inspection_id"] == g["related_inspection_id"]
                     and str(r["date"]) >= str(g["date"])), None)
        if resp:
            ok = _ts(resp["date"]) <= due
            row(a, "RC-04", "answer NOV within limit", g["event_id"], t.strftime("%Y-%m-%d"), due.strftime("%Y-%m-%d"),
                "met" if ok else "breached", f"NOV answered on {resp['date']} ({resp['event_id']})" + ("" if ok else ", late"),
                [g["event_id"], resp["event_id"]], src(a, "nov_response_days"), g["fine_usd"])
        else:
            row(a, "RC-04", "answer NOV within limit", g["event_id"], t.strftime("%Y-%m-%d"), due.strftime("%Y-%m-%d"),
                "breached" if now > due else "pending",
                f"NOV [{g['status']}] fine ${g['fine_usd']:,.0f}; no nov_response_filed recorded; response due {due:%Y-%m-%d}",
                [g["event_id"]], src(a, "nov_response_days"), g["fine_usd"])
    # RC-02: flowline integrity test per calendar year that overlaps the window
    first = s or date(1900, 1, 1)
    wells = db.rows(f"SELECT asset_id, installed_on FROM wells WHERE dataset_id={d} AND {pred('asset_id')}")
    tests = db.rows(f"""SELECT asset_id, maintenance_id, started_at::DATE AS day FROM maintenance WHERE dataset_id={d}
                        AND issue ILIKE '%flowline integrity test%' AND {pred('asset_id')}""")
    firstins = {r["asset_id"]: r["d"] for r in db.rows(
        f"SELECT asset_id, min(inspected_at)::DATE d FROM inspections WHERE dataset_id={d} AND {pred('asset_id')} GROUP BY 1")}
    for w in wells:
        a = w["asset_id"]
        for y in range(max(first.year, (date.fromisoformat(str(firstins.get(a, deps.today))).year)), e.year + 1):
            ye = date(y, 12, 31)
            if ye < first:
                continue
            t = [x for x in tests if x["asset_id"] == a and str(x["day"]).startswith(str(y))]
            if t:
                row(a, "RC-02", "flowline integrity test per calendar year", f"{y}", f"{y}-01-01", str(ye), "met",
                    f"flowline integrity test done {t[0]['day']} ({t[0]['maintenance_id']})", [t[0]["maintenance_id"]], "SOP-RC §2")
            else:
                row(a, "RC-02", "flowline integrity test per calendar year", f"{y}", f"{y}-01-01", str(ye),
                    "breached" if ye < deps.today else "pending",
                    f"no flowline integrity test recorded in {y}" + ("" if ye < deps.today else f"; due by {ye}"), [], "SOP-RC §2")
    # RC-01: gaps between consecutive inspections
    gaps = db.rows(f"""SELECT asset_id, prev_id, inspection_id, prev_at, inspected_at, gap FROM (
        SELECT asset_id, inspection_id, inspected_at, lag(inspection_id) OVER w prev_id, lag(inspected_at) OVER w prev_at,
               date_diff('day', lag(inspected_at) OVER w, inspected_at) gap FROM {mac(deps, 'ins_eff')}
        WHERE {pred('asset_id')} WINDOW w AS (PARTITION BY asset_id ORDER BY inspected_at))
        WHERE gap > 14 AND {win_pred('inspected_at', s, e)}""")
    for g in gaps:
        a = g["asset_id"]
        if g["gap"] > pv(a, "inspection_max_gap_days"):
            row(a, "RC-01", "inspect at least every N days", g["inspection_id"], g["prev_at"][:10], g["inspected_at"][:10],
                "breached", f"{g['gap']} days between {g['prev_id']} and {g['inspection_id']}",
                [g["prev_id"], g["inspection_id"]], "SOP-RC §2")
    # Manual §10: RCA
    for r in db.rows(rca_sql(db, deps, sc, None, False)):
        a = r["asset_id"]
        rep = db.rows(f"""SELECT f.maintenance_id FROM report_facts f WHERE f.dataset_id={d} AND f.field='compliance:Manual §10'
                          AND f.value ILIKE 'Action required%' AND f.maintenance_id IN {in_list(r['maintenance_ids'].split(', '))}""")
        row(a, "Manual §10", "root-cause analysis after N corrective repairs in 12 months", r["equipment_kind"],
            None, None, "pending",
            f"{r['corrective_count']} corrective {r['equipment_kind']} repairs in 12 months (threshold {r['threshold']:g}); "
            f"RCA required, none recorded" + (f"; report {rep[0]['maintenance_id']} marks 'Action required'" if rep else ""),
            r["maintenance_ids"].split(", ") + [r["threshold_overlay"]], r["threshold_source"])
    # Manual §4: planned shut-in must be in the maintenance system before work
    notes = db.rows(f"""SELECT c.asset_id, c.ref, c.text, c.valid_from, c.valid_to, c.source FROM {mac(deps, 'context_for')} c
                        WHERE {pred('c.asset_id')} AND c.kind IN ('operator_feedback', 'operator_note', 'planned_downtime', 'shared_fact')
                        AND c.status <> 'superseded'""")
    for n in notes:
        txt = n["text"] or ""
        if not (SHUTIN_RE.search(txt) and PLANNED_RE.search(txt) and n["valid_from"]):
            continue
        vf, vt = date.fromisoformat(str(n["valid_from"])), date.fromisoformat(str(n["valid_to"] or "9999-12-31"))
        if vf > e or vt < (s or date.min):
            continue
        a = n["asset_id"]
        m = db.rows(f"""SELECT maintenance_id FROM maintenance WHERE dataset_id={d} AND asset_id={lit(a)}
                        AND started_at::DATE BETWEEN DATE '{vf - timedelta(days=3)}' AND DATE '{min(vt, deps.today)}'""")
        if not m:
            row(a, "Manual §4", "planned shut-in recorded in maintenance system before work", n["ref"], str(vf), None,
                "pending", f"planned shut-in from {vf} ({n['ref']}) has no maintenance record; manual §4 requires one "
                           f"before work starts", [n["ref"]], "OM-T200 §4")
    cols = COLS
    rows = [{c: r.get(c) for c in cols} for r in out]
    return finish(db, deps, "obligation_set", cols, rows, sql=spills_sql, citations=cites, notes=[
        f"window {s or 'start of data'}..{e} applied to triggers (spill/NOV dates, calendar years, gaps); {scope_text(sc)}",
        "state: met | breached | pending (deadline not reached, or action still open). Overdue with no evidence = breached",
        "thresholds from params(): runtime override > manual > SOP; 'now' = as-of end of today",
        "rows are computed from several queries; sql shows the spill trigger query only"])


def win_ok(day, s: date | None, e: date) -> bool:
    x = date.fromisoformat(str(day)[:10])
    return x <= e and (s is None or x >= s)
