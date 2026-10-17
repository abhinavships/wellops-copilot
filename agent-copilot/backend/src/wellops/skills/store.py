"""Skill store in DuckDB: `skills` (one row per playbook, manual or auto-distilled) and `skill_runs` (every outcome).

JSON-ish fields (triggers, steps, pitfalls, keywords, tools) are stored as JSON text so the schema stays portable.
The store lives in the same DuckDB file as the substrate (via tools.get_db()), behind the Db lock.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from wellops.settings import ROOT

SKILLS_DIR = ROOT / "skills"
JSON_FIELDS = ("triggers", "steps", "pitfalls", "keywords", "tools")
MAX_TRIGGERS = 25
MAX_PITFALLS = 12

DDL = """
CREATE TABLE IF NOT EXISTS skills (
    skill_id VARCHAR PRIMARY KEY, name VARCHAR, description VARCHAR,
    triggers VARCHAR,          -- JSON list of example questions
    steps VARCHAR,             -- JSON list of {"tool": str, "args": {k: template|literal}}
    visual VARCHAR, pitfalls VARCHAR, body VARCHAR, tools VARCHAR,
    source VARCHAR,            -- manual | auto
    status VARCHAR DEFAULT 'active',  -- active | deprecated (decayed)
    signature VARCHAR, content_sha VARCHAR, version INTEGER DEFAULT 1,
    uses INTEGER DEFAULT 0, successes INTEGER DEFAULT 0, failures INTEGER DEFAULT 0,
    last_used TIMESTAMPTZ, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ,
    keywords VARCHAR           -- JSON list of normalized retrieval tokens
);
CREATE TABLE IF NOT EXISTS skill_runs (
    run_id VARCHAR PRIMARY KEY, skill_id VARCHAR, action VARCHAR, ok BOOLEAN, question VARCHAR,
    principal VARCHAR, chat_id VARCHAR, steps VARCHAR, detail VARCHAR, created_at TIMESTAMPTZ
);
"""


def now() -> datetime:
    return datetime.now(timezone.utc)


def db():
    from wellops.tools import get_db
    d = get_db()
    if not getattr(d, "_skills_ready", False):
        with d.lock:
            for stmt in DDL.split(";"):
                if stmt.strip():
                    d.con.execute(stmt)
            d._skills_ready = True
        seed_manual(d)
    return d


def _row(r: dict[str, Any]) -> dict[str, Any]:
    for f in JSON_FIELDS:
        v = r.get(f)
        r[f] = json.loads(v) if isinstance(v, str) and v else []
    n = (r.get("successes") or 0) + (r.get("failures") or 0)
    r["success_rate"] = round(((r.get("successes") or 0) + 1) / (n + 2), 3)  # Laplace: unknown skills sit at 0.5
    return r


def get(skill_id: str) -> dict[str, Any] | None:
    rows = db().rows("SELECT * FROM skills WHERE skill_id = ?", [skill_id])
    return _row(rows[0]) if rows else None


def all_skills(include_deprecated: bool = True, source: str | None = None) -> list[dict[str, Any]]:
    sql, p = "SELECT * FROM skills WHERE 1=1", []
    if not include_deprecated:
        sql += " AND status = 'active'"
    if source:
        sql += " AND source = ?"
        p.append(source)
    return [_row(r) for r in db().rows(sql + " ORDER BY source, skill_id", p)]


def upsert(s: dict[str, Any]) -> dict[str, Any]:
    """Insert or replace a full skill row (fields not given keep their defaults)."""
    d = db()
    t = now()
    s = {**s}
    s.setdefault("created_at", t)
    s["updated_at"] = t
    for f in JSON_FIELDS:
        s[f] = json.dumps(s.get(f) or [], default=str)
    cols = [c for c in ("skill_id", "name", "description", "triggers", "steps", "visual", "pitfalls", "body", "tools",
                        "source", "status", "signature", "content_sha", "version", "uses", "successes", "failures",
                        "last_used", "created_at", "updated_at", "keywords") if c in s]
    with d.lock:
        d.con.execute("DELETE FROM skills WHERE skill_id = ?", [s["skill_id"]])
        d.con.execute(f"INSERT INTO skills ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [s[c] for c in cols])
    return get(s["skill_id"])  # type: ignore[return-value]


def update(skill_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = now()
    for f in JSON_FIELDS:
        if f in fields:
            fields[f] = json.dumps(fields[f], default=str)
    sets = ", ".join(f"{k} = ?" for k in fields)
    db().run(f"UPDATE skills SET {sets} WHERE skill_id = ?", [*fields.values(), skill_id])


def log_run(skill_id: str | None, action: str, ok: bool, question: str | None = None, deps=None,
            steps: Any = None, detail: str | None = None) -> None:
    db().run("INSERT INTO skill_runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
             [uuid.uuid4().hex[:12], skill_id, action, ok, question, getattr(deps, "principal", None),
              getattr(deps, "chat_id", None), json.dumps(steps or [], default=str), detail, now()])


def runs(skill_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    sql, p = "SELECT * FROM skill_runs", []
    if skill_id:
        sql, p = sql + " WHERE skill_id = ?", [skill_id]
    return db().rows(sql + " ORDER BY created_at DESC LIMIT ?", [*p, limit])


def unique_id(base: str) -> str:
    base = slug(base) or "skill"
    existing = {r["skill_id"] for r in db().rows("SELECT skill_id FROM skills")}
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


def slug(s: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")[:60]


def add_unique(items: list[str], new: str | None, cap: int) -> list[str]:
    """Append if not already present (case/whitespace-insensitive); keep the newest `cap`."""
    if not new or not new.strip():
        return items
    key = " ".join(new.lower().split())
    if any(" ".join(i.lower().split()) == key for i in items):
        return items
    return [*items, new.strip()][-cap:]


# --- seeding from hand-written SKILL.md folders ---------------------------------------------------------------------

TOOL_NAMES = ("resolve_entity", "well_status", "inspection_series", "downtime", "spills", "obligations", "rca_candidates",
              "repair_costs", "evidence_chain", "read_doc", "search_docs", "search_knowledge", "sql",
              "remember_correction", "propose_correction", "recall_conversations", "recall_episodes", "assert_fact",
              "show_visual", "ingest_file", "analyze_image", "list_media", "generate_image")


def parse_skill_md(path: Path) -> dict[str, Any] | None:
    text = path.read_text()
    if not text.startswith("---"):
        return None
    _, fm, body = text.split("---", 2)
    meta = yaml.safe_load(fm) or {}
    body = body.strip()
    name = slug(str(meta.get("name") or path.parent.name))
    tools = [t for t in TOOL_NAMES if re.search(rf"\b{t}\b", body)]
    rules = [re.sub(r"^-\s*(\[[\w-]+\]\s*)?", "", ln).strip() for ln in body.splitlines() if ln.startswith("- ")]
    pitfalls = [r for r in rules if re.search(r"\b(never|do not|don't|not )\b", r, re.I)][:MAX_PITFALLS]
    return {"skill_id": name, "name": name, "description": str(meta.get("description", "")).strip(), "body": body,
            "tools": tools, "pitfalls": pitfalls, "triggers": list(meta.get("triggers") or []),
            "steps": list(meta.get("steps") or []), "visual": meta.get("visual"), "source": "manual",
            "content_sha": hashlib.sha1(text.encode()).hexdigest()}


def seed_manual(d=None, root: Path = SKILLS_DIR) -> int:
    """Import skills/*/SKILL.md as source='manual'. New files are inserted; a changed file updates the content and
    bumps the version but keeps usage stats; unchanged files are left alone. Returns the number written."""
    from .retrieve import keywords_for
    n = 0
    for f in sorted(root.glob("*/SKILL.md")):
        s = parse_skill_md(f)
        if not s:
            continue
        cur = get(s["skill_id"])
        if cur and cur.get("content_sha") == s["content_sha"]:
            continue
        if cur:
            s = {**cur, **{k: v for k, v in s.items() if k not in ("triggers",)},
                 "triggers": cur["triggers"] or s["triggers"], "version": (cur.get("version") or 1) + 1}
            s.pop("success_rate", None)
        s["keywords"] = keywords_for(s)
        upsert(s)
        n += 1
    return n
