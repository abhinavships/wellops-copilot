"""Cross-stream contracts. Backend (agent, tools, verifier), frontend (renderer) and evals all build
against these. Change only with a note in DECISIONS.md; the TS mirror lives in frontend/src/contracts.ts.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

import json

from pydantic import BaseModel, Field, model_validator


class RunDeps(BaseModel):
    """Per-run context injected by the API from the session. Never chosen by the model."""

    principal: str  # user_id, e.g. "U-201"
    chat_id: str
    today: date
    active_dataset: str = "sample"

    @property
    def asof(self) -> str:
        return f"{self.today} 23:59:59+00"


class Citation(BaseModel):
    kind: Literal["inspection", "maintenance", "report", "reg_event", "note", "assertion", "doc", "well"]
    id: str
    span: tuple[int, int] | None = None  # char span inside a report/doc
    label: str | None = None


class DQFlag(BaseModel):
    entity_id: str
    kind: str
    detail: str


class ToolResult(BaseModel):
    """Every data tool returns this. Rows live server-side under `handle`; the model sees a preview."""

    handle: str  # e.g. "r3"; resolvable by the UI (/api/handles/{handle}) and the verifier
    shape: Literal["scalar", "entity", "entity_list", "timeseries", "category_measure", "geo_points", "matrix",
                   "obligation_set", "diff", "text"]
    columns: list[str]
    preview: list[dict[str, Any]]  # first N rows
    row_count: int
    sql: str | None = None  # canonical query that produced it (re-executed by the verifier)
    citations: list[Citation] = []
    dq_flags: list[DQFlag] = []
    applied_overlays: list[str] = []  # assertion ids that changed these rows
    notes: list[str] = []  # assumptions: window, as-of, dataset


class Claim(BaseModel):
    """A fact stated in the answer, bound to the result it came from (grounding gate)."""

    text: str  # as written in the prose, e.g. "21 days"
    value: str | float | int | bool
    handle: str
    column: str | None = None
    row_key: dict[str, Any] | None = None  # e.g. {"asset_id": "WELL-0001"}


class Conflict(BaseModel):
    subject: str  # e.g. "INS-0164-0205 producing state"
    sides: list[dict[str, Any]]  # [{"source": "...", "author": "...", "says": "...", "ref": "..."}]
    resolution: Literal["unresolved", "data_supports_a", "correction_applied", "claim_unconfirmed"]
    ask: str | None = None  # confirmation question for the user


VisualType = Literal["status_card", "table", "line", "bar", "map", "heatmap", "obligation_timeline",
                     "correction_diff", "conflict_card", "evidence_chain", "kpi", "gauge", "timeline", "image",
                     "ingest_report", "none"]


class Visual(BaseModel):
    """A2UI-style component spec. Data is bound by handle; the LLM never copies rows into it."""

    type: VisualType
    handle: str | None = None
    title: str | None = None
    encodings: dict[str, Any] = {}  # e.g. {"x": "asset_id", "y": "downtime_days", "sort": "desc"}
    annotations: list[dict[str, Any]] = []  # e.g. threshold lines, shaded not-producing bands
    requested_by_user: bool = False


class AnswerSpec(BaseModel):
    markdown: str
    claims: list[Claim] = []
    visuals: list[Visual] = []
    citations: list[Citation] = []
    conflicts: list[Conflict] = []
    applied_overlays: list[str] = []
    assumptions: list[str] = []  # window, as-of, dataset, conventions
    follow_ups: list[str] = []  # e.g. "Suggest a site check: no recorded reason"

    @model_validator(mode="before")
    @classmethod
    def _unwrap(cls, v: Any) -> Any:  # small models wrap the output: {"AnswerSpec": {...}}
        if isinstance(v, str) and v.lstrip().startswith("{"):
            v = json.loads(v)
        if isinstance(v, dict) and "markdown" not in v and len(v) == 1:
            (inner,) = v.values()
            if isinstance(inner, dict) and "markdown" in inner:
                return inner
        return v


AssertionKind = Literal["value_correction", "analysis_correction", "detection_correction", "rule_override",
                        "shared_fact", "status_claim", "planned_downtime"]
ScopeLevel = Literal["inspection", "well", "area", "well_type", "fleet"]


class AssertionIn(BaseModel):
    kind: AssertionKind
    scope_level: ScopeLevel
    scope_key: str
    target_attr: str | None = None  # wellhead_temp_c | spill_est_volume_bbl | spill_is_real | <rule param> | status
    value_num: float | None = None
    value_text: str | None = None
    reason: str | None = None
    valid_from: date | None = None
    valid_to: date | None = None

    @model_validator(mode="before")
    @classmethod
    def _json_string(cls, v: Any) -> Any:  # small models send nested tool args as JSON strings
        return json.loads(v) if isinstance(v, str) and v.lstrip().startswith("{") else v


class AssertionOut(AssertionIn):
    assertion_id: str
    author_user_id: str
    original_num: float | None = None
    status: Literal["confirmed", "unconfirmed", "disputed", "superseded"]
    tx_time: datetime
    conflicts: list[Conflict] = []
    effects: list[str] = []  # human-readable consequences, e.g. "WELL-0164 now producing"


# AG-UI custom event names emitted alongside standard AG-UI events.
CUSTOM_EVENTS = ("wellops.answer", "wellops.verification", "wellops.capability", "wellops.conflict")
