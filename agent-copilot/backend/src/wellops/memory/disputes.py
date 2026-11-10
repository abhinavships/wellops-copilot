"""User feedback on data: every proposed change is checked, explained, and backed by the record's provenance.

`propose_correction` takes ALL the fields a user changed in one message and decides per field:
- rejected  : fails the column constraints (not a number, "infinity", physically implausible) -> nothing stored
- computed  : a derived or identity field (Δ, threshold, producing flag, timestamps) -> explained, nothing stored
- applied   : valid and justified ("the gun was faulty; re-measured at 41.0 °C") -> stored now, attributed, original
              kept, undoable (what the evaluation spec asks for)
- pending   : (only with settings.feedback_commits off) valid but unjustified ("this is wrong, change it to 22.6") -> proof card, stored only if the user confirms
- claim     : a status statement ("it is producing") -> stored as a claim, confirmed or unconfirmed against the data
The answer always carries the evidence (raw record, author, method, rule, nearby notes, earlier corrections) and the
conflicts the change creates, so the user can judge whether the data or the correction is right.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from wellops.contracts import AssertionIn, RunDeps
from wellops.tools.core import ToolError, get_db, lit, mac, user_names

from wellops.settings import settings

from .constraints import check, column

JUSTIFIED = re.compile(r"re-?measur|measured|faulty|broken|calibrat|field crew|sampled|lab |confirmed|gauge|thermal gun|"
                       r"sensor|verified|inspected again|recount|typo|entered wrong|because", re.I)


def _ensure(db) -> None:
    db.run("""CREATE TABLE IF NOT EXISTS correction_requests (request_id VARCHAR PRIMARY KEY, principal VARCHAR,
              chat_id VARCHAR, record_id VARCHAR, asset_id VARCHAR, reason VARCHAR, changes VARCHAR, evidence VARCHAR,
              status VARCHAR, opened_at TIMESTAMPTZ, resolved_at TIMESTAMPTZ)""")


def _resolve_record(db, deps: RunDeps, record_id: str, date: str | None) -> str:
    rid = record_id.strip().upper()
    if not rid.startswith("WELL-"):
        return rid
    if date:  # "the 24 Sep spill at WELL-0151" -> that day's inspection
        r = db.rows(f"SELECT inspection_id FROM {mac(deps, 'ins_eff')} WHERE asset_id={lit(rid)} "
                    f"AND inspected_at::DATE = {lit(date)}::DATE ORDER BY inspected_at DESC LIMIT 1")
    else:
        r = db.rows(f"SELECT inspection_id FROM {mac(deps, 'latest_status')} WHERE asset_id={lit(rid)}")
    if not r:
        raise ToolError(f"no inspection for {rid}" + (f" on {date}" if date else ""))
    return r[0]["inspection_id"]


def evidence(db, deps: RunDeps, rid: str) -> tuple[dict, dict, list[dict]]:
    ins = db.rows(f"SELECT * FROM {mac(deps, 'ins_eff')} WHERE inspection_id={lit(rid)}")
    if not ins:
        raise ToolError(f"unknown inspection {rid}")
    i = ins[0]
    raw = db.rows("SELECT * FROM inspections WHERE dataset_id=? AND inspection_id=?", [deps.active_dataset, rid])[0]
    names = user_names(db, deps)
    ev: list[dict] = [{
        "source": f"inspection {rid}", "label": "Recorded reading",
        "value": f"wellhead {raw['wellhead_temp_c']} °C · ambient {raw['ambient_temp_c']} °C"
                 + (f" · spill {raw['spill_est_volume_bbl']} bbl" if raw.get("spill_detected") else ""),
        "author": names.get(raw["inspector_user_id"], raw["inspector_user_id"]), "at": raw["inspected_at"],
        "method": raw.get("method"), "kind": "inspection", "id": rid}]
    if i.get("delta_min_c") is not None:
        delta, thr = i.get("delta_c") or 0, i["delta_min_c"]
        ev.append({"source": str(i.get("delta_rule") or "operating manual").replace("rule:", ""), "label": "Rule applied",
                   "value": f"Δ {delta:.1f} °C vs {thr:g} °C needed → {'producing' if delta >= thr else 'not producing'}",
                   "kind": "doc"})
    if raw.get("notes"):
        ev.append({"source": f"inspection {rid} note", "label": "Inspector's note", "quote": raw["notes"],
                   "author": names.get(raw["inspector_user_id"], raw["inspector_user_id"]), "kind": "inspection", "id": rid})
    for n in db.rows("""SELECT note_id, author_user_id, created_at, text FROM notes WHERE dataset_id=? AND asset_id=?
                        AND created_at <= ?::TIMESTAMPTZ AND created_at >= ?::TIMESTAMPTZ - INTERVAL 45 DAY
                        ORDER BY created_at DESC LIMIT 3""",  # only notes near the reading are evidence
                     [deps.active_dataset, i["asset_id"], deps.asof, raw["inspected_at"]]):
        ev.append({"source": f"note {n['note_id']}", "label": "Field note", "quote": n["text"],
                   "author": names.get(n["author_user_id"], n["author_user_id"]), "at": n["created_at"],
                   "kind": "note", "id": n["note_id"]})
    for a in db.rows("""SELECT assertion_id, kind, target_attr, value_num, value_text, author_user_id, status FROM assertions
                        WHERE scope_key IN (?, ?) AND superseded_by IS NULL""", [rid, i["asset_id"]]):
        ev.append({"source": f"assertion {a['assertion_id']}", "label": "Earlier correction",
                   "value": f"{a['target_attr'] or a['kind']}: {a['value_num'] if a['value_num'] is not None else a['value_text']} [{a['status']}]",
                   "author": names.get(a["author_user_id"], a["author_user_id"]), "kind": "assertion", "id": a["assertion_id"]})
    return i, raw, ev


def _current(i: dict, raw: dict, col: str) -> Any:
    return {"wellhead_temp_c": i.get("wellhead_eff"), "ambient_temp_c": i.get("ambient_eff"), "spill_est_volume_bbl": i.get("volume_eff"),
            "spill": raw.get("spill_detected"), "producing": i.get("producing"), "delta_c": i.get("delta_c"),
            "delta_min_c": i.get("delta_min_c"), "inspected_at": raw.get("inspected_at"), "inspection_id": raw.get("inspection_id"),
            "margin_c": (i.get("delta_c") or 0) - (i.get("delta_min_c") or 0)}.get(col)


def _store(deps: RunDeps, rid: str, asset: str, col: str, value: Any, reason: str) -> dict:
    from wellops import memory
    _, c = column(col)
    if col == "producing":
        a = AssertionIn(kind="status_claim", scope_level="well", scope_key=asset, value_text="producing" if value else "not producing", reason=reason)
    elif c.kind == "bool":
        if value:
            raise ToolError("marking a spill as real again is not supported; undo the earlier detection correction instead")
        a = AssertionIn(kind="detection_correction", scope_level="inspection", scope_key=rid, target_attr="spill_is_real", value_num=0.0, reason=reason)
    else:
        a = AssertionIn(kind=c.assertion, scope_level="inspection", scope_key=rid, target_attr=c.target, value_num=float(value), reason=reason)
    out = memory.assert_fact(deps, a)
    return {"assertion_id": out.assertion_id, "assertion_status": out.status, "effects": out.effects,
            "conflicts": [x.model_dump(mode="json") for x in out.conflicts]}


def propose_correction(deps: RunDeps, record_id: str, changes: dict[str, Any], reason: str | None = None,
                       date: str | None = None) -> dict:
    db = get_db()
    _ensure(db)
    if not changes:
        raise ToolError("changes must name at least one field, e.g. {\"wellhead_temp_c\": 41.0}")
    rid = _resolve_record(db, deps, record_id, date)
    i, raw, ev = evidence(db, deps, rid)
    justified = bool(reason and JUSTIFIED.search(reason))
    commit = justified or settings.feedback_commits  # the user's explicit correction is stored now (attributed, undoable)
    rows, conflicts, effects = [], [], []
    for name, proposed in changes.items():
        col, val, err = check(name, proposed)
        _, meta = column(col)
        row = {"field": col, "label": meta.label if meta else name, "unit": meta.unit if meta else "",
               "current": _current(i, raw, col), "proposed": proposed}
        if col == "producing" or (meta and meta.kind == "derived" and str(proposed).strip().lower() in ("producing", "not producing", "true", "false")):
            # a status statement: record it as a claim that the data confirms or contradicts
            claimed = str(proposed).strip().lower() in ("true", "yes", "producing", "running", "1", "active")
            r = _store(deps, rid, i["asset_id"], "producing", claimed, reason or "user statement")
            row.update(field="producing", label="Producing status", proposed="producing" if claimed else "not producing",
                       status="claim", claim_status=r["assertion_status"], assertion_id=r["assertion_id"])
            conflicts += r["conflicts"]
            effects += r["effects"]
        elif err:
            row.update(status="computed" if meta and meta.kind in ("derived", "identity", "rule") else "rejected", reason=err)
        elif val == row["current"] or (isinstance(val, float) and isinstance(row["current"], (int, float)) and abs(val - row["current"]) < 1e-9):
            row.update(status="unchanged", reason="already the value on record")
        elif commit:
            r = _store(deps, rid, i["asset_id"], col, val, reason or "")
            row.update(status="applied", proposed=val, assertion_id=r["assertion_id"])
            conflicts += r["conflicts"]
            effects += r["effects"]
        else:
            row.update(status="pending", proposed=val, reason="no reason given: confirm after reviewing the evidence")
        rows.append(row)
    rid_req = "D-" + uuid.uuid4().hex[:8]
    pending = [r for r in rows if r["status"] == "pending"]
    db.run("INSERT INTO correction_requests VALUES (?,?,?,?,?,?,?,?,?,?,NULL)",
           [rid_req, deps.principal, deps.chat_id, rid, i["asset_id"], reason, json.dumps(rows, default=str),
            json.dumps(ev, default=str), "pending" if pending else "closed", datetime.now(timezone.utc)])
    applied = [r for r in rows if r["status"] == "applied"]
    ask = ("These changes are applied and attributed to you. Conflicting evidence is shown; confirm or undo." if applied and conflicts
           else "Does this evidence convince you, or should we apply the change?" if pending
           else "Nothing was changed." if not applied and not any(r["status"] == "claim" for r in rows) else None)
    return {"dispute_id": rid_req, "record": {"kind": "inspection", "id": rid}, "asset_id": i["asset_id"], "changes": rows,
            "evidence": ev, "conflicts": conflicts, "effects": list(dict.fromkeys(effects)), "justified": justified,
            "reason": reason, "ask": ask}


def open_dispute(deps: RunDeps, record_id: str, attr: str, claimed_value: Any = None, reason: str | None = None) -> dict:
    """Single-field form (kept for older callers)."""
    return propose_correction(deps, record_id, {attr: claimed_value}, reason)


def resolve_dispute(deps: RunDeps, dispute_id: str, action: str, value: Any = None) -> dict:
    """The user's decision on pending changes: 'correct' applies them (attributed), 'keep' keeps the record."""
    db = get_db()
    _ensure(db)
    r = db.rows("SELECT * FROM correction_requests WHERE request_id=?", [dispute_id])
    if not r:
        raise ToolError(f"unknown request {dispute_id}")
    d = r[0]
    rows = json.loads(d["changes"])
    if d["status"] != "pending":
        return {"dispute_id": dispute_id, "status": d["status"], "changes": rows}
    now = datetime.now(timezone.utc)
    if action == "keep":
        for x in rows:
            if x["status"] == "pending":
                x["status"] = "kept"
        db.run("UPDATE correction_requests SET status='kept', changes=?, resolved_at=? WHERE request_id=?",
               [json.dumps(rows, default=str), now, dispute_id])
        return {"dispute_id": dispute_id, "status": "kept", "changes": rows,
                "message": "Kept the recorded values; your review is logged."}
    if action != "correct":
        raise ToolError("action must be 'correct' or 'keep'")
    effects = []
    for x in rows:
        if x["status"] == "pending":
            out = _store(deps, d["record_id"], d["asset_id"], x["field"], x["proposed"],
                         f"{d['reason'] or 'user correction'} (confirmed after reviewing evidence, request {dispute_id})")
            x.update(status="applied", assertion_id=out["assertion_id"])
            effects += out["effects"]
    db.run("UPDATE correction_requests SET status='corrected', changes=?, resolved_at=? WHERE request_id=?",
           [json.dumps(rows, default=str), now, dispute_id])
    applied = [x for x in rows if x["status"] == "applied"]
    return {"dispute_id": dispute_id, "status": "corrected", "changes": rows, "effects": effects,
            "assertion_id": applied[-1]["assertion_id"] if applied else None}
