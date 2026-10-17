"""Disputed data: show the proof first, change nothing until the user decides.

When a user says a value is wrong, the agent opens a dispute. We pull the provenance of that value (the raw record,
who recorded it and how, the rule that interprets it, notes and report text that mention it), present it as a proof
card, and ask: correct it, or keep it? Only an explicit "correct" commits an attributed, undoable assertion.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from wellops.contracts import AssertionIn, RunDeps
from wellops.tools.core import ToolError, get_db, lit, mac, user_names

ATTRS = {  # what the user can dispute -> (assertion kind, scope level, target_attr)
    "wellhead_temp_c": ("value_correction", "inspection", "wellhead_temp_c"),
    "ambient_temp_c": ("value_correction", "inspection", "ambient_temp_c"),
    "spill_est_volume_bbl": ("value_correction", "inspection", "spill_est_volume_bbl"),
    "spill": ("detection_correction", "inspection", "spill_is_real"),
    "producing": ("status_claim", "well", "status"),
}


def _ensure(db) -> None:
    db.run("""CREATE TABLE IF NOT EXISTS disputes (dispute_id VARCHAR PRIMARY KEY, principal VARCHAR, chat_id VARCHAR,
              record_kind VARCHAR, record_id VARCHAR, asset_id VARCHAR, attr VARCHAR, current_value VARCHAR,
              claimed_value VARCHAR, status VARCHAR, evidence VARCHAR, opened_at TIMESTAMPTZ, resolved_at TIMESTAMPTZ,
              assertion_id VARCHAR)""")


def open_dispute(deps: RunDeps, record_id: str, attr: str, claimed_value: Any = None, reason: str | None = None) -> dict:
    db = get_db()
    _ensure(db)
    attr = {"wellhead": "wellhead_temp_c", "temperature": "wellhead_temp_c", "ambient": "ambient_temp_c",
            "volume": "spill_est_volume_bbl", "status": "producing"}.get(attr, attr)
    if attr not in ATTRS:
        raise ToolError(f"can dispute {sorted(ATTRS)}; got {attr!r}")
    rid = record_id.strip().upper()
    if rid.startswith("WELL-"):  # a well: dispute its latest reading
        r = db.rows(f"SELECT inspection_id FROM {mac(deps, 'latest_status')} WHERE asset_id={lit(rid)}")
        if not r:
            raise ToolError(f"no inspection for {rid}")
        rid = r[0]["inspection_id"]
    ins = db.rows(f"SELECT * FROM {mac(deps, 'ins_eff')} WHERE inspection_id={lit(rid)}")
    if not ins:
        raise ToolError(f"unknown inspection {rid}")
    i = ins[0]
    raw = db.rows("SELECT * FROM inspections WHERE dataset_id=? AND inspection_id=?", [deps.active_dataset, rid])[0]
    names = user_names(db, deps)
    thr = i.get("delta_min_c")
    current = {"producing": i.get("producing"), "spill": raw.get("spill_detected"), "wellhead_temp_c": i.get("wellhead_eff"),
               "ambient_temp_c": i.get("ambient_eff"), "spill_est_volume_bbl": i.get("volume_eff")}.get(attr)
    ev: list[dict] = [{
        "source": f"inspection {rid}", "label": "Recorded reading",
        "value": f"wellhead {raw['wellhead_temp_c']} °C · ambient {raw['ambient_temp_c']} °C"
                 + (f" · spill {raw['spill_est_volume_bbl']} bbl" if raw.get("spill_detected") else ""),
        "author": names.get(raw["inspector_user_id"], raw["inspector_user_id"]), "at": raw["inspected_at"],
        "method": raw.get("method"), "kind": "inspection", "id": rid}]
    if thr is not None and attr in ("producing", "wellhead_temp_c", "ambient_temp_c"):
        delta = i.get("delta_c") or 0
        ev.append({"source": str(i.get("delta_rule") or "operating manual").replace("rule:", ""), "label": "Rule applied",
                   "value": f"Δ {delta:.1f} °C vs {thr:g} °C needed → {'producing' if delta >= thr else 'not producing'}",
                   "kind": "doc"})
    for n in db.rows("""SELECT note_id, author_user_id, created_at, text FROM notes WHERE dataset_id=? AND asset_id=?
                        AND created_at <= ?::TIMESTAMPTZ ORDER BY created_at DESC LIMIT 3""",
                     [deps.active_dataset, i["asset_id"], deps.asof]):
        ev.append({"source": f"note {n['note_id']}", "label": "Field note", "quote": n["text"],
                   "author": names.get(n["author_user_id"], n["author_user_id"]), "at": n["created_at"],
                   "kind": "note", "id": n["note_id"]})
    prior = db.rows("""SELECT assertion_id, kind, value_num, value_text, author_user_id, status FROM assertions
                       WHERE scope_key IN (?, ?) AND superseded_by IS NULL""", [rid, i["asset_id"]])
    for a in prior:
        ev.append({"source": f"assertion {a['assertion_id']}", "label": "Earlier correction",
                   "value": f"{a['kind']}: {a['value_num'] if a['value_num'] is not None else a['value_text']} [{a['status']}]",
                   "author": names.get(a["author_user_id"], a["author_user_id"]), "kind": "assertion", "id": a["assertion_id"]})
    consistent = len(prior) == 0
    hint = ("The recorded data is internally consistent; no earlier correction exists." if consistent
            else "There are earlier corrections on this record.")
    did = "D-" + uuid.uuid4().hex[:8]
    db.run("INSERT INTO disputes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL,NULL)",
           [did, deps.principal, deps.chat_id, "inspection", rid, i["asset_id"], attr, json.dumps(current, default=str),
            json.dumps(claimed_value, default=str), "pending", json.dumps(ev, default=str), datetime.now(timezone.utc)])
    return {"dispute_id": did, "record": {"kind": "inspection", "id": rid}, "asset_id": i["asset_id"], "attr": attr,
            "current_value": current, "claimed_value": claimed_value, "reason": reason, "evidence": ev,
            "verdict_hint": hint, "ask": "Does this evidence convince you, or should we correct it?"}


def resolve_dispute(deps: RunDeps, dispute_id: str, action: str, value: Any = None) -> dict:
    from wellops import memory
    db = get_db()
    _ensure(db)
    r = db.rows("SELECT * FROM disputes WHERE dispute_id=?", [dispute_id])
    if not r:
        raise ToolError(f"unknown dispute {dispute_id}")
    d = r[0]
    if d["status"] != "pending":
        return {"dispute_id": dispute_id, "status": d["status"], "assertion_id": d["assertion_id"]}
    now = datetime.now(timezone.utc)
    if action == "keep":
        db.run("UPDATE disputes SET status='kept', resolved_at=? WHERE dispute_id=?", [now, dispute_id])
        return {"dispute_id": dispute_id, "status": "kept", "message": "Kept the recorded value; your review is logged."}
    if action != "correct":
        raise ToolError("action must be 'correct' or 'keep'")
    kind, level, target = ATTRS[d["attr"]]
    claimed = value if value is not None else json.loads(d["claimed_value"] or "null")
    a = AssertionIn(kind=kind, scope_level=level, scope_key=d["record_id"] if level == "inspection" else d["asset_id"],
                    target_attr=target, reason=f"dispute {dispute_id} confirmed after reviewing evidence",
                    value_num=float(claimed) if kind == "value_correction" else (0.0 if kind == "detection_correction" else None),
                    value_text=str(claimed) if kind == "status_claim" else None)
    out = memory.assert_fact(deps, a)
    db.run("UPDATE disputes SET status='corrected', resolved_at=?, assertion_id=? WHERE dispute_id=?",
           [now, out.assertion_id, dispute_id])
    return {"dispute_id": dispute_id, "status": "corrected", "assertion_id": out.assertion_id,
            "assertion_status": out.status, "effects": out.effects}
