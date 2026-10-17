"""Semantic data tools. Plain functions: (RunDeps, args) -> ToolOut. No LLM inside."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from wellops.contracts import Citation, RunDeps
from wellops.memory import conflicts as cf

from .core import (Db, Scope, ToolError, ToolOut, area_ids, at, coerce_scope, ds, finish, get_db, in_list, lit, mac,
                   parse_date, query_result, scope_text, user_names, well_pred, win_pred, window)

REASON_RE = re.compile(r"shut[- ]?in|shutdown|delay|waiting|workover|planned|outage|replacement|tie-in|restart|"
                       r"awaiting|parts", re.I)
NON_EXPLANATION = ("correction", "detection_correction", "value_correction", "analysis_correction", "status_claim",
                   "rule_override")


def _ts(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


def _d(v) -> str:
    return str(v)[:10]


def well_status(deps: RunDeps, scope: Scope | dict | str | None = None, at_date: str | None = None,
                only: Literal['producing', 'not_producing'] | None = None) -> ToolOut:
    """Status of wells from each well's latest inspection (optionally the latest on/before `at_date`)."""
    db = get_db()
    pred = well_pred(db, deps, scope, "s.asset_id")
    cut = parse_date(at_date)
    cut_sql = f"WHERE inspected_at <= TIMESTAMPTZ '{cut} 23:59:59+00'" if cut else ""
    sql = f"""SELECT s.asset_id, w.name AS well_name, s.well_type, a.name AS area, s.inspection_id, s.inspected_at,
  s.inspector_user_id, coalesce(u.name, s.inspector_user_id) AS inspector, s.method, s.ambient_eff AS ambient_c,
  s.wellhead_eff AS wellhead_c, s.wellhead_temp_c AS wellhead_recorded_c, round(s.delta_c, 1) AS delta_c,
  s.delta_min_c AS threshold_c, round(s.delta_c - s.delta_min_c, 1) AS margin_c, s.producing,
  w.recorded_status, (w.recorded_status = 'active') <> s.producing AS recorded_status_stale,
  s.notes AS inspection_note, s.wellhead_overlay, s.ambient_overlay, s.delta_rule
FROM (SELECT * FROM {mac(deps, 'ins_eff')} {cut_sql}
      QUALIFY row_number() OVER (PARTITION BY asset_id ORDER BY inspected_at DESC) = 1) s
JOIN wells w ON w.dataset_id = {ds(deps)} AND w.asset_id = s.asset_id
JOIN areas a ON a.dataset_id = {ds(deps)} AND a.area_id = s.area_id
LEFT JOIN users u ON u.dataset_id = {ds(deps)} AND u.user_id = s.inspector_user_id
WHERE {pred}{'' if not only else ' AND ' + ('' if only == 'producing' else 'NOT ') + 's.producing'} ORDER BY s.asset_id"""
    cols, rows = db.q(sql)
    _attach_explanations(db, deps, rows)
    cols += ["reason_summary", "explanation"]
    confl = cf.scan_inspections(db, deps, [r["inspection_id"] for r in rows]) + \
        cf.scan_claims(db, deps, [r["asset_id"] for r in rows])
    notes = ["Status = effective reading of each well's latest inspection (overlays applied); producing ⇔ Δ ≥ threshold, "
             "where Δ = wellhead − ambient. recorded_status is a monthly snapshot and can be stale.",
             f"scope: {scope_text(coerce_scope(db, deps, scope))}" + (f", latest on/before {cut}" if cut else "")]
    if len(confl) > 25:
        notes.append(f"{len(confl)} conflicts found; first 25 returned")
    return finish(db, deps, "entity" if len(rows) == 1 else "entity_list", cols, rows, sql=sql, notes=notes,
                  conflicts=confl[:25])


def _attach_explanations(db: Db, deps: RunDeps, rows: list[dict]) -> None:
    """explanation = context whose validity covers the reading + repairs open at that time; never invented."""
    nonprod = [r for r in rows if not r["producing"]]
    for r in rows:
        r["reason_summary"], r["explanation"] = "producing", []
    if not nonprod:
        return
    assets = [r["asset_id"] for r in nonprod]
    ctx = db.rows(f"""SELECT c.asset_id, c.ref, c.kind, c.text, c.valid_from, c.valid_to, c.status, c.source,
                             coalesce(u.name, c.author_user_id) AS author
                      FROM {mac(deps, 'context_for')} c
                      LEFT JOIN users u ON u.dataset_id={ds(deps)} AND u.user_id=c.author_user_id
                      WHERE c.asset_id IN {in_list(assets)} AND c.kind NOT IN {in_list(NON_EXPLANATION)}""")
    rep = db.rows(f"""SELECT asset_id, maintenance_id, maintenance_type, equipment_kind, issue, started_at, completed_at
                      FROM {mac(deps, 'repairs')} WHERE asset_id IN {in_list(assets)} AND completed_at > started_at""")
    for r in nonprod:
        day, when, items = _d(r["inspected_at"]), _ts(r["inspected_at"]), []
        for c in (c for c in ctx if c["asset_id"] == r["asset_id"]):
            if (c["valid_from"] or "0000") <= day <= (c["valid_to"] or "9999"):
                items.append({"kind": "note", "ref": c["ref"], "author": c["author"], "text": c["text"],
                              "window": f"{c['valid_from'] or '…'}..{c['valid_to'] or 'open'}",
                              "role": "reason" if REASON_RE.search(c["text"] or "") else "context",
                              "status": c["status"]})
        for m in (m for m in rep if m["asset_id"] == r["asset_id"]):
            if _ts(m["started_at"]) <= when < _ts(m["completed_at"]):
                items.append({"kind": "repair", "ref": m["maintenance_id"], "author": None, "role": "reason",
                              "text": f"{m['maintenance_type']} {m['equipment_kind']}: {m['issue']}; started "
                                      f"{_d(m['started_at'])}, planned end {_d(m['completed_at'])} (open at reading)",
                              "window": f"{_d(m['started_at'])}..{_d(m['completed_at'])}", "status": "open"})
        reasons = [i for i in items if i["role"] == "reason"]
        r["explanation"] = items
        r["reason_summary"] = ("; ".join(f"{i['ref']}: {i['text']}" for i in reasons) if reasons else
                               "No recorded reason: no note or open repair covers this reading. Suggest a site check.")


def inspection_series(deps: RunDeps, well: str, start: str | None = None, end: str | None = None) -> ToolOut:
    db = get_db()
    s, e = window(deps, start, end)
    if not re.fullmatch(r"WELL-\d{4}", well.upper()):
        raise ToolError("well must look like WELL-0001 (use resolve_entity)")
    sql = f"""SELECT e.inspected_at::DATE AS date, e.inspected_at, e.inspection_id, e.ambient_eff AS ambient_c,
  e.wellhead_eff AS wellhead_c, e.wellhead_temp_c AS wellhead_recorded_c, round(e.delta_c, 1) AS delta_c,
  e.delta_min_c AS threshold_c, e.producing, e.spill_real, e.notes, e.wellhead_overlay, e.ambient_overlay
FROM {mac(deps, 'ins_eff')} e WHERE e.asset_id = {lit(well.upper())} AND {win_pred('e.inspected_at', s, e)}
ORDER BY e.inspected_at"""
    return query_result(db, deps, "timeseries", sql,
                        [f"window {s or 'start'}..{e} (inclusive, UTC dates)", "missed inspections are absent, not zero-filled"])


GROUPS = {
    "well": ("e.asset_id, e.well_type, a.name AS area", "e.asset_id, e.well_type, a.name", "category_measure"),
    "area": ("a.name AS area", "a.name", "category_measure"),
    "well_type": ("e.well_type", "e.well_type", "category_measure"),
    "month": ("strftime(e.inspected_at, '%Y-%m') AS month", "1", "timeseries"),
    "year": ("year(e.inspected_at) AS year", "1", "timeseries"),
    "area_month": ("a.name AS area, strftime(e.inspected_at, '%Y-%m') AS month", "a.name, 2", "matrix"),
    "none": ("", "", "scalar"),
}


def downtime(deps: RunDeps, scope=None, start: str | None = None, end: str | None = None,
             group_by: Literal["well", "area", "well_type", "month", "year", "area_month", "none"] | None = None,
             top_k: int | None = None) -> ToolOut:
    """Downtime = 7 days × inspections where the well was not producing. Never maintenance.downtime_hours."""
    db = get_db()
    sc = coerce_scope(db, deps, scope)
    s, e = window(deps, start, end)
    g = group_by or ("none" if sc.kind == "wells" and len(sc.ids) == 1 else "well")
    sel, grp, shape = GROUPS[g]
    extra = ", string_agg(strftime(e.inspected_at, '%Y-%m-%d'), ', ' ORDER BY e.inspected_at) " \
            "FILTER (WHERE NOT e.producing) AS non_producing_dates" if g in ("well", "none") else ""
    order = "downtime_days DESC, 1" if g in ("well", "area", "well_type") else (grp if g != "none" else "1")
    if g in ("month", "year", "area_month"):
        order = "1, 2" if g == "area_month" else "1"
    sql = f"""SELECT {sel + ',' if sel else ''} count(*) FILTER (WHERE NOT e.producing) AS non_producing_inspections,
  7 * count(*) FILTER (WHERE NOT e.producing) AS downtime_days, count(*) AS inspections{extra}
FROM {mac(deps, 'ins_eff')} e JOIN areas a ON a.dataset_id = {ds(deps)} AND a.area_id = e.area_id
WHERE {well_pred(db, deps, sc, 'e.asset_id')} AND {win_pred('e.inspected_at', s, e)}
{'GROUP BY ' + grp if grp else ''} ORDER BY {order}{f' LIMIT {int(top_k)}' if top_k else ''}"""
    return query_result(db, deps, shape, sql, [
        f"window {s or 'start of data'}..{e} inclusive (UTC dates); {scope_text(sc)}",
        "downtime = 7 days × inspections with Δ < threshold; missed inspections add 0; "
        "maintenance.downtime_hours is per repair job and is NOT used",
        "ties at the top_k boundary are broken by well id"])


def spills(deps: RunDeps, scope=None, start: str | None = None, end: str | None = None, real_only: bool = True,
           group_by: Literal["well", "area", "month", "area_month", "none"] | None = None) -> ToolOut:
    """Real spills (detected and not corrected). Corrected detections are returned under a second handle."""
    db = get_db()
    sc = coerce_scope(db, deps, scope)
    s, e = window(deps, start, end)
    base = f"""FROM {mac(deps, 'spills_real')} e JOIN areas a ON a.dataset_id = {ds(deps)} AND a.area_id = e.area_id
WHERE {well_pred(db, deps, sc, 'e.asset_id')} AND {win_pred('e.inspected_at', s, e)}"""
    if group_by:
        sel, grp, shape = {"well": ("e.asset_id", "1", "category_measure"), "area": ("a.name AS area", "1", "category_measure"),
                           "month": ("strftime(e.inspected_at, '%Y-%m') AS month", "1", "timeseries"),
                           "area_month": ("a.name AS area, strftime(e.inspected_at, '%Y-%m') AS month", "1, 2", "matrix"),
                           "none": ("", "", "scalar")}[group_by]
        sql = (f"SELECT {sel + ',' if sel else ''} count(*) AS spill_count, round(sum(e.volume_eff), 1) AS total_volume_bbl "
               f"{base} {'GROUP BY ' + grp + ' ORDER BY ' + grp if grp else ''}")
        shape_out = shape
    else:
        sql = f"""SELECT e.inspection_id, e.asset_id, a.name AS area, e.inspected_at, e.volume_eff AS volume_bbl,
  e.spill_est_volume_bbl AS estimated_bbl, e.volume_overlay, e.spill_class, e.spill_area_pct, e.lat, e.lon,
  e.spill_class IN ('reportable', 'major') AS reportable, e.notes AS inspection_note
{base} ORDER BY e.inspected_at, e.asset_id"""
        shape_out = "geo_points"
    notes = [f"window {s or 'start of data'}..{e} inclusive; {scope_text(sc)}",
             "real spill = detected and not corrected by a correction note/assertion (SOP-SR §7); class from corrected volume"]
    related: list[str] = []
    corr_sql = f"""SELECT i.inspection_id, i.asset_id, i.inspected_at, i.spill_est_volume_bbl AS estimated_bbl,
  i.detection_correction, coalesce(c.value_text, c.reason) AS correction_text, c.author_user_id AS corrected_by
FROM {mac(deps, 'ins_eff')} i JOIN assertions c ON c.assertion_id = i.detection_correction
WHERE i.spill_detected AND i.detection_correction IS NOT NULL AND {well_pred(db, deps, sc, 'i.asset_id')}
  AND {win_pred('i.inspected_at', s, e)} ORDER BY i.inspected_at"""
    corr = None
    if not group_by:
        corr = query_result(db, deps, "entity_list", corr_sql,
                            ["detections corrected to not-a-spill: excluded from counts, listed for transparency"])
        related.append(corr.handle)
        notes.append(f"{corr.row_count} corrected detection(s) excluded; see handle {corr.handle}")
    out = query_result(db, deps, shape_out, sql, notes)
    out.related_handles = related
    if not real_only and corr is not None:
        out.notes.append("real_only=False: corrected detections are in the related handle, never in the main rows")
    return out


def repair_costs(deps: RunDeps, scope=None, start: str | None = None, end: str | None = None,
                 group_by: Literal["well", "area", "equipment_kind", "maintenance_type", "month", "year", "none"] = "well",
                 maintenance_type: Literal["corrective", "scheduled"] | None = None) -> ToolOut:
    """Repair cost by `started_at` (a repair belongs to the period it started in)."""
    db = get_db()
    sc = coerce_scope(db, deps, scope)
    s, e = window(deps, start, end)
    sel, grp = {"well": ("m.asset_id", "1"), "area": ("a.name AS area", "1"), "equipment_kind": ("m.equipment_kind", "1"),
                "maintenance_type": ("m.maintenance_type", "1"), "month": ("strftime(m.started_at, '%Y-%m') AS month", "1"),
                "year": ("year(m.started_at) AS year", "1"), "none": ("", "")}[group_by]
    sql = f"""SELECT {sel + ',' if sel else ''} count(*) AS jobs, sum(m.cost_total) AS total_usd,
  sum(m.cost_labor) AS labor_usd, sum(m.cost_parts) AS parts_usd, count(*) FILTER (WHERE m.open_as_of) AS open_jobs
FROM {mac(deps, 'repairs')} m JOIN wells w ON w.dataset_id = {ds(deps)} AND w.asset_id = m.asset_id
JOIN areas a ON a.dataset_id = {ds(deps)} AND a.area_id = w.area_id
WHERE {well_pred(db, deps, sc, 'm.asset_id')} AND {win_pred('m.started_at', s, e)}
{f"AND m.maintenance_type = {lit(maintenance_type)}" if maintenance_type else ''}
{'GROUP BY ' + grp + ' ORDER BY ' + ('1' if group_by in ('month', 'year') else 'total_usd DESC, 1') if grp else ''}"""
    return query_result(db, deps, "scalar" if group_by == "none" else "category_measure", sql, [
        f"window {s or 'start of data'}..{e} by repair start date; {scope_text(sc)}; all maintenance types"
        + (f" filtered to {maintenance_type}" if maintenance_type else ""),
        "open_jobs = repairs still in progress at as-of (cost is the recorded total)"])


def rca_sql(db: Db, deps: RunDeps, scope, equipment_kind: str | None, include_below: bool) -> str:
    return f"""SELECT m.asset_id, w.well_type, m.equipment_kind, count(*) AS corrective_count, p.value AS threshold,
  count(*) >= p.value AS meets_threshold, p.source AS threshold_source,
  CASE WHEN p.source LIKE 'assertion:%' THEN substr(p.source, 11) END AS threshold_overlay,
  string_agg(m.maintenance_id, ', ' ORDER BY m.started_at) AS maintenance_ids
FROM {mac(deps, 'repairs')} m JOIN wells w ON w.dataset_id = {ds(deps)} AND w.asset_id = m.asset_id
JOIN {mac(deps, 'params')} p ON p.asset_id = m.asset_id AND p.param = 'rca_corrective_count_min'
JOIN {mac(deps, 'params')} pw ON pw.asset_id = m.asset_id AND pw.param = 'rca_window_months'
WHERE m.maintenance_type = 'corrective' AND {well_pred(db, deps, scope, 'm.asset_id')}
  AND m.started_at > {at(deps)} - to_months(CAST(pw.value AS INTEGER))
  {f"AND m.equipment_kind = {lit(equipment_kind)}" if equipment_kind else ''}
GROUP BY m.asset_id, w.well_type, m.equipment_kind, p.value, p.source
HAVING count(*) >= p.value{' - 1' if include_below else ''}
ORDER BY m.asset_id, m.equipment_kind"""


def rca_candidates(deps: RunDeps, scope=None, equipment_kind: str | None = None, include_below: bool = False) -> ToolOut:
    """Wells needing a root-cause analysis: corrective repairs on the same equipment kind in the last 12 months
    reaching the effective threshold (manual §10, or a confirmed runtime rule override for the well's scope)."""
    db = get_db()
    sc = coerce_scope(db, deps, scope)
    sql = rca_sql(db, deps, sc, equipment_kind, include_below)
    return query_result(db, deps, "entity_list", sql, [
        f"{scope_text(sc)}; corrective repairs by started_at in the 12 months before {deps.today}",
        "threshold per well from params: runtime rule override (scope-specific) > manual (T350 inherits T200 §10)"
        + ("; include_below=True also lists equipment one repair short" if include_below else "")])


def evidence_chain(deps: RunDeps, id: str) -> ToolOut:
    """inspection -> maintenance -> report (+facts with spans) -> regulatory events -> notes/assertions."""
    db = get_db()
    d = ds(deps)
    ident = id.strip()
    ins = ident if ident.startswith("INS-") else None
    if ident.startswith("MNT-"):
        ins = (db.rows(f"SELECT triggered_by_inspection_id i FROM maintenance WHERE dataset_id={d} AND maintenance_id={lit(ident)}") or [{}])[0].get("i")
    elif ident.startswith("REG-"):
        ins = (db.rows(f"SELECT related_inspection_id i FROM reg_events WHERE dataset_id={d} AND event_id={lit(ident)}") or [{}])[0].get("i")
    elif ident.startswith("CTX-"):
        ins = (db.rows(f"SELECT coalesce(inspection_id, inspection_id_in_text) i FROM notes WHERE dataset_id={d} AND note_id={lit(ident)}") or [{}])[0].get("i")
    elif not ins:
        a = db.rows(f"SELECT scope_key FROM assertions WHERE dataset_id={d} AND assertion_id={lit(ident)} AND scope_level='inspection'")
        ins = a[0]["scope_key"] if a else None
    if not ins:
        raise ToolError(f"cannot find an inspection linked to {id!r}")
    rows: list[dict] = []
    sqls = []
    q1 = f"""SELECT e.inspection_id, e.asset_id, e.inspected_at, e.inspector_user_id, e.ambient_eff, e.wellhead_eff, e.wellhead_temp_c,
  round(e.delta_c,1) delta_c, e.delta_min_c, e.producing, e.spill_detected, e.spill_real, e.volume_eff, e.spill_est_volume_bbl, e.notes,
  e.wellhead_overlay, e.volume_overlay, e.detection_correction FROM {mac(deps, 'ins_eff')} e WHERE e.inspection_id = {lit(ins)}"""
    sqls.append(q1)
    for r in db.rows(q1):
        rows.append({"step": 1, "kind": "inspection", "id": r["inspection_id"], "ts": r["inspected_at"],
                     "summary": f"{r['asset_id']} Δ={r['delta_c']} °C (threshold {r['delta_min_c']}), "
                                f"{'producing' if r['producing'] else 'not producing'}; spill_detected={r['spill_detected']}, "
                                f"real={r['spill_real']}, volume={r['volume_eff']} bbl; note: {r['notes']}",
                     "overlay": r["wellhead_overlay"] or r["volume_overlay"] or r["detection_correction"]})
    q2 = f"""SELECT m.maintenance_id, m.asset_id, m.started_at, m.completed_at, m.maintenance_type, m.equipment_kind, m.issue,
  m.cost_total, m.report_file, r.path FROM maintenance m LEFT JOIN reports r ON r.dataset_id = m.dataset_id AND r.maintenance_id = m.maintenance_id
WHERE m.dataset_id = {d} AND m.triggered_by_inspection_id = {lit(ins)} ORDER BY m.started_at"""
    sqls.append(q2)
    cites: list[Citation] = []
    for m in db.rows(q2):
        rows.append({"step": 2, "kind": "maintenance", "id": m["maintenance_id"], "ts": m["started_at"],
                     "summary": f"{m['maintenance_type']} {m['equipment_kind']}: {m['issue']} (${m['cost_total']:,.0f}); "
                                f"report {m['path'] or m['report_file']}"})
        for f in db.rows(f"""SELECT field, value, num, span_start, span_end, extractor FROM report_facts
                             WHERE dataset_id={d} AND maintenance_id={lit(m['maintenance_id'])}
                             AND field NOT LIKE 'compliance:%' ORDER BY span_start"""):
            rows.append({"step": 3, "kind": "report_fact", "id": f"{m['maintenance_id']}#{f['field']}", "ts": None,
                         "summary": f"{f['field']} = {f['value']} ({f['extractor']})",
                         "span": [f["span_start"], f["span_end"]], "report": m["maintenance_id"]})
            cites.append(Citation(kind="report", id=m["maintenance_id"], span=(f["span_start"], f["span_end"]),
                                  label=f["field"]))
        for f in db.rows(f"""SELECT field, value, span_start, span_end FROM report_facts WHERE dataset_id={d}
                             AND maintenance_id={lit(m['maintenance_id'])} AND field LIKE 'compliance:%'"""):
            rows.append({"step": 3, "kind": "report_compliance", "id": f"{m['maintenance_id']}#{f['field']}", "ts": None,
                         "summary": f"{f['field'][11:]}: {f['value']}", "span": [f["span_start"], f["span_end"]],
                         "report": m["maintenance_id"]})
    q3 = f"SELECT * FROM reg_events WHERE dataset_id = {d} AND related_inspection_id = {lit(ins)} ORDER BY date"
    sqls.append(q3)
    for g in db.rows(q3):
        rows.append({"step": 4, "kind": "reg_event", "id": g["event_id"], "ts": g["date"],
                     "summary": f"{g['event_type']} {g['protocol_id']} [{g['status']}] {g['detail']}"
                                + (f" hours_to_report={g['hours_to_report']}" if g["hours_to_report"] is not None else "")
                                + (f" fine=${g['fine_usd']:,.0f}" if g["fine_usd"] else "")})
    q4 = f"""SELECT c.ref, c.kind, c.author_user_id, c.text, c.status, c.source FROM {mac(deps, 'context_for')} c
WHERE c.inspection_ref = {lit(ins)} ORDER BY c.ref"""
    sqls.append(q4)
    names = user_names(db, deps)
    for c in db.rows(q4):
        rows.append({"step": 5, "kind": "note" if c["source"] == "dataset_note" else "assertion", "id": c["ref"], "ts": None,
                     "summary": f"{c['kind']} by {names.get(c['author_user_id'], c['author_user_id'])} [{c['status']}]: {c['text']}"})
    q5 = f"""SELECT assertion_id, kind, target_attr, value_num, original_num, author_user_id, reason, status, tx_time
FROM assertions WHERE dataset_id = {d} AND scope_level = 'inspection' AND scope_key = {lit(ins)} AND source <> 'dataset_note'"""
    sqls.append(q5)
    for a in db.rows(q5):
        rows.append({"step": 5, "kind": "assertion", "id": a["assertion_id"], "ts": a["tx_time"],
                     "summary": f"{a['kind']} {a['target_attr']}: {a['original_num']} -> {a['value_num']} by "
                                f"{names.get(a['author_user_id'], a['author_user_id'])} [{a['status']}] {a['reason'] or ''}"})
    cols = ["step", "kind", "id", "ts", "summary", "span", "report", "overlay"]
    rows = [{c: r.get(c) for c in cols} for r in rows]
    confl = cf.scan_inspections(db, deps, [ins])
    return finish(db, deps, "entity_list", cols, rows, sql=None, notes=[
        f"chain rooted at {ins} (from {id}); steps: 1 inspection, 2 maintenance, 3 report facts with char spans, 4 regulatory, 5 notes/assertions",
        "queries: " + " | ".join(re.sub(r"\s+", " ", s) for s in sqls)], citations=cites, conflicts=confl)
