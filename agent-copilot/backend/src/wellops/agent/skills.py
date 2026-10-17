"""agentskills.io folders: skills/<name>/SKILL.md with YAML frontmatter. Progressive disclosure via three tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from wellops.settings import ROOT

SKILLS_DIR = ROOT / "skills"


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    body: str


def load_skills(root: Path = SKILLS_DIR) -> dict[str, Skill]:
    out = {}
    for f in sorted(root.glob("*/SKILL.md")):
        text = f.read_text()
        if not text.startswith("---"):
            continue
        _, fm, body = text.split("---", 2)
        meta = yaml.safe_load(fm) or {}
        name = meta.get("name") or f.parent.name
        out[name] = Skill(name, str(meta.get("description", "")).strip(), f.parent, body.strip())
    return out


def catalog(skills: dict[str, Skill]) -> str:
    return "\n".join(f"- {s.name}: {s.description}" for s in skills.values())


def read_skill_file(skills: dict[str, Skill], name: str, path: str) -> str:
    s = skills.get(name)
    if not s:
        return f"unknown skill {name!r}; available: {sorted(skills)}"
    p = (s.path / path).resolve()
    if s.path.resolve() not in p.parents or not p.is_file():
        return f"no such file {path!r} in skill {name!r}"
    return p.read_text()[:20000]
