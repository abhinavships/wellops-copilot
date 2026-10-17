"""Implied-state lexicon and conflict scanning (ADR-014): free text can imply a state; tags never act as corrections."""

from __future__ import annotations

import re
from typing import Literal

from wellops.contracts import Conflict, RunDeps
from wellops.tools.core import Db, in_list, lit

Implied = Literal["producing", "not_producing", "spill", "not_spill"]

# Order matters: negatives are checked before positives ("not producing" contains "producing").
LEXICON: list[tuple[Implied, re.Pattern]] = [
    ("not_spill", re.compile(r"not (?:a )?(?:crude|spill|oil)|produced water|\brain|\bwater\b|shadow|\bmud\b|wet soil", re.I)),
    ("spill", re.compile(r"detection stands|(?:it is|is|confirmed|sampled.*) crude|crude (?:oil )?confirmed", re.I)),
    ("not_producing", re.compile(r"not producing|not running|not moving|no stroke|unit stopped|cold to touch|shut[- ]?in|"
                                 r"shut down|\boffline\b|stopped", re.I)),
    ("producing", re.compile(r"back online|running fine|\bproducing\b|\bis running\b|running normally|online since", re.I)),
]


def implied_states(text: str | None) -> set[Implied]:
    """All states a text implies. A positive 'spill' match suppresses the 'not_spill' hit it may contain."""
    if not text:
        return set()
    hits = {k for k, p in LEXICON if p.search(text)}
    if "spill" in hits and not re.search(r"not (?:a )?crude|only produced water", text, re.I):
        hits.discard("not_spill")
    if "not_producing" in hits:
        hits.discard("producing")
    return hits


def _state_phrase(producing: bool, delta: float, thr: float) -> str:
    return f"{'producing' if producing else 'not producing'} (wellhead {delta:+.1f} °C vs air; threshold {thr:g} °C)"


def scan_inspections(db: Db, deps: RunDeps, ids: list[str], *, include_notes_vs_state: bool = True) -> list[Conflict]:
    """Conflicts about the given inspections: implied state of attached text vs the effective state, and
    human notes that disagree with each other (incl. notes that carry the inspection id only in their text)."""
    if not ids:
        return []
    ds, at = lit(deps.active_dataset), f"TIMESTAMPTZ '{deps.asof}'"
    ins = {r["inspection_id"]: r for r in db.rows(
        f"""SELECT e.inspection_id, e.asset_id, e.inspected_at, e.inspector_user_id, e.notes, e.producing, e.delta_c,
                   e.delta_min_c, e.wellhead_overlay, e.ambient_overlay, e.spill_real, e.detection_correction,
                   coalesce(u.name, e.inspector_user_id) inspector
            FROM ins_eff({ds}, {at}) e LEFT JOIN users u ON u.dataset_id={ds} AND u.user_id=e.inspector_user_id
            WHERE e.inspection_id IN {in_list(ids)}""")}
    ctx = db.rows(f"""SELECT c.ref, c.kind, c.author_user_id, coalesce(u.name, c.author_user_id) author, c.text,
                             c.inspection_ref FROM context_for({ds}, {at}) c
                      LEFT JOIN users u ON u.dataset_id={ds} AND u.user_id=c.author_user_id
                      WHERE c.inspection_ref IN {in_list(ids)} AND c.kind NOT IN ('correction', 'detection_correction',
                      'value_correction', 'analysis_correction', 'status_claim')""")
    by_ins: dict[str, list[dict]] = {}
    for c in ctx:
        by_ins.setdefault(c["inspection_ref"], []).append(c)
    out: list[Conflict] = []
    for iid, r in ins.items():
        eff = "producing" if r["producing"] else "not_producing"
        state_side = {"source": "sensor" if not (r["wellhead_overlay"] or r["ambient_overlay"]) else "user_correction",
                      "author": None, "says": _state_phrase(r["producing"], r["delta_c"], r["delta_min_c"]),
                      "ref": r["wellhead_overlay"] or r["inspection_id"]}
        texts = [("inspection_note", r["inspector"], r["notes"], iid)] + [
            (c["kind"], c["author"], c["text"], c["ref"]) for c in by_ins.get(iid, [])]
        if include_notes_vs_state:
            for src, author, text, ref in texts:
                im = implied_states(text)
                bad = {"producing", "not_producing"} & im - {eff}
                if bad:
                    out.append(Conflict(
                        subject=f"{iid} producing state",
                        sides=[state_side, {"source": src, "author": author, "says": text, "ref": ref}],
                        resolution="correction_applied" if state_side["source"] == "user_correction" else "unresolved",
                        ask="Please confirm which reading is right (is the well producing?)."))
        if r["detection_correction"] and include_notes_vs_state:  # a correction contradicted by attached text
            for src, author, text, ref in texts:
                if "spill" in implied_states(text) and src != "inspection_note":
                    out.append(Conflict(
                        subject=f"{iid} spill detection",
                        sides=[{"source": "user_correction", "author": None, "says": "detection corrected: not a spill",
                                "ref": r["detection_correction"]}, {"source": src, "author": author, "says": text, "ref": ref}],
                        resolution="correction_applied", ask="Please confirm the correction (a note says it is crude)."))
        # two human notes disagreeing about the same inspection
        labelled = [(a, t, ref, im) for _, a, t, ref in texts[1:] if (im := implied_states(t) & {"spill", "not_spill"})]
        for i, (a1, t1, r1, im1) in enumerate(labelled):
            for a2, t2, r2, im2 in labelled[i + 1:]:
                if im1 != im2:
                    stands = r["spill_real"]
                    out.append(Conflict(
                        subject=f"{iid} spill detection",
                        sides=[{"source": "note", "author": a1, "says": t1, "ref": r1},
                               {"source": "note", "author": a2, "says": t2, "ref": r2}],
                        resolution="data_supports_a" if stands else "correction_applied",
                        ask="Neither note is a formal correction; the detection "
                            + ("stands." if stands else "was corrected.") + " Confirm if a correction is intended."))
    return out


def scan_claims(db: Db, deps: RunDeps, assets: list[str]) -> list[Conflict]:
    """Stored status claims that disagree with the latest effective sensor state."""
    if not assets:
        return []
    ds, at = lit(deps.active_dataset), f"TIMESTAMPTZ '{deps.asof}'"
    rows = db.rows(f"""SELECT a.assertion_id, a.scope_key asset, a.value_text, a.status, a.valid_from, a.reason,
                              coalesce(u.name, a.author_user_id) author, s.inspection_id, s.inspected_at, s.delta_c,
                              s.delta_min_c, s.producing
                       FROM assertions a JOIN latest_status({ds}, {at}) s ON s.asset_id = a.scope_key
                       LEFT JOIN users u ON u.dataset_id={ds} AND u.user_id=a.author_user_id
                       WHERE a.dataset_id={ds} AND a.kind='status_claim' AND a.scope_level='well'
                         AND a.superseded_by IS NULL AND a.scope_key IN {in_list(assets)}""")
    out = []
    for r in rows:
        claimed = implied_states(r["value_text"]) & {"producing", "not_producing"}
        eff = "producing" if r["producing"] else "not_producing"
        if claimed and eff not in claimed:
            out.append(Conflict(
                subject=f"{r['asset']} current state",
                sides=[{"source": "sensor", "author": None, "says": _state_phrase(r["producing"], r["delta_c"], r["delta_min_c"]),
                        "ref": r["inspection_id"]},
                       {"source": "user_claim", "author": r["author"], "says": r["value_text"], "ref": r["assertion_id"]}],
                resolution="claim_unconfirmed", ask=f"{r['author']}, can you confirm? The latest inspection disagrees."))
    return out
