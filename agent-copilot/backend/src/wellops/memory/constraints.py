"""Column constraints for user corrections: what a field is, what values are physically plausible, and whether it
can be edited at all (derived fields are computed, identity fields never change).

Every value a user proposes passes through `check()` before anything is stored, so "infinity", "1.2.2" or a 3000 °C
wellhead never reach the data, and an edit to a computed column is explained instead of silently applied.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Column:
    label: str
    kind: str  # "number" | "bool" | "derived" | "rule" | "identity"
    unit: str = ""
    lo: float | None = None
    hi: float | None = None
    assertion: str | None = None  # assertion kind that stores an edit
    target: str | None = None
    why: str = ""  # explanation for non-editable fields


COLUMNS: dict[str, Column] = {
    "wellhead_temp_c": Column("Wellhead temperature", "number", "°C", -40, 250, "value_correction", "wellhead_temp_c"),
    "ambient_temp_c": Column("Ambient temperature", "number", "°C", -60, 60, "value_correction", "ambient_temp_c"),
    "spill_est_volume_bbl": Column("Spill volume", "number", "bbl", 0, 5000, "analysis_correction", "spill_est_volume_bbl"),
    "spill": Column("Spill detection", "bool", assertion="detection_correction", target="spill_is_real"),
    "delta_c": Column("Δ above ambient", "derived", "°C", why="computed as wellhead − ambient; correct a temperature instead"),
    "margin_c": Column("Margin", "derived", "°C", why="computed as Δ − threshold; correct a temperature instead"),
    "producing": Column("Producing status", "derived", why="computed from Δ vs the manual threshold; correct a reading, "
                                                         "or say it as a status claim to have it recorded and checked"),
    "delta_min_c": Column("Production threshold", "rule", "°C", 0, 100, why="a manual rule for the whole well type; "
                                                                        "state it as a rule (e.g. 'for all T200 wells use 12 °C')"),
    "inspected_at": Column("Inspection time", "identity", why="identifies the inspection record"),
    "inspection_id": Column("Inspection id", "identity", why="identifies the inspection record"),
}
ALIASES = {"wellhead": "wellhead_temp_c", "wellhead_c": "wellhead_temp_c", "wellhead_eff": "wellhead_temp_c",
           "wellhead_recorded_c": "wellhead_temp_c", "temperature": "wellhead_temp_c", "ambient": "ambient_temp_c",
           "ambient_c": "ambient_temp_c", "air": "ambient_temp_c", "volume": "spill_est_volume_bbl",
           "spill_volume": "spill_est_volume_bbl", "volume_bbl": "spill_est_volume_bbl", "delta": "delta_c",
           "threshold": "delta_min_c", "threshold_c": "delta_min_c", "margin": "margin_c", "status": "producing",
           "spill_is_real": "spill", "spill_detected": "spill", "date": "inspected_at", "time": "inspected_at"}
NUMBER = re.compile(r"^[+-]?(\d+(\.\d+)?|\.\d+)$")


def column(name: str) -> tuple[str, Column | None]:
    k = ALIASES.get(name.strip().lower(), name.strip().lower())
    return k, COLUMNS.get(k)


def check(name: str, raw: Any) -> tuple[str, Any, str | None]:
    """-> (canonical column, parsed value, error or None)."""
    k, col = column(name)
    if col is None:
        return k, raw, f"unknown field {name!r}; editable fields: " + ", ".join(
            c for c, v in COLUMNS.items() if v.kind in ("number", "bool"))
    if col.kind in ("derived", "identity"):
        return k, raw, f"{col.label} is not editable: {col.why}"
    if col.kind == "rule":
        return k, raw, f"{col.label} is {col.why}"
    if col.kind == "bool":
        s = str(raw).strip().lower()
        if s in ("false", "no", "0", "not real", "not a spill", "none"):
            return k, False, None
        if s in ("true", "yes", "1", "real"):
            return k, True, None
        return k, raw, f"{col.label} must be yes/no, got {raw!r}"
    s = str(raw).strip().replace(",", "").removesuffix(col.unit).removesuffix("°C").removesuffix("C").strip()
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        v = float(raw)
    elif NUMBER.match(s):
        v = float(s)
    else:
        return k, raw, f"{col.label} must be a number in {col.unit or 'units'}, got {raw!r}"
    if math.isnan(v) or math.isinf(v):
        return k, raw, f"{col.label} must be a finite number, got {raw!r}"
    if (col.lo is not None and v < col.lo) or (col.hi is not None and v > col.hi):
        return k, v, f"{col.label} {v:g} {col.unit} is outside the plausible range {col.lo:g}–{col.hi:g} {col.unit}"
    return k, v, None
