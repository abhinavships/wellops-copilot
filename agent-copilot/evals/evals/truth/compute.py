"""Ground truth computed from the substrate (no LLM). Each function returns a Truth dict:

  ids:        list of ids that must be named (exact)
  set:        list[str] the answer's primary result set
  numbers:    {label: float}
  dates:      list[ISO date]
  cites:      list[str] ids that are legitimate evidence
  text:       list[str] names/phrases that should appear (people etc.)
  rows:       list[dict] (for rendering fake answers)
  visual:     default visual type per the doc's choice table
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable

from evals.truth.db import DS, asof, get_con, overlays, q

REGISTRY: dict[str, Callable[..., dict]] = {}


def truth_fn(f):
    REGISTRY[f.__name__] = f
    return f


def _d(x) -> date:
    return x if isinstance(x, date) and not isinstance(x, datetime) else date.fromisoformat(str(x)[:10])


def area_id(con, name: str) -> str:
    r = q(con, "SELECT area_id FROM areas WHERE dataset_id=? AND (name=? OR area_id=?)", DS, name, name)
    if not r:
        raise KeyError(f"area {name}")
    return r[0][0]


def user_name(con, uid: str) -> str:
    return q(con, "SELECT name FROM users WHERE dataset_id=? AND user_id=?", DS, uid)[0][0]


def _T(**kw) -> dict:
    base = dict(ids=[], set=[], numbers={}, dates=[], cites=[], text=[], rows=[], visual=None, notes=[])
    base.update(kw)
    return base


def _clock(args) -> str:
    return asof(args.get("clock", "2026-10-17"))


@truth_fn
def well_status(well: str, today="2026-10-17", clock=None, **_):
    con = get_con()
    r = q(con, """SELECT s.inspection_id, s.inspected_at::DATE, s.inspector_user_id, s.method, s.producing,
                         s.delta_c, s.recorded_status, s.recorded_status_stale, s.wellhead_eff, s.ambient_eff
                  FROM latest_status(?, ?::TIMESTAMPTZ) s WHERE asset_id=?""", DS, asof(clock or today), well)[0]
    notes = q(con, """SELECT ref FROM context_for(?, ?::TIMESTAMPTZ) WHERE asset_id=? AND valid_from <= ?
                      AND coalesce(valid_to, DATE '9999-01-01') >= ? AND kind IN ('operator_feedback','operator_note')
                      ORDER BY valid_from DESC""", DS, asof(clock or today), well, r[1], r[1])
    return _T(ids=[r[0]], set=[well], numbers={"delta_c": round(r[5], 1)}, dates=[str(r[1])],
              cites=[r[0], well] + [n[0] for n in notes], text=[user_name(con, r[2])],
              rows=[dict(inspection_id=r[0], producing=r[4], delta=round(r[5], 1), method=r[3],
                         recorded_status=r[6], stale=r[7], inspected=str(r[1]))],
              visual="status_card", notes=[f"producing={r[4]}"])


@truth_fn
def area_not_producing(area: str, today="2026-10-17", **_):
    con = get_con()
    a = area_id(con, area)
    rows = q(con, """SELECT asset_id, inspection_id, delta_c, recorded_status, recorded_status_stale
                     FROM latest_status(?, ?::TIMESTAMPTZ) WHERE area_id=? AND NOT producing ORDER BY 1""",
             DS, asof(today), a)
    reps = q(con, """SELECT asset_id, maintenance_id FROM repairs(?, ?::TIMESTAMPTZ) WHERE open_as_of""", DS, asof(today))
    openr = {}
    for w, m in reps:
        openr.setdefault(w, []).append(m)
    ids = [r[0] for r in rows]
    nums = {f"delta:{r[0]}": round(r[2], 1) for r in rows}
    return _T(ids=ids, set=ids, numbers=nums, cites=[r[1] for r in rows] + [m for w in ids for m in openr.get(w, [])],
              rows=[dict(well=r[0], delta=round(r[2], 1), recorded=r[3], stale=r[4], open_repairs=openr.get(r[0], []))
                    for r in rows], visual="table")


@truth_fn
def series(well: str, weeks: int = 13, today="2026-10-16", **_):
    con = get_con()
    t = _d(today)
    rows = q(con, """SELECT inspection_id, inspected_at::DATE, wellhead_eff, ambient_eff, producing FROM ins_eff(?, ?::TIMESTAMPTZ)
                     WHERE asset_id=? AND inspected_at::DATE > ? AND inspected_at::DATE <= ? ORDER BY 2""",
             DS, asof(t), well, t - timedelta(weeks=weeks), t)
    np_ = [str(r[1]) for r in rows if not r[4]]
    return _T(numbers={"readings": len(rows)}, dates=np_, cites=[r[0] for r in rows[-3:]] + [well],
              rows=[dict(date=str(r[1]), wellhead=r[2], ambient=r[3], producing=r[4]) for r in rows], visual="line",
              set=[well])


@truth_fn
def spills_well(well: str, years: int = 4, today="2026-10-16", **_):
    con = get_con()
    t = _d(today)
    rows = q(con, """SELECT inspection_id, inspected_at::DATE, volume_eff FROM spills_real(?, ?::TIMESTAMPTZ)
                     WHERE asset_id=? AND inspected_at::DATE > ? AND inspected_at::DATE <= ? ORDER BY 2""",
             DS, asof(t), well, t.replace(year=t.year - years), t)
    return _T(numbers={"count": len(rows), "total_bbl": round(sum(r[2] for r in rows), 1)},
              dates=[str(r[1]) for r in rows], cites=[r[0] for r in rows], ids=[r[0] for r in rows],
              rows=[dict(inspection_id=r[0], date=str(r[1]), bbl=r[2]) for r in rows], visual="table", set=[well])


@truth_fn
def downtime_days(well: str, year: int, **_):
    con = get_con()
    rows = q(con, """SELECT inspection_id, inspected_at::DATE FROM ins_eff(?, ?::TIMESTAMPTZ) WHERE asset_id=?
                     AND year(inspected_at)=? AND NOT producing ORDER BY 2""", DS, asof(f"{year}-12-31"), well, year)
    return _T(numbers={"days": 7 * len(rows)}, dates=[str(r[1]) for r in rows], cites=[r[0] for r in rows],
              visual="none", set=[well])


@truth_fn
def top_downtime(area: str, k: int, since: str, until: str = "2026-10-16", **_):
    con = get_con()
    a = area_id(con, area)
    rows = q(con, """SELECT asset_id, 7*count(*) d FROM ins_eff(?, ?::TIMESTAMPTZ) WHERE area_id=? AND NOT producing
                     AND inspected_at::DATE >= ? AND inspected_at::DATE <= ? GROUP BY 1 ORDER BY d DESC, asset_id""",
             DS, asof(until), a, _d(since), _d(until))
    top = rows[:k]
    return _T(ids=[r[0] for r in top], set=[r[0] for r in top],
              numbers={f"days:{r[0]}": r[1] for r in top} | ({"next_days": rows[k][1]} if len(rows) > k else {}),
              cites=[r[0] for r in top], rows=[dict(well=r[0], days=r[1]) for r in top], visual="bar")


@truth_fn
def spills_map(area: str, days: int = 90, today="2026-10-16", **_):
    con = get_con()
    t = _d(today)
    rows = q(con, """SELECT asset_id, inspected_at::DATE, volume_eff, lat, lon, inspection_id FROM spills_real(?, ?::TIMESTAMPTZ)
                     WHERE area_id=? AND inspected_at::DATE > ? AND inspected_at::DATE <= ? ORDER BY 1""",
             DS, asof(t), area_id(con, area), t - timedelta(days=days), t)
    nums = {}
    for r in rows:
        nums[f"bbl:{r[0]}"] = round(r[2], 1)
    return _T(ids=[r[0] for r in rows], set=[r[0] for r in rows], numbers=nums, dates=sorted({str(r[1]) for r in rows}),
              cites=[r[5] for r in rows], rows=[dict(well=r[0], date=str(r[1]), bbl=r[2], lat=r[3], lon=r[4]) for r in rows],
              visual="map")


@truth_fn
def spills_heatmap(year: int, **_):
    con = get_con()
    rows = q(con, """SELECT a.name, month(s.inspected_at), count(*) FROM spills_real(?, ?::TIMESTAMPTZ) s
                     JOIN areas a ON a.dataset_id=? AND a.area_id=s.area_id WHERE year(s.inspected_at)=?
                     GROUP BY 1,2 ORDER BY 1,2""", DS, asof(f"{year}-12-31"), DS, year)
    nums, tot = {}, {}
    for a, m, c in rows:
        nums[f"{a}|{m}"] = c
        tot[a] = tot.get(a, 0) + c
    nums |= {f"total|{a}": v for a, v in tot.items()}
    return _T(numbers=nums, rows=[dict(area=a, month=m, n=c) for a, m, c in rows], visual="heatmap",
              text=sorted(tot))


@truth_fn
def rules_broken(well: str, months: int = 12, today="2026-10-16", **_):
    """SR-01/SR-02/SR-03/RC-04 for real spills of a well in the window + RCA count + RC-02 test date."""
    con = get_con()
    t = _d(today)
    start = t - timedelta(days=round(months * 30.4375))
    sp = q(con, """SELECT inspection_id, inspected_at, volume_eff FROM spills_real(?, ?::TIMESTAMPTZ)
                   WHERE asset_id=? AND inspected_at::DATE > ? AND volume_eff >= 5""", DS, asof(t), well, start)
    nums, dates, cites = {}, [], []
    for ins, at, vol in sp:
        ev = q(con, """SELECT event_id, event_type, hours_to_report, fine_usd, date, status FROM reg_events
                       WHERE asset_id=? AND related_inspection_id=?""", DS if False else well, ins)
        for e in ev:
            cites.append(e[0])
            if e[1] == "spill_report_filed" and e[2] is not None and e[2] > 24:
                nums["sr01_hours"] = e[2]
            if e[1] == "notice_of_violation":
                nums["fine_usd"] = e[3]
                dates.append(str(e[4] + timedelta(days=30)))  # RC-04 response due
        cf = q(con, """SELECT f.num, m.maintenance_id FROM report_facts f JOIN maintenance m USING (dataset_id, maintenance_id)
                       WHERE f.dataset_id=? AND f.field='containment_hours' AND m.triggered_by_inspection_id=?""", DS, ins)
        for h, mid in cf:
            if h > 4:
                nums["sr02_hours"] = h
                cites.append(mid)
        dates.append(str((at + timedelta(days=60)).date()))  # SR-03 due
    rca = q(con, """SELECT maintenance_id FROM maintenance WHERE dataset_id=? AND asset_id=? AND maintenance_type='corrective'
                    AND equipment_kind='flowline' AND started_at::DATE > ? AND started_at::DATE <= ?""", DS, well, start, t)
    nums["rca_flowline_corrective"] = len(rca)
    cites += [r[0] for r in rca]
    rc02 = q(con, """SELECT maintenance_id, started_at::DATE FROM maintenance WHERE dataset_id=? AND asset_id=?
                     AND issue ILIKE '%flowline integrity test%' AND started_at::DATE > ? ORDER BY 2 DESC""", DS, well, start)
    if rc02:
        dates.append(str(rc02[0][1]))
        cites.append(rc02[0][0])
    return _T(numbers=nums, dates=sorted(set(dates)), cites=sorted(set(cites)), ids=[r[0] for r in rca],
              visual="table", set=[well])


@truth_fn
def containment_breaches(area: str, days: int = 90, today="2026-10-16", **_):
    con = get_con()
    t = _d(today)
    rows = q(con, """SELECT m.asset_id, f.num, m.maintenance_id, i.inspection_id FROM report_facts f
                     JOIN maintenance m USING (dataset_id, maintenance_id)
                     JOIN inspections i ON i.dataset_id=m.dataset_id AND i.inspection_id=m.triggered_by_inspection_id
                     JOIN wells w ON w.dataset_id=m.dataset_id AND w.asset_id=m.asset_id
                     WHERE f.dataset_id=? AND f.field='containment_hours' AND f.num > 4 AND w.area_id=?
                       AND i.inspected_at::DATE > ? AND i.inspected_at::DATE <= ? ORDER BY 1""",
             DS, area_id(con, area), t - timedelta(days=days), t)
    return _T(ids=[r[0] for r in rows], set=[r[0] for r in rows], numbers={f"hours:{r[0]}": r[1] for r in rows},
              cites=[x for r in rows for x in (r[2], r[3])], rows=[dict(well=r[0], hours=r[1], report=r[2], ins=r[3])
              for r in rows], visual="table")


@truth_fn
def repair_costs(wells: list, year: int, **_):
    con = get_con()
    rows = q(con, """SELECT asset_id, round(sum(cost_total)) FROM maintenance WHERE dataset_id=? AND asset_id IN (SELECT unnest(?))
                     AND year(started_at)=? GROUP BY 1""", DS, list(wells), year)
    d = dict(rows)
    return _T(ids=list(wells), set=list(wells), numbers={f"usd:{w}": d.get(w, 0) for w in wells},
              rows=[dict(well=w, usd=d.get(w, 0)) for w in wells], visual="table", cites=list(wells))


@truth_fn
def rca_candidates(area: str, equipment: str = "pump_jack", today="2026-10-17", **_):
    con = get_con()
    a = area_id(con, area)
    rows = q(con, """SELECT m.asset_id, count(*) FROM repairs(?, ?::TIMESTAMPTZ) m
             JOIN wells w USING (dataset_id, asset_id)
             JOIN params(?, ?::TIMESTAMPTZ) p ON p.asset_id=m.asset_id AND p.param='rca_corrective_count_min'
             WHERE w.area_id=? AND m.maintenance_type='corrective' AND m.equipment_kind=?
               AND m.started_at > (?::DATE - INTERVAL 12 MONTH)
             GROUP BY m.asset_id, p.value HAVING count(*) >= p.value ORDER BY 1""",
             DS, asof(today), DS, asof(today), a, equipment, _d(today))
    ids = [r[0] for r in rows]
    return _T(ids=ids, set=ids, numbers={f"repairs:{r[0]}": r[1] for r in rows}, cites=ids, visual="table",
          rows=[dict(well=r[0], n=r[1]) for r in rows])


@truth_fn
def spill_reassessment(inspection: str, today="2026-10-17", **_):
    con = get_con()
    r = q(con, """SELECT asset_id, inspected_at, volume_eff, spill_class FROM spills_real(?, ?::TIMESTAMPTZ)
                  WHERE inspection_id=?""", DS, asof(today), inspection)[0]
    mnt = q(con, """SELECT m.maintenance_id, f.num FROM maintenance m LEFT JOIN report_facts f ON f.dataset_id=m.dataset_id
                    AND f.maintenance_id=m.maintenance_id AND f.field='containment_hours'
                    WHERE m.dataset_id=? AND m.triggered_by_inspection_id=?""", DS, inspection)
    notice = q(con, """SELECT count(*) FROM reg_events WHERE dataset_id=? AND related_inspection_id=?
                       AND event_type='spill_report_filed'""", DS, inspection)[0][0]
    at = r[1]
    return _T(ids=[inspection], set=[r[0]], numbers={"volume_bbl": r[2], "notices_on_record": notice,
          **({"containment_hours": mnt[0][1]} if mnt and mnt[0][1] else {})},
          dates=[str((at + timedelta(days=60)).date())], cites=[inspection] + [m[0] for m in mnt],
          text=[r[3]], visual="correction_diff", notes=[f"class={r[3]}"])


@truth_fn
def reportable_spills(area: str, days: int = 90, today="2026-10-17", **_):
    con = get_con()
    t = _d(today)
    rows = q(con, """SELECT asset_id, inspection_id, volume_eff FROM spills_real(?, ?::TIMESTAMPTZ)
                     WHERE area_id=? AND inspected_at::DATE > ? AND volume_eff >= 5 ORDER BY 1""",
             DS, asof(t), area_id(con, area), t - timedelta(days=days))
    out = []
    for w, ins, v in rows:
        n = q(con, "SELECT count(*) FROM reg_events WHERE dataset_id=? AND related_inspection_id=? "
                   "AND event_type='spill_report_filed'", DS, ins)[0][0]
        out.append((w, ins, v, n))
    return _T(ids=[r[0] for r in out], set=[r[0] for r in out], numbers={f"bbl:{r[0]}": r[2] for r in out},
          cites=[r[1] for r in out], visual="table", rows=[dict(well=r[0], ins=r[1], bbl=r[2], notices=r[3]) for r in out],
          notes=["overdue:" + ",".join(r[0] for r in out if r[3] == 0)])


@truth_fn
def spill_detection_state(inspection: str, today="2026-10-17", **_):
    con = get_con()
    r = q(con, """SELECT asset_id, volume_eff, spill_real, detection_correction, notes FROM ins_eff(?, ?::TIMESTAMPTZ)
                  WHERE inspection_id=?""", DS, asof(today), inspection)[0]
    notes = q(con, "SELECT note_id, author_user_id, note_type FROM notes WHERE dataset_id=? AND asset_id=? AND "
                   "(inspection_id=? OR inspection_id_in_text=?)", DS, r[0], inspection, inspection)
    mnt = q(con, "SELECT maintenance_id FROM maintenance WHERE dataset_id=? AND triggered_by_inspection_id=?", DS, inspection)
    return _T(ids=[inspection], set=[r[0]], numbers={"volume_bbl": r[1]},
              cites=[inspection] + [n[0] for n in notes] + [m[0] for m in mnt],
              text=[user_name(con, n[1]) for n in notes], visual="conflict_card",
              rows=[dict(note=n[0], author=n[1], type=n[2]) for n in notes])


def compute(spec: dict | None) -> dict | None:
    if not spec:
        return None
    args = dict(spec.get("args") or {})
    con = get_con()
    with overlays(con, spec.get("overlays") or []):
        return REGISTRY[spec["fn"]](**args)
