"""REST for people: browse, read, author and export WellOps skills (the same store the agent learns into)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

import wellops.skills as K

router = APIRouter(prefix="/api/skills", tags=["skills"])


class SkillIn(BaseModel):
    name: str
    description: str = ""
    steps: list[Any] | None = None  # 'tool(arg={slot}, ...)' strings or {"tool", "args"} objects
    triggers: list[str] | None = None
    visual: str | None = None
    pitfalls: list[str] | None = None
    skill_id: str | None = None  # set to edit an existing skill


class OutcomeIn(BaseModel):
    ok: bool
    question: str | None = None
    pitfall: str | None = None


@router.get("")
def list_or_find(q: str | None = None, k: int = 5, source: str | None = None, include_deprecated: bool = False) -> list[dict]:
    return K.find_skills(q, k) if q else K.list_skills(source, include_deprecated)


@router.get("/{skill_id}")
def get_one(skill_id: str) -> dict:
    s = K.get_skill(skill_id)
    if not s:
        raise HTTPException(404, f"unknown skill {skill_id!r}")
    from wellops.skills import store
    return {**s, "runs": store.runs(s["skill_id"], 20)}


@router.post("")
def create_or_edit(body: SkillIn) -> dict:
    return K.create_skill(body.name, body.description, body.triggers, body.steps, body.visual, body.pitfalls, body.skill_id)


@router.post("/{skill_id}/outcome")
def outcome(skill_id: str, body: OutcomeIn) -> dict:
    s = K.record_outcome(skill_id, body.ok, body.question, body.pitfall)
    if not s:
        raise HTTPException(404, f"unknown skill {skill_id!r}")
    return s


@router.get("/{skill_id}/md", response_class=PlainTextResponse)
def markdown(skill_id: str) -> PlainTextResponse:
    md = K.export_skill_md(skill_id)
    if md is None:
        raise HTTPException(404, f"unknown skill {skill_id!r}")
    return PlainTextResponse(md, media_type="text/markdown")


def install(app) -> None:
    """REST router + the skills MCP server at /skills-mcp."""
    from wellops.skills.server import install as mount
    app.include_router(router)
    mount(app)
