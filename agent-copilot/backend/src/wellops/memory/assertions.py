"""Governed memory writer (ADR-013): corrections/rule overrides commit immediately, attributed, original kept, undoable."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, time, timezone

from wellops.contracts import AssertionIn, AssertionOut, Conflict, RunDeps

from wellops.tools.core import Db, ToolError, get_db, in_list, lit
from . import conflicts as cf

INSPECTION_ATTRS = {"wellhead_temp_c", "ambient_temp_c", "spill_est_volume_bbl", "spill_is_real"}
_last_tx: dict[str, datetime] = {}


def tx_now(deps: RunDeps) -> datetime:
    """Wall-clock time-of-day inside the run's clock day, strictly increasing per process."""
    wall = datetime.now(timezone.utc)
    t = datetime.combine(deps.today, wall.time().replace(microsecond=wall.microsecond), tzinfo=timezone.utc)
    t = min(t, datetime.combine(deps.today, time(23, 59, 58), tzinfo=timezone.utc))
    last = _last_tx.get(deps.active_dataset)
    if last and t <= last:
        t = last.replace(microsecond=min(last.microsecond + 1, 999999)) if last.microsecond < 999999 else last
        if t <= last:
            t = last.replace(second=min(last.second + 1, 58), microsecond=0)
    _last_tx[deps.active_dataset] = t
    return t


def _fx(deps: RunDeps) -> RunDeps:
    return deps.model_copy(update={"chat_id": "_fx"})


def _snapshot(db: Db, deps: RunDeps, a: AssertionIn) -> dict:
    d, at = lit(deps.active_dataset), f"TIMESTAMPTZ '{deps.asof}'"
    snap: dict = {}
    if a.scope_level == "inspection":
        r = db.rows(f"""SELECT e.asset_id, e.producing, round(e.delta_c, 1) delta_c, e.delta_min_c, e.wellhead_eff, e.volume_eff,
                               e.spill_detected, e.spill_real, e.inspected_at,
                               CASE WHEN e.volume_eff >= pm.value THEN 'major' WHEN e.volume_eff >= pr.value THEN 'reportable'
                                    WHEN e.volume_eff >= pmod.value THEN 'moderate' ELSE 'minor' END spill_class
                        FROM ins_eff({d}, {at}) e
                        JOIN params({d}, {at}) pm ON pm.asset_id=e.asset_id AND pm.param='spill_class_major_min_bbl'
                        JOIN params({d}, {at}) pr ON pr.asset_id=e.asset_id AND pr.param='spill_class_reportable_min_bbl'
                        JOIN params({d}, {at}) pmod ON pmod.asset_id=e.asset_id AND pmod.param='spill_class_moderate_min_bbl'
                        WHERE e.inspection_id={lit(a.scope_key)}""")
        snap = r[0] if r else {}
    elif a.kind == "rule_override":
        from wellops.tools.data import rca_candidates
        if a.target_attr == "rca_corrective_count_min":
            from wellops.tools.core import Scope
            res = rca_candidates(_fx(deps), Scope(), None, False)
            snap = {"rca": {(r["asset_id"], r["equipment_kind"]): r["corrective_count"] for r in db.rows(
                "SELECT asset_id, equipment_kind, corrective_count FROM (" + res.sql + ")")}}
    return snap


def _effects(db: Db, deps: RunDeps, a: AssertionIn, before: dict, after: dict) -> list[str]:
    fx: list[str] = []
    if a.scope_level == "inspection" and before and after:
        w, i = after["asset_id"], a.scope_key
        if before["producing"] != after["producing"]:
            fx.append(f"{w} now {'producing' if after['producing'] else 'not producing'} at {i} "
                      f"(Δ {after['delta_c']} °C vs threshold {after['delta_min_c']:g} °C; was Δ {before['delta_c']} °C)")
        if before["spill_real"] and not after["spill_real"]:
            fx.append(f"{i} no longer counts as a spill (corrected detection)")
        if after["spill_real"] and before["spill_class"] != after["spill_class"]:
            fx.append(f"{i} spill class {before['spill_class']} -> {after['spill_class']} "
                      f"({before['volume_eff']} -> {after['volume_eff']} bbl)")
            if after["spill_class"] in ("reportable", "major") and before["spill_class"] not in ("reportable", "major"):
                from wellops.tools.obligations import obligations
                res = obligations(_fx(deps), [w], str(after["inspected_at"])[:10], str(deps.today))
                rows = db.rows(f"SELECT rows FROM handles WHERE chat_id='_fx' AND handle={lit(res.handle)}")
                for r in json.loads(rows[0]["rows"]) if rows else []:
                    if r["trigger_ref"] == i and r["protocol"] in ("SR-01", "SR-03"):
                        fx.append(f"{r['protocol']} {r['state']}: due {r['due_at']} - {r['detail']}")
    if a.kind == "rule_override" and "rca" in before:
        from wellops.tools.core import Scope
        from wellops.tools.data import rca_candidates
        res = rca_candidates(_fx(deps), Scope(), None, False)
        now = {(r["asset_id"], r["equipment_kind"]): r["corrective_count"] for r in db.rows(
            "SELECT asset_id, equipment_kind, corrective_count FROM (" + res.sql + ")")}
        new = sorted(set(now) - set(before["rca"]))
        gone = sorted(set(before["rca"]) - set(now))
        fx += [f"{w} {k} now needs an RCA ({now[(w, k)]} corrective repairs)" for w, k in new]
        fx += [f"{w} {k} no longer needs an RCA" for w, k in gone]
        if not new and not gone:
            fx.append("no well changes RCA status")
    return fx


def _original(db: Db, deps: RunDeps, a: AssertionIn) -> float | None:
    d = lit(deps.active_dataset)
    if a.scope_level == "inspection" and a.target_attr in ("wellhead_temp_c", "ambient_temp_c", "spill_est_volume_bbl"):
        r = db.rows(f"SELECT {a.target_attr} v FROM inspections WHERE dataset_id={d} AND inspection_id={lit(a.scope_key)}")
        return r[0]["v"] if r else None
    if a.kind == "detection_correction":
        return 1.0
    if a.kind == "rule_override":
        sc = {"well": "asset_id", "area": "area_id", "well_type": "well_type"}.get(a.scope_level)
        flt = f"AND w.{sc}={lit(a.scope_key)}" if sc else ""
        r = db.rows(f"""SELECT min(p.value) v FROM params({d}, TIMESTAMPTZ '{deps.asof}') p JOIN wells w
                        ON w.dataset_id={d} AND w.asset_id=p.asset_id WHERE p.param={lit(a.target_attr)} {flt}""")
        return r[0]["v"] if r else None
    return None


def assert_fact(deps: RunDeps, a: AssertionIn) -> AssertionOut:
    db = get_db()
    d = lit(deps.active_dataset)
    if a.scope_level == "inspection" and not db.rows(
            f"SELECT 1 FROM inspections WHERE dataset_id={d} AND inspection_id={lit(a.scope_key)}"):
        raise ToolError(f"unknown inspection {a.scope_key!r}")
    if a.scope_level == "well" and not db.rows(f"SELECT 1 FROM wells WHERE dataset_id={d} AND asset_id={lit(a.scope_key)}"):
        raise ToolError(f"unknown well {a.scope_key!r}")
    if a.kind in ("value_correction", "analysis_correction"):
        if a.scope_level != "inspection" or a.target_attr not in INSPECTION_ATTRS - {"spill_is_real"} or a.value_num is None:
            raise ToolError("corrections need scope_level=inspection, target_attr in wellhead_temp_c|ambient_temp_c|"
                            "spill_est_volume_bbl and value_num")
    elif a.kind == "detection_correction":
        if a.scope_level != "inspection":
            raise ToolError("detection_correction needs scope_level=inspection")
        a = a.model_copy(update={"target_attr": "spill_is_real", "value_num": 0.0})
    elif a.kind == "rule_override":
        known = {r["param"] for r in db.rows("SELECT DISTINCT param FROM rules")}
        if a.target_attr not in known or a.value_num is None:
            raise ToolError(f"rule_override needs target_attr in {sorted(known)} and value_num")
    elif a.kind in ("shared_fact", "planned_downtime", "status_claim") and a.scope_level != "well":
        raise ToolError(f"{a.kind} needs scope_level=well")
    before = _snapshot(db, deps, a)
    original = _original(db, deps, a)
    claim_status: str = "confirmed"
    claim_conflict = False
    if a.kind == "status_claim":
        claimed = cf.implied_states(a.value_text or "") & {"producing", "not_producing"}
        cur = db.rows(f"SELECT producing FROM latest_status({d}, TIMESTAMPTZ '{deps.asof}') WHERE asset_id={lit(a.scope_key)}")
        if claimed and cur:
            eff = "producing" if cur[0]["producing"] else "not_producing"
            claim_conflict = eff not in claimed
        claim_status = "unconfirmed" if claim_conflict else "confirmed"
    aid = "A-" + uuid.uuid4().hex[:8]
    tx = tx_now(deps)
    db.run("INSERT INTO assertions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
           [aid, deps.active_dataset, a.kind, a.scope_level, a.scope_key, a.target_attr, a.value_num, a.value_text, original,
            deps.principal, a.reason, "chat", deps.chat_id, a.valid_from, a.valid_to, tx, claim_status, "fleet", None])
    after = _snapshot(db, deps, a)
    effects = _effects(db, deps, a, before, after)
    confl: list[Conflict] = []
    if a.scope_level == "inspection":
        confl = cf.scan_inspections(db, deps, [a.scope_key])
    elif a.kind == "status_claim":
        confl = cf.scan_claims(db, deps, [a.scope_key])
    if claim_conflict:
        effects.append("claim stored as unconfirmed: the latest inspection disagrees")
    return AssertionOut(**a.model_dump(), assertion_id=aid, author_user_id=deps.principal, original_num=original,
                        status=claim_status, tx_time=tx, conflicts=confl, effects=effects)


def undo_assertion(deps: RunDeps, assertion_id: str) -> AssertionOut:
    """Undo = supersede: the row stays (history), views stop applying it."""
    db = get_db()
    r = db.rows("SELECT * FROM assertions WHERE assertion_id=? AND dataset_id=?", [assertion_id, deps.active_dataset])
    if not r:
        raise ToolError(f"unknown assertion {assertion_id!r}")
    r = r[0]
    if r["superseded_by"]:
        raise ToolError("already superseded")
    if r["source"] == "dataset_note":
        raise ToolError("dataset notes cannot be undone from chat")
    a = AssertionIn(kind=r["kind"], scope_level=r["scope_level"], scope_key=r["scope_key"], target_attr=r["target_attr"],
                    value_num=r["value_num"], value_text=r["value_text"], reason=r["reason"],
                    valid_from=r["valid_from"], valid_to=r["valid_to"])
    before = _snapshot(db, deps, a)
    db.run("UPDATE assertions SET status='superseded', superseded_by=? WHERE assertion_id=?",
           [f"undo:{deps.principal}", assertion_id])
    after = _snapshot(db, deps, a)
    fx = _effects(db, deps, a, before, after)
    return AssertionOut(**a.model_dump(), assertion_id=assertion_id, author_user_id=r["author_user_id"],
                        original_num=r["original_num"], status="superseded", tx_time=r["tx_time"], effects=fx or ["undone"])


def list_conflicts(deps: RunDeps, subject: str) -> list[Conflict]:
    """Conflicts about a well (latest reading, its referenced inspections, claims) or one inspection."""
    db = get_db()
    d, at = lit(deps.active_dataset), f"TIMESTAMPTZ '{deps.asof}'"
    s = subject.strip().upper()
    if s.startswith("INS-"):
        return cf.scan_inspections(db, deps, [s])
    ids = [r["i"] for r in db.rows(
        f"""SELECT DISTINCT inspection_ref i FROM context_for({d}, {at}) WHERE asset_id={lit(s)} AND inspection_ref IS NOT NULL
            UNION SELECT inspection_id FROM latest_status({d}, {at}) WHERE asset_id={lit(s)}""")]
    return cf.scan_inspections(db, deps, ids) + cf.scan_claims(db, deps, [s])
