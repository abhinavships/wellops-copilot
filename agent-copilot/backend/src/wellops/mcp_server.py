"""WellOps tools as an MCP server (streamable HTTP, mounted in the API process so it shares the DuckDB handle).

Hermes Agent connects to it as `mcp-wellops`. Session context (user, chat, clock) arrives as request headers that
the API writes into the per-turn Hermes config, never as model arguments.
"""

from __future__ import annotations

import contextlib
import json
from datetime import date
from typing import Any, Literal

from mcp.server.fastmcp import Context, FastMCP

from wellops import memory, tools
from wellops.contracts import AssertionIn, RunDeps, Visual
from wellops.settings import settings
from wellops.tools.core import Scope, ToolError

mcp = FastMCP("wellops", stateless_http=True, json_response=True)
mcp.settings.streamable_http_path = "/"

UI_QUEUE: dict[str, list[Visual]] = {}  # chat_id -> visuals the agent asked to show this turn
TOOL_LOG: dict[str, list[Any]] = {}  # chat_id -> ToolOut results this turn (for the verifier)
PREVIEW_ROWS = 25


def _deps(ctx: Context) -> RunDeps:
    h = ctx.request_context.request.headers if ctx.request_context.request else {}
    return RunDeps(principal=h.get("x-user-id") or "U-201", chat_id=h.get("x-chat-id") or "default",
                   today=date.fromisoformat(h.get("x-clock-today") or str(settings.clock_today)),
                   active_dataset=settings.active_dataset)


def _out(ctx: Context, fn, *a, **k) -> str:
    deps = _deps(ctx)
    try:
        r = fn(deps, *a, **k)
    except ToolError as e:
        return json.dumps({"error": str(e)})
    if hasattr(r, "handle"):
        TOOL_LOG.setdefault(deps.chat_id, []).append(r)
        d = r.model_dump(mode="json")
        d["preview"] = d.get("preview", [])[:PREVIEW_ROWS]
        d["citations"] = d.get("citations", [])[:15]
        return json.dumps(d, default=str)
    return json.dumps(r.model_dump(mode="json") if hasattr(r, "model_dump") else r, default=str)


ScopeT = Scope | str | list[str] | None


@mcp.tool()
def resolve_entity(ctx: Context, text: str) -> str:
    """Map text to ids: wells, areas ("Northeast Block 8"), users, well types, INS-/MNT- ids, and relative dates
    ("last 4 months") as start/end windows against today. Call this first for areas and relative periods."""
    return _out(ctx, tools.resolve_entity, text)


@mcp.tool()
def well_status(ctx: Context, scope: ScopeT = None, at_date: str | None = None,
                only: Literal["producing", "not_producing"] | None = None) -> str:
    """Current status per well from its latest inspection: delta vs threshold, producing flag, reason_summary.
    scope: {"kind": "wells"|"area"|"well_type"|"all", "ids": [...]}, or a well id / area name string."""
    return _out(ctx, tools.well_status, scope, at_date, only)


@mcp.tool()
def inspection_series(ctx: Context, well: str, start: str | None = None, end: str | None = None) -> str:
    """Inspection readings of one well over time (ambient, wellhead, delta, producing). Dates YYYY-MM-DD."""
    return _out(ctx, tools.inspection_series, well, start, end)


@mcp.tool()
def downtime(ctx: Context, scope: ScopeT = None, start: str | None = None, end: str | None = None,
             group_by: Literal["well", "area", "well_type", "month", "year", "area_month", "none"] | None = None,
             top_k: int | None = None) -> str:
    """Downtime days = 7 x non-producing inspections. Grouped/ranked. Use resolve_entity for the window."""
    return _out(ctx, tools.downtime, scope, start, end, group_by, top_k)


@mcp.tool()
def spills(ctx: Context, scope: ScopeT = None, start: str | None = None, end: str | None = None,
           group_by: Literal["well", "area", "month", "area_month", "none"] | None = None) -> str:
    """Real spills (corrected detections excluded) with class, volume, lat/lon; group_by gives counts."""
    return _out(ctx, tools.spills, scope, start, end, True, group_by)


@mcp.tool()
def obligations(ctx: Context, scope: ScopeT = None, start: str | None = None, end: str | None = None) -> str:
    """SOP/manual duties (spill notices, containment, NOV answers, RCA...) as met|breached|pending with due dates."""
    return _out(ctx, tools.obligations, scope, start, end)


@mcp.tool()
def rca_candidates(ctx: Context, scope: ScopeT = None, equipment_kind: str | None = None) -> str:
    """Wells needing root-cause analysis (repeat corrective repairs on one equipment kind in 12 months)."""
    return _out(ctx, tools.rca_candidates, scope, equipment_kind, False)


@mcp.tool()
def repair_costs(ctx: Context, scope: ScopeT = None, start: str | None = None, end: str | None = None,
                 group_by: Literal["well", "area", "equipment_kind", "maintenance_type", "month", "year", "none"] = "well") -> str:
    """Repair cost by start date, grouped."""
    return _out(ctx, tools.repair_costs, scope, start, end, group_by, None)


@mcp.tool()
def evidence_chain(ctx: Context, id: str) -> str:
    """Evidence for an INS-/MNT-/REG-/CTX- id: inspection, repair, report spans, regulatory events, notes, conflicts."""
    return _out(ctx, tools.evidence_chain, id)


@mcp.tool()
def read_doc(ctx: Context, doc_id: str, section: str | None = None) -> str:
    """Read a manual/SOP section verbatim (OM-T200, OM-T350, SOP-SR, SOP-RC, SOP-WS, uploaded UP-*)."""
    return _out(ctx, tools.read_doc, doc_id, section)


@mcp.tool()
def search_docs(ctx: Context, query: str) -> str:
    """Keyword search in manuals, SOPs and uploaded documents."""
    return _out(ctx, tools.search_docs, query, None)


@mcp.tool()
def sql(ctx: Context, query: str) -> str:
    """Read-only SQL (SELECT/WITH). Use {ds} and {asof} placeholders. Prefer the typed tools above."""
    return _out(ctx, tools.sql, query, "entity_list")


@mcp.tool()
def remember_correction(ctx: Context, kind: str, scope_level: str, scope_key: str, target_attr: str | None = None,
                        value_num: float | None = None, value_text: str | None = None, reason: str | None = None) -> str:
    """Commit user feedback (attributed, undoable): value_correction (scope inspection, target_attr, value_num),
    rule_override (scope well_type|area|well|fleet, target_attr = rule param), status_claim, shared_fact."""
    return _out(ctx, memory.assert_fact, AssertionIn(kind=kind, scope_level=scope_level, scope_key=scope_key,
                                                      target_attr=target_attr, value_num=value_num,
                                                      value_text=value_text, reason=reason))


@mcp.tool()
def recall_conversations(ctx: Context, query: str) -> str:
    """Earlier conversations of THIS user (e.g. 'the wells we discussed yesterday')."""
    return _out(ctx, memory.recall_episodes, query, None)


@mcp.tool()
def generate_image(ctx: Context, prompt: str, width: int = 768, height: int = 512) -> str:
    """Create an image from a text prompt (illustrations, site diagrams, report covers). Then call
    show_visual(type="image", title=<prompt>, handle=<media_id>) to display it."""
    from wellops.tools.media_gen import generate_image as gen
    r = gen(_deps(ctx), prompt, width, height)
    deps = _deps(ctx)
    UI_QUEUE.setdefault(deps.chat_id, []).append(Visual(type="image", title=prompt[:80], encodings={"src": r["url"]}))
    return json.dumps(r)


VisualT = Literal["status_card", "table", "line", "bar", "map", "heatmap", "obligation_timeline", "evidence_chain",
                  "conflict_card", "correction_diff", "kpi", "gauge", "timeline", "image", "ingest_report", "none"]


@mcp.tool()
def show_visual(ctx: Context, type: VisualT, handle: str | None = None, title: str | None = None,
                x: str | None = None, y: str | None = None, series: list[str] | None = None) -> str:
    """Render generative UI for the user from a tool result handle (e.g. "r2"). Pick the type that fits the data:
    one well -> status_card; ranking/compare -> bar; over time -> line; lat/lon -> map; area x month -> heatmap;
    duties -> obligation_timeline; a single number -> kpi. Call once per visual, after the data tool."""
    deps = _deps(ctx)
    enc = {k: v for k, v in {"x": x, "y": y, "series": series}.items() if v}
    UI_QUEUE.setdefault(deps.chat_id, []).append(Visual(type=type, handle=handle, title=title, encodings=enc))
    return json.dumps({"ok": True, "shown": type, "handle": handle})


def install(app) -> None:
    """Mount at /mcp and run the MCP session manager inside the app lifespan."""
    inner = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(a):
        async with mcp.session_manager.run():
            async with inner(a) as state:
                yield state

    app.router.lifespan_context = lifespan
    app.mount("/mcp", mcp.streamable_http_app())
