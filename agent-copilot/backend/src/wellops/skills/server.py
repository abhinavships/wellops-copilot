"""The skills library as its own MCP server (streamable HTTP, stateless, JSON responses), mounted at /skills-mcp.

Any MCP client (Claude Desktop, Cursor, another agent) can browse, retrieve, author and grade WellOps playbooks here;
the live WellOps agent reaches find_skills/get_skill through the main registry at /mcp.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from mcp.server.fastmcp import FastMCP

import wellops.skills as K

skills_mcp = FastMCP("wellops-skills", stateless_http=True, json_response=True)
skills_mcp.settings.streamable_http_path = "/"


def _j(v: Any) -> str:
    return json.dumps(v, default=str)


@skills_mcp.tool()
def list_skills(source: str | None = None, include_deprecated: bool = False) -> str:
    """All playbooks (manual = hand-written, auto = distilled from verified turns) with usage and success rate."""
    return _j(K.list_skills(source, include_deprecated))


@skills_mcp.tool()
def get_skill(skill_id: str) -> str:
    """One playbook: templated steps (tool calls with {slot} arguments), visual, pitfalls, triggers, stats."""
    return _j(K.get_skill(skill_id) or {"error": f"unknown skill {skill_id!r}"})


@skills_mcp.tool()
def find_skills(question: str, k: int = 3) -> str:
    """The k playbooks that best fit a question (keyword/BM25 over triggers and description, weighted by success rate)."""
    return _j(K.find_skills(question, k))


@skills_mcp.tool()
def create_skill(name: str, description: str, steps: list[str] | None = None, triggers: list[str] | None = None,
                 visual: str | None = None, pitfalls: list[str] | None = None, skill_id: str | None = None) -> str:
    """Add a playbook, or edit one by passing its skill_id (version bumps, stats kept). steps are tool calls with slot
    templates, e.g. ['resolve_entity(text={area})', 'downtime(scope={area_scope}, start={window.start},
    group_by="well", top_k={n})', 'show_visual(type="bar")']. triggers are example questions."""
    return _j(K.create_skill(name, description, triggers, steps, visual, pitfalls, skill_id))


@skills_mcp.tool()
def record_outcome(skill_id: str, ok: bool, question: str | None = None, pitfall: str | None = None) -> str:
    """Grade a use of a playbook: ok=true reinforces it (question becomes a trigger); ok=false counts a failure and
    records the pitfall. Auto playbooks that keep failing are deprecated."""
    return _j(K.record_outcome(skill_id, ok, question, pitfall) or {"error": f"unknown skill {skill_id!r}"})


@skills_mcp.tool()
def export_skill_md(skill_id: str) -> str:
    """A playbook rendered as an agentskills.io SKILL.md (YAML frontmatter + markdown)."""
    return K.export_skill_md(skill_id) or _j({"error": f"unknown skill {skill_id!r}"})


def install(app) -> None:
    """Mount at /skills-mcp and run this server's session manager inside the app lifespan."""
    inner = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(a):
        async with skills_mcp.session_manager.run():
            async with inner(a) as state:
                yield state

    app.router.lifespan_context = lifespan
    app.mount("/skills-mcp", skills_mcp.streamable_http_app())
