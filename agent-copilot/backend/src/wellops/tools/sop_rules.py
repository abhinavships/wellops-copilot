"""SOP rule checks over the raw dataset files (operator notes, MOC memos, recall notices, type/team history).

These rules (Operations SOP OPS-1/2/3/5, supplier recall notices) need fields the core ingest does not keep
(type_history, team_history, data_correction values, "Parts installed" lots), so they read the dataset folder
directly. Parameters (deadlines, thresholds, lots, memo wells and expiry) are parsed from the documents themselves.
"""

from __future__ import annotations

import glob
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache

from wellops.contracts import RunDeps
from wellops.settings import settings

from .core import finish, get_db

UTC = timezone.utc


def _p(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _root() -> str:
    return str(settings.data_dir)


def _jsonl(path: str) -> list[dict]:
    try:
        return [json.loads(x) for x in open(path) if x.strip()]
    except FileNotFoundError:
        return []


@lru_cache(maxsize=2)
def _fleet(root: str) -> dict:
    """Wells with area, effective type/team history, notes, inspections, jobs, reports; read once per dataset."""
    areas = {a["area_id"]: a for a in json.load(open(f"{root}/fleet/areas.json"))["areas"]}
    wells = {}
    for d in sorted(glob.glob(f"{root}/assets/*")):
        w = os.path.basename(d)
        try:
            meta = json.load(open(f"{d}/metadata.json"))
        except FileNotFoundError:
            continue
        reports = {}
        for f in glob.glob(f"{d}/maintenance-reports/*.md"):
            t = open(f).read()
            m = re.search(r"(MNT-\d{4}-\d+(?:-L\d+)?)", t)
            reports[m.group(1) if m else os.path.basename(f)] = t
        wells[w] = {"meta": meta, "area": areas.get(meta.get("area_id"), {}).get("name", meta.get("area_id")),
                    "area_team": areas.get(meta.get("area_id"), {}).get("team_id"),
                    "notes": _jsonl(f"{d}/context.jsonl"), "ins": _jsonl(f"{d}/inspections.jsonl"),
                    "jobs": _jsonl(f"{d}/maintenance.jsonl"), "reports": reports}
    return {"areas": areas, "wells": wells}


def fleet() -> dict:
    return _fleet(_root())


def _sop(name: str) -> str:
    try:
        return open(f"{_root()}/fleet/sops/{name}").read()
    except FileNotFoundError:
        return ""


def type_at(w: dict, day: str) -> str:
    t = w["meta"].get("well_type")
    for h in w["meta"].get("type_history") or []:
        if h.get("from", "") <= day and (not h.get("to") or day <= h["to"]):
            t = h["well_type"]
    return t


def team_now(w: dict) -> str | None:
    cur = [h for h in w["meta"].get("team_history") or [] if not h.get("to")]
    return cur[-1]["team_id"] if cur else (w["meta"].get("team_id") or w["area_team"])


def _thresholds() -> dict[str, float]:
    out = {}
    for f in glob.glob(f"{_root()}/fleet/well-types/*.md"):
        t = open(f).read()
        m = re.search(r"at least \*\*(\d+(?:\.\d+)?) °C above", t)
        if m:
            out[os.path.basename(f)[:-3]] = float(m.group(1))
    return out


def _corrected(w: dict, ins: dict, field: str):
    for n in w["notes"]:
        if n.get("note_type") == "data_correction" and n.get("inspection_id") == ins["inspection_id"] and n.get("field") == field:
            return n.get("corrected_value")
    return ins.get(field)


def producing_reading(w: dict, ins: dict, th: dict) -> tuple[bool | None, float | None, float, str]:
    wh, am = _corrected(w, ins, "wellhead_temp_c"), _corrected(w, ins, "ambient_temp_c")
    t = type_at(w, ins["inspected_at"][:10])
    need = th.get(t, 15.0)
    if wh is None or am is None:
        return None, None, need, t
    return (wh - am) >= need, round(wh - am, 1), need, t


def _until(deps: RunDeps) -> datetime:
    return datetime.combine(deps.today, datetime.max.time(), tzinfo=UTC)


def _done(deps: RunDeps, shape: str, rows: list[dict], notes: list[str]):
    cols = list(rows[0].keys()) if rows else ["result"]
    return finish(get_db(), deps, shape, cols, rows, sql=None, notes=notes)


# ------------------------------------------------------------------------------------------------ OPS-1 gas
GAS = re.compile(r"smell(?:ed)? gas|gas smell|rotten[- ]egg|gas odou?r|h2s (?:monitor )?alarm|monitor alarmed", re.I)


def gas_response(deps: RunDeps, since: str = "2026-11-01"):
    """OPS-1: every operator-note gas report since `since` and whether a field response (maintenance job start or
    manual inspection, drone excluded) began within the deadline (24 h before 1 Jan 2027, 12 h from then; parsed)."""
    sop = _sop("operations-sop-rev2.md")
    hrs = [int(x) for x in re.findall(r"\*\*(\d+) hours\*\* for reports", sop)] or [24, 12]
    cut = re.search(r"reports noted from (\d+ \w+ \d{4})", sop)
    cutover = datetime.strptime(cut.group(1), "%d %b %Y").replace(tzinfo=UTC) if cut else datetime(2027, 1, 1, tzinfo=UTC)
    F, rows, start, end = fleet(), [], _p(since + "T00:00:00Z"), _until(deps)
    for wid, w in F["wells"].items():
        for n in w["notes"]:
            if n.get("note_type") != "operator_note" or not GAS.search(n.get("text", "")):
                continue
            t = _p(n["created_at"])
            if not (start <= t <= end):
                continue
            named = re.findall(r"WELL-\d{4}", n["text"])
            target = named[0] if named else wid
            tw = F["wells"].get(target, w)
            dl = hrs[0] if t < cutover else hrs[-1]
            cand = [(_p(j["started_at"]), j["maintenance_id"]) for j in tw["jobs"] if j.get("started_at") and t < _p(j["started_at"]) <= end]
            cand += [(_p(i["inspected_at"]), i["inspection_id"]) for i in tw["ins"] if i.get("method") == "manual" and t < _p(i["inspected_at"]) <= end]
            first = min(cand) if cand else None
            h = round((first[0] - t).total_seconds() / 3600, 1) if first else None
            rows.append({"asset_id": target, "area": tw["area"], "reported_at": t.strftime("%Y-%m-%d %H:%M UTC"),
                         "note": n["text"], "response": f"{first[1]} at {first[0]:%Y-%m-%d %H:%M}" if first else "none",
                         "hours_to_response": h, "deadline_h": dl,
                         "result": "Met" if first and h <= dl else "Missed"})
    rows.sort(key=lambda r: r["reported_at"])
    missed = sum(r["result"] == "Missed" for r in rows)
    return _done(deps, "entity_list", rows, [f"OPS-1 (Operations SOP rev 2): response = maintenance start or MANUAL inspection "
                                       f"(drone excluded); deadline {hrs[0]} h before {cutover:%d %b %Y}, {hrs[-1]} h from then",
                                       f"{len(rows)} reports, {missed} missed"])


# ------------------------------------------------------------------------------------------------ OPS-2 bypass
def _mocs() -> dict[str, tuple[str, date]]:
    """well -> (memo id, expiry); an extension memo replaces the expiry of the memo it extends."""
    memos = {}
    for f in sorted(glob.glob(f"{_root()}/fleet/sops/moc-*.md")):
        t = open(f).read()
        mid = re.search(r"MOC-\d{4}-\d+", t).group(0)
        exp = re.search(r"expires on (\d+ \w+ \d{4})", t)
        ext = re.search(r"extends (MOC-\d{4}-\d+)", t)
        line = re.search(r"(?:authorized on these wells|named in MOC-\d{4}-\d+):?\s*\n?(.*)", t)
        wells = re.findall(r"WELL-\d{4}", line.group(1) if line else t)
        memos[mid] = {"exp": datetime.strptime(exp.group(1), "%d %b %Y").date() if exp else None, "ext": ext.group(1) if ext else None, "wells": wells}
    out = {}
    for mid, m in memos.items():
        for w in m["wells"]:
            if w not in out or (m["exp"] and out[w][1] and m["exp"] > out[w][1]):
                out[w] = (mid + (f" (extends {m['ext']})" if m["ext"] else ""), m["exp"])
    return out


BY_OUT = re.compile(r"removed|taken out|took the bypass out|pulled|restored|back to normal", re.I)


def bypasses(deps: RunDeps):
    """OPS-2: every temporary flowline bypass (in/out from operator notes) vs the MOC memos: Allowed / Expired / Not approved."""
    F, moc, rows = fleet(), _mocs(), []
    for wid, w in F["wells"].items():
        ev = sorted((n["created_at"], n["text"]) for n in w["notes"]
                    if n.get("note_type") == "operator_note" and re.search(r"bypass", n.get("text", ""), re.I)
                    and n["created_at"][:10] <= str(deps.today))
        cur = None
        for at, text in ev + [(None, None)]:
            if at is not None and not BY_OUT.search(text):
                cur = cur or at
                continue
            if cur is None:
                continue
            mid, exp = moc.get(wid, (None, None))
            out_day = date.fromisoformat(at[:10]) if at else None
            last = out_day or deps.today
            status = "Not approved" if not mid else ("Expired" if exp and last > exp else "Allowed")
            rows.append({"asset_id": wid, "area": w["area"], "bypass_in": cur[:10], "bypass_out": at[:10] if at else "still in",
                         "approval": mid or "none", "valid_until": str(exp) if exp else "", "status": status,
                         "in_place_now": at is None})
            cur = None
    rows.sort(key=lambda r: (not r["in_place_now"], r["asset_id"]))
    return _done(deps, "entity_list", rows, ["OPS-2: allowed only on a well named in an unexpired MOC (an extension memo replaces "
                                       "the expiry); in/out from operator notes; violation = no MOC, or still in after expiry"])


# ------------------------------------------------------------------------------------------------ OPS-3 status
SHUT = re.compile(r"\bshut[- ]in\b", re.I)
RESTART = re.compile(r"restart|back online|put back on production", re.I)


def status_conflicts(deps: RunDeps):
    """OPS-3: wells whose latest inspection and latest shut-in/restart operator note disagree, with the status that
    wins (most recent; a newer restart note = producing (unconfirmed))."""
    F, th, rows, end = fleet(), _thresholds(), [], str(deps.today) + "T23:59:59Z"
    for wid, w in F["wells"].items():
        if w["meta"].get("status") == "plugged_and_abandoned":
            continue
        ins = [i for i in w["ins"] if i["inspected_at"] <= end]
        ops = [n for n in w["notes"] if n.get("note_type") == "operator_note" and wid in n.get("text", "")
               and (SHUT.search(n["text"]) or RESTART.search(n["text"])) and n["created_at"] <= end]
        if not ins or not ops:
            continue
        li, ln = max(ins, key=lambda i: i["inspected_at"]), max(ops, key=lambda n: n["created_at"])
        prod, delta, need, t = producing_reading(w, li, th)
        note_prod = not SHUT.search(ln["text"])
        if prod is None or note_prod == prod:
            continue
        newer = ln["created_at"] > li["inspected_at"]
        now = ("Not producing oil (shut in)" if not note_prod else "Producing oil (unconfirmed)") if newer \
            else ("Producing oil" if prod else "Not producing oil")
        why = (f"operator note ({ln['created_at'][:16]}) is newer than the inspection" if newer
               else f"inspection ({li['inspected_at'][:16]}) is newer than the note")
        rows.append({"asset_id": wid, "area": w["area"], "inspection_id": li["inspection_id"], "inspected_at": li["inspected_at"][:16],
                     "inspection_shows": f"{'producing' if prod else 'not producing'} (Δ {delta} °C vs {need:g} °C, {t})",
                     "note_at": ln["created_at"][:16], "note": ln["text"], "status_now": now, "why": why + " (OPS-3)"})
    rows.sort(key=lambda r: r["asset_id"])
    return _done(deps, "entity_list", rows, ["OPS-3: the most recent of latest inspection (by the well-type threshold, after "
                                       "data_correction notes) and latest shut-in/restart note wins"])


# ------------------------------------------------------------------------------------------------ recall
def recalled_pumps(deps: RunDeps, team: str | None = None):
    """Supplier recall notices: wells whose CURRENT pump (latest 'Parts installed' line) is from a recalled lot, with the
    install job and cost, plus any user statements about the well (who said it; unconfirmed without a work order)."""
    notice = _sop("supplier-recall-notice-2026-12-01.md")
    model = (re.search(r"Highline (RP-\d+)", notice) or [None, "RP-175"])[1]
    sect = notice.split("Pumps from all other lots")[0]
    lots = set(re.findall(r"LOT-\d{4}-[A-Z]", sect))
    F, rows, end = fleet(), [], str(deps.today) + "T23:59:59Z"
    db = get_db()
    said = db.rows("""SELECT a.scope_key, a.value_text, a.reason, a.author_user_id, u.name, a.tx_time::VARCHAR t, a.status
                      FROM assertions a LEFT JOIN users u ON u.user_id=a.author_user_id AND u.dataset_id=a.dataset_id
                      WHERE a.dataset_id=? AND a.status NOT IN ('undone','superseded')""", [deps.active_dataset])
    for wid, w in F["wells"].items():
        jobs = {j["maintenance_id"]: j for j in w["jobs"]}
        inst = []
        for mid, txt in w["reports"].items():
            m = re.search(r"\*\*Parts installed:\*\*(.*)", txt)
            j = jobs.get(mid, {})
            if m and j.get("started_at") and j["started_at"] <= end:
                inst.append((j["started_at"], mid, m.group(1).strip(), j))
        if not inst:
            continue
        at, mid, part, j = max(inst)
        lot = re.search(r"LOT-\d{4}-[A-Z]", part)
        if not (lot and lot.group(0) in lots and model in part):
            continue
        tm = team_now(w)
        if team and team not in (tm, w["area"]):
            continue
        cost = j.get("cost_usd") or {}
        mine = [x for x in said if x["scope_key"] == wid]
        HEAR = re.compile(r"told me|said by|according to|heard|says? (?:that|it)", re.I)
        SWAP = re.compile(r"swap|replac|pulled|changed out", re.I)
        firsthand = [x for x in mine if SWAP.search(f"{x['value_text']} {x['reason']}") and not HEAR.search(f"{x['value_text']} {x['reason']}")]
        notes = [f"{x['name'] or x['author_user_id']} ({x['t'][:10]}): {x['value_text'] or x['reason']}"
                 + (" [second-hand, not applied]" if HEAR.search(f"{x['value_text']} {x['reason']}") else " [unconfirmed: no work order]")
                 for x in mine]
        in_ground = "No: replaced per " + (firsthand[-1]["name"] or firsthand[-1]["author_user_id"]) + " (unconfirmed, no work order yet)" \
            if firsthand else "Yes"
        rows.append({"asset_id": wid, "area": w["area"], "team_id": tm, "in_ground": in_ground, "install_job": mid, "installed_on": at[:10],
                     "pump": part, "install_cost_usd": cost.get("total") if isinstance(cost, dict) else cost,
                     "user_statements": "; ".join(notes) or "", "source": f"maintenance report {mid} (Parts installed)"})
    rows.sort(key=lambda r: (r["area"], r["asset_id"]))
    still = sum(r["in_ground"] == "Yes" for r in rows)
    return _done(deps, "entity_list", rows, [f"{still} recalled pumps still in the ground per records and first-hand reports "
                                             f"({len(rows) - still} marked replaced by the person who reported it, no work order yet)",
                                             f"recall: Highline {model} lots {sorted(lots)} (notice HPC-RN-2026-07); only the pump in "
                                       "the well today counts (latest Parts installed line)",
                                       "a first-hand statement ('swapped yesterday') counts as replaced, attributed and unconfirmed until the "
                                             "work order arrives; a second-hand one ('X told me') is shown but not applied"])


# ------------------------------------------------------------------------------------------------ OPS-5 cold
def freeze_checks(deps: RunDeps, start: str = "2026-12-01", end: str = "2026-12-31"):
    """OPS-5: inspections at or below the SOP cold limit on a producing well that had no 'Heat trace check' job within 7 days."""
    sop = _sop("operations-sop-rev2.md")
    lim = float((re.search(r"at or below \*\*(−?-?\d+) °C", sop) or [None, "-5"])[1].replace("−", "-"))
    days = int((re.search(r"within \*\*(\d+) days\*\* after the inspection", sop) or [None, "7"])[1])
    F, th, rows = fleet(), _thresholds(), []
    for wid, w in F["wells"].items():
        heat = [_p(j["started_at"]) for j in w["jobs"] if "heat trace check" in (j.get("issue") or "").lower() and j.get("started_at")]
        for i in w["ins"]:
            if not (start <= i["inspected_at"][:10] <= end) or i.get("ambient_temp_c") is None:
                continue
            am = _corrected(w, i, "ambient_temp_c")
            if am is None or am > lim:
                continue
            prod, delta, need, t = producing_reading(w, i, th)
            if not prod:
                continue
            t0 = _p(i["inspected_at"])
            ok = [h for h in heat if t0 < h <= t0 + timedelta(days=days)]
            rows.append({"asset_id": wid, "area": w["area"], "inspection_id": i["inspection_id"], "inspected_at": i["inspected_at"][:16],
                         "ambient_c": am, "check": "met" if ok else "MISSED", "heat_trace_job_start": str(min(ok))[:16] if ok else ""})
    rows.sort(key=lambda r: (r["check"] != "MISSED", r["inspected_at"]))
    return _done(deps, "entity_list", rows, [f"OPS-5: ambient ≤ {lim} °C on a producing reading needs a 'Heat trace check' job within {days} days"])


# ------------------------------------------------------------------------------------------------ spill clean-up
NOT_CRUDE = re.compile(r"not (?:a )?crude|no crude|not a spill", re.I)


def spill_cleanup_compliance(deps: RunDeps, areas: list[str] | None = None, since: str = "2026-06-01", min_bbl: float = 5.0,
                             required: str = "Soil samples sent to the lab."):
    """Reportable spills (volume >= min_bbl after data_correction notes; detections later confirmed not crude excluded)
    in the given areas since a date, their clean-up job (triggered by the inspection, or whose report cites it) and
    whether that report contains the required sentence."""
    F, rows, end = fleet(), [], str(deps.today) + "T23:59:59Z"
    want = {a.lower() for a in areas or []}
    for wid, w in F["wells"].items():
        if want and w["area"].lower() not in want and (w["meta"].get("area_id") or "").lower() not in want:
            continue
        for i in w["ins"]:
            if not i.get("spill_detected") or not (since <= i["inspected_at"][:10] and i["inspected_at"] <= end):
                continue
            if any(n.get("inspection_id") == i["inspection_id"] and n.get("note_type") == "correction" and NOT_CRUDE.search(n.get("text", ""))
                   for n in w["notes"]):
                continue
            vol = _corrected(w, i, "spill_est_volume_bbl")
            if vol is None or vol < min_bbl:
                continue
            jobs = [j for j in w["jobs"] if j.get("triggered_by_inspection_id") == i["inspection_id"]] or \
                   [j for j in w["jobs"] if i["inspection_id"] in w["reports"].get(j["maintenance_id"], "")]
            j = min(jobs, key=lambda x: x.get("started_at") or "") if jobs else None
            rep = w["reports"].get(j["maintenance_id"], "") if j else ""
            ok = bool(j) and required.lower() in rep.lower()
            why = ("clean-up report has the lab-sample sentence" if ok else
                   "no clean-up job found" if not j else
                   "field readings only, no lab samples" if re.search(r"soil", rep, re.I) else "no soil samples recorded")
            rows.append({"asset_id": wid, "area": w["area"], "inspection_id": i["inspection_id"], "inspected_at": i["inspected_at"][:10],
                         "volume_bbl": vol, "cleanup_job": j["maintenance_id"] if j else "none",
                         "compliant": "Yes" if ok else "No", "reason": why})
    rows.sort(key=lambda r: (r["compliant"], r["area"], r["asset_id"]))
    return _done(deps, "entity_list", rows, [f"reportable = ≥ {min_bbl:g} bbl after corrections, not-crude detections excluded; "
                                             f"compliant only if the clean-up report contains \"{required}\""])


# ------------------------------------------------------------------------------------------------ shut-in waits
WAIT = [("parts", re.compile(r"part|back-?order|supplier|vendor", re.I)), ("weather", re.compile(r"rain|weather|snow|ice|road", re.I)),
        ("contractors", re.compile(r"contractor|rig is busy|workover rig", re.I)), ("crews", re.compile(r"crew", re.I))]
LONGER = re.compile(r"(?:stayed|is staying|has stayed|stays) shut in for about (\d+) days? longer than the repair itself because ([^.]*)", re.I)


def shut_in_waits(deps: RunDeps, start: str = "2026-01-01", end: str | None = None):
    """Jobs started in [start, end] whose report says the well 'stayed shut in for about N days longer than the repair
    itself because ...': per area and reason (parts / crews / contractors / weather) the share of such jobs and the
    typical (median) extra days. A repair belongs to a period by its start date."""
    F, rows = fleet(), []
    end = end or str(deps.today)
    for wid, w in F["wells"].items():
        for j in w["jobs"]:
            st = j.get("started_at") or ""
            if not (start <= st[:10] <= end):
                continue
            m = LONGER.search(w["reports"].get(j["maintenance_id"], ""))
            if not m:
                continue
            reason = next((k for k, rx in WAIT if rx.search(m.group(2))), "other")
            rows.append({"area": w["area"], "reason": reason, "extra": int(m.group(1)), "id": j["maintenance_id"]})
    by: dict[tuple[str, str], list[int]] = {}
    for r in rows:
        by.setdefault((r["area"], r["reason"]), []).append(r["extra"])
    tot = {a: sum(len(v) for (aa, _), v in by.items() if aa == a) for a, _ in by}
    med = lambda v: sorted(v)[len(v) // 2] if len(v) % 2 else (sorted(v)[len(v) // 2 - 1] + sorted(v)[len(v) // 2]) / 2
    out = [{"area": a, "waiting_on": k, "jobs": len(v), "share_pct": round(100 * len(v) / tot[a]), "typical_extra_days": med(v)}
           for (a, k), v in sorted(by.items())]
    return _done(deps, "entity_list", out, [f"{len(rows)} jobs started {start}..{end} whose report states the extra shut-in "
                                            "and its cause ('... longer than the repair itself because ...'); typical = median",
                                            "share = that reason's share of the area's delayed jobs"])
