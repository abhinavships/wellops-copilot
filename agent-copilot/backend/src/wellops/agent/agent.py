"""Pydantic AI agent: RunDeps in, AnswerSpec out. Built lazily so imports work without keys or a database."""

from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models import Model

from wellops import memory, tools
from wellops.contracts import AnswerSpec, AssertionIn, AssertionOut, RunDeps
from wellops.settings import settings
from wellops.tools.core import Scope, ToolError
from wellops.verify import run_validator

from . import prompt, skills as sk


def build_model(names: list[str] | None = None) -> Model:
    """FallbackModel over MODEL_FALLBACKS (local Ollama first, then OpenRouter free models); falls over on API errors."""
    from pydantic_ai.models.fallback import FallbackModel
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.models.openrouter import OpenRouterModel
    from pydantic_ai.providers.ollama import OllamaProvider
    from pydantic_ai.providers.openrouter import OpenRouterProvider

    built: list[Model] = []
    for spec in names or settings.fallback_models:
        kind, _, name = spec.partition(":")
        if kind == "ollama" and not settings.ollama_base_url:
            continue
        if kind == "ollama":
            built.append(OpenAIChatModel(name, provider=OllamaProvider(base_url=settings.ollama_base_url or None)))
        elif kind == "openrouter":
            built.append(OpenRouterModel(name, provider=OpenRouterProvider(api_key=settings.openrouter_api_key or "missing")))
        else:
            from pydantic_ai.models import infer_model
            built.append(infer_model(spec))
    return built[0] if len(built) == 1 else FallbackModel(*built)


def _call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except ToolError as e:
        raise ModelRetry(str(e)) from e


@lru_cache(maxsize=1)
def _skills() -> dict[str, sk.Skill]:
    return sk.load_skills()


def build_agent(model: Model | str | None = None) -> Agent[RunDeps, AnswerSpec]:
    agent: Agent[RunDeps, AnswerSpec] = Agent(
        model, deps_type=RunDeps, output_type=AnswerSpec, retries={"tools": 2, "output": 2}, name="wellops")

    @agent.instructions
    def _sys(ctx: RunContext[RunDeps]) -> str:
        return prompt.SYSTEM.format(skills=sk.catalog(_skills()), today=ctx.deps.today, principal=ctx.deps.principal)

    @agent.output_validator
    def _verify(ctx: RunContext[RunDeps], out: AnswerSpec) -> AnswerSpec:
        return run_validator(ctx, out)

    t = agent.tool

    @t
    def resolve_entity(ctx: RunContext[RunDeps], text: str) -> tools.ToolOut:
        """Map text to ids: wells, areas ("Northeast Block 8"), users, well types, INS-/MNT- ids, and relative dates
        ("last 4 months", "yesterday") as start/end windows against today."""
        return tools.resolve_entity(ctx.deps, text)

    @t
    def well_status(ctx: RunContext[RunDeps], scope: Scope, at_date: str | None = None,
                    only: Literal["producing", "not_producing"] | None = None) -> tools.ToolOut:
        """Current status per well from its latest inspection: delta vs threshold, recorded vs effective status,
        reason_summary (notes/repairs covering the reading, or 'No recorded reason'). scope: {kind: wells|area|well_type|all, ids: [...]}."""
        return _call(tools.well_status, ctx.deps, scope, at_date, only)

    @t
    def inspection_series(ctx: RunContext[RunDeps], well: str, start: str | None = None, end: str | None = None) -> tools.ToolOut:
        """Inspection readings of one well over time (ambient, wellhead, delta, producing). Dates YYYY-MM-DD."""
        return _call(tools.inspection_series, ctx.deps, well, start, end)

    @t
    def downtime(ctx: RunContext[RunDeps], scope: Scope, start: str | None = None, end: str | None = None,
                 group_by: Literal["well", "area", "well_type", "month", "year", "area_month", "none"] | None = None,
                 top_k: int | None = None) -> tools.ToolOut:
        """Downtime days = 7 x non-producing inspections (never maintenance.downtime_hours). Grouped/ranked."""
        return _call(tools.downtime, ctx.deps, scope, start, end, group_by, top_k)

    @t
    def spills(ctx: RunContext[RunDeps], scope: Scope, start: str | None = None, end: str | None = None,
               group_by: Literal["well", "area", "month", "area_month", "none"] | None = None) -> tools.ToolOut:
        """Real spills (corrections excluded; corrected ones under related_handles) with class, volume, lat/lon.
        group_by gives counts and total volume rows."""
        return _call(tools.spills, ctx.deps, scope, start, end, True, group_by)

    @t
    def obligations(ctx: RunContext[RunDeps], scope: Scope, start: str | None = None, end: str | None = None) -> tools.ToolOut:
        """SOP/manual duties (SR-01/02/03, RC-01/02/04, RCA, planned shut-in) as met|breached|pending with due dates and evidence."""
        return _call(tools.obligations, ctx.deps, scope, start, end)

    @t
    def rca_candidates(ctx: RunContext[RunDeps], scope: Scope, equipment_kind: str | None = None,
                       include_below: bool = False) -> tools.ToolOut:
        """Wells needing root-cause analysis (repeat corrective repairs on one equipment kind in 12 months), honouring rule overrides."""
        return _call(tools.rca_candidates, ctx.deps, scope, equipment_kind, include_below)

    @t
    def repair_costs(ctx: RunContext[RunDeps], scope: Scope, start: str | None = None, end: str | None = None,
                     group_by: Literal["well", "area", "equipment_kind", "maintenance_type", "month", "year", "none"] = "well",
                     maintenance_type: Literal["corrective", "scheduled"] | None = None) -> tools.ToolOut:
        """Repair cost by start date, grouped."""
        return _call(tools.repair_costs, ctx.deps, scope, start, end, group_by, maintenance_type)

    @t
    def evidence_chain(ctx: RunContext[RunDeps], id: str) -> tools.ToolOut:
        """Chain for an INS-/MNT-/REG-/CTX-/assertion id: inspection, repair, report facts with spans, regulatory events, notes, conflicts."""
        return _call(tools.evidence_chain, ctx.deps, id)

    @t
    def read_doc(ctx: RunContext[RunDeps], doc_id: str, section: str | None = None) -> tools.ToolOut:
        """Read a manual/SOP section verbatim (OM-T200, OM-T350, SOP-SR, SOP-RC, SOP-WS). No section = list sections."""
        return _call(tools.read_doc, ctx.deps, doc_id, section)

    @t
    def search_docs(ctx: RunContext[RunDeps], query: str, doc_id: str | None = None) -> tools.ToolOut:
        """Keyword search in manuals/SOPs."""
        return _call(tools.search_docs, ctx.deps, query, doc_id)

    @t
    def search_reports(ctx: RunContext[RunDeps], query: str, asset_id: str | None = None) -> tools.ToolOut:
        """Keyword search in maintenance report text."""
        return _call(tools.search_reports, ctx.deps, query, asset_id)

    @t
    def sql(ctx: RunContext[RunDeps], query: str, shape: str = "entity_list") -> tools.ToolOut:
        """Read-only SQL (SELECT/WITH, 2000 rows). Use {ds} and {asof} placeholders. Catalog:\n""" + tools.CATALOG
        return _call(tools.sql, ctx.deps, query, shape)

    @t
    def assert_fact(ctx: RunContext[RunDeps], assertion: AssertionIn) -> AssertionOut:
        """Commit user feedback now (attributed, undoable): value_correction/analysis_correction (inspection scope, target_attr,
        value_num), detection_correction, rule_override (scope_level well_type|area|well|fleet, target_attr = rule param,
        value_num), shared_fact, planned_downtime (well, valid_from/to), status_claim (well, value_text). Returns effects and conflicts."""
        return _call(memory.assert_fact, ctx.deps, assertion)

    @t
    def undo_assertion(ctx: RunContext[RunDeps], assertion_id: str) -> AssertionOut:
        """Undo (supersede) an assertion made in chat."""
        return _call(memory.undo_assertion, ctx.deps, assertion_id)

    @t
    def list_conflicts(ctx: RunContext[RunDeps], subject: str) -> list[Any]:
        """Conflicts about a well id or an inspection id (notes vs sensor, notes vs notes, claims vs data)."""
        return [c.model_dump() for c in _call(memory.list_conflicts, ctx.deps, subject)]

    @t
    def recall_episodes(ctx: RunContext[RunDeps], query: str, when: str | None = None) -> tools.ToolOut:
        """Earlier conversations of THIS user (e.g. 'the three wells we discussed yesterday'). Empty = none exist."""
        return memory.recall_episodes(ctx.deps, query, when)

    @t
    def list_skills(ctx: RunContext[RunDeps]) -> str:
        """List playbook skills."""
        return sk.catalog(_skills())

    @t
    def load_skill(ctx: RunContext[RunDeps], name: str) -> str:
        """Load a skill's itemized playbook."""
        s = _skills().get(name)
        return f"# {s.name}\n{s.body}" if s else f"unknown skill {name!r}; available: {sorted(_skills())}"

    @t
    def read_skill_file(ctx: RunContext[RunDeps], name: str, path: str) -> str:
        """Read a file inside a skill folder."""
        return sk.read_skill_file(_skills(), name, path)

    return agent


_AGENT: Agent[RunDeps, AnswerSpec] | None = None


def get_agent() -> Agent[RunDeps, AnswerSpec]:
    """The shared agent; the model is attached lazily on first use (no keys needed to import)."""
    global _AGENT
    if _AGENT is None:
        _AGENT = build_agent(build_model())
    return _AGENT


def default_model() -> Model:
    return build_model()
