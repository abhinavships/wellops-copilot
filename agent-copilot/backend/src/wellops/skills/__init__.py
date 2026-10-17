"""Self-improving skills: playbooks the agent retrieves before acting, distilled automatically from verified turns.

Lifecycle: observe (on_turn_complete gets the tool path and the verifier verdict) -> distill (literals -> templated
steps, deterministic name/description) -> retrieve (find_skills: BM25 x success rate) -> reinforce / decay (uses,
successes, failures, pitfalls; an auto skill that keeps failing is deprecated and drops out of retrieval).

Public API (also served over MCP at /skills-mcp and REST at /api/skills):
    find_skills, get_skill, list_skills, create_skill, record_outcome, export_skill_md, learn_from_turn,
    on_turn_complete
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from wellops.settings import settings

from . import distill as D
from . import store as S
from .retrieve import keywords_for, rank

log = logging.getLogger("wellops.skills")
MATCH_SIMILARITY = 0.75  # step-token Jaccard above which a path counts as the same method
DEPRECATE_AFTER = 3  # failures before an auto skill can be deprecated
DEPRECATE_RATE = 0.35  # ... if its success rate is below this


# --- read ----------------------------------------------------------------------------------------------------------

def _public(s: dict[str, Any], full: bool = True) -> dict[str, Any]:
    out = {k: s.get(k) for k in ("skill_id", "name", "description", "source", "status", "version", "visual", "uses",
                                 "successes", "failures", "success_rate", "last_used", "created_at", "updated_at")}
    out["steps"] = [D.render_step(st) for st in s.get("steps") or [] if isinstance(st, dict)]
    out["pitfalls"] = s.get("pitfalls") or []
    out["triggers"] = (s.get("triggers") or [])[-8:] if not full else s.get("triggers") or []
    if full:
        out["steps_structured"] = s.get("steps") or []
        out["tools"] = s.get("tools") or []
        out["body"] = s.get("body")
    if "score" in s:
        out["score"] = s["score"]
    return out


def list_skills(source: str | None = None, include_deprecated: bool = False) -> list[dict[str, Any]]:
    return [_public(s, full=False) for s in S.all_skills(include_deprecated, source)]


def get_skill(skill_id: str) -> dict[str, Any] | None:
    s = S.get(skill_id) or S.get(S.slug(skill_id))
    return _public(s) if s else None


def find_skills(question: str, k: int = 3) -> list[dict[str, Any]]:
    """Best playbooks for a question: BM25 over triggers/description, weighted by success rate."""
    return [_public(s, full=False) for s in rank(question, S.all_skills(include_deprecated=False), k)]


# --- write ---------------------------------------------------------------------------------------------------------

def _steps_in(steps: list[Any] | None) -> list[dict[str, Any]]:
    out = []
    for st in steps or []:
        if isinstance(st, str):
            out.append(D.parse_step(st))
        elif isinstance(st, dict) and (st.get("tool") or st.get("name")):
            out.append({"tool": st.get("tool") or st.get("name"), "args": dict(st.get("args") or {})})
    return out


def create_skill(name: str, description: str = "", triggers: list[str] | None = None, steps: list[Any] | None = None,
                 visual: str | None = None, pitfalls: list[str] | None = None, skill_id: str | None = None,
                 body: str | None = None, source: str = "manual") -> dict[str, Any]:
    """Add a skill, or edit one when skill_id names an existing skill (bumps version, keeps stats). Steps may be
    strings like 'downtime(scope={area_scope}, group_by="well", top_k={n})' or {"tool", "args"} dicts."""
    cur = S.get(skill_id) if skill_id else None
    parsed = _steps_in(steps)
    if cur:
        s = {**cur}
        s.pop("success_rate", None)
        s.update({k: v for k, v in dict(name=S.slug(name) or cur["name"], description=description or cur["description"],
                                         visual=visual or cur.get("visual"), body=body if body is not None else cur.get("body")).items()})
        if steps is not None:
            s["steps"], s["signature"] = parsed, D.signature(parsed) if parsed else None
        if triggers is not None:
            s["triggers"] = list(dict.fromkeys(t.strip() for t in triggers if t and t.strip()))[-S.MAX_TRIGGERS:]
        if pitfalls is not None:
            s["pitfalls"] = list(dict.fromkeys(p.strip() for p in pitfalls if p and p.strip()))[-S.MAX_PITFALLS:]
        s["version"] = (cur.get("version") or 1) + 1
    else:
        sid = S.unique_id(skill_id or name)
        s = dict(skill_id=sid, name=S.slug(name) or sid, description=description, steps=parsed,
                 signature=D.signature(parsed) if parsed else None, visual=visual, body=body,
                 triggers=list(dict.fromkeys(t.strip() for t in triggers or [] if t and t.strip())),
                 pitfalls=list(dict.fromkeys(p.strip() for p in pitfalls or [] if p and p.strip())),
                 tools=list(dict.fromkeys(st["tool"] for st in parsed)), source=source, status="active", version=1,
                 uses=0, successes=0, failures=0)
    s["keywords"] = keywords_for(s)
    out = S.upsert(s)
    S.log_run(out["skill_id"], "edited" if cur else "created", True, detail=f"{source} create_skill")
    return _public(out)


def record_outcome(skill_id: str, ok: bool, question: str | None = None, pitfall: str | None = None,
                   deps=None, steps: Any = None) -> dict[str, Any] | None:
    """Reinforce (ok) or penalize (not ok) a skill; a question joins its triggers on success, a pitfall is recorded on
    failure. Auto skills that keep failing are deprecated (decay); a later success revives them."""
    s = S.get(skill_id)
    if not s:
        return None
    uses, succ, fail = (s["uses"] or 0) + 1, (s["successes"] or 0) + int(ok), (s["failures"] or 0) + int(not ok)
    fields: dict[str, Any] = dict(uses=uses, successes=succ, failures=fail, last_used=S.now())
    if ok and question:
        trig = S.add_unique(s["triggers"], question, S.MAX_TRIGGERS)
        if trig != s["triggers"]:
            fields["triggers"] = trig
    if not ok and pitfall:
        pits = S.add_unique(s["pitfalls"], pitfall, S.MAX_PITFALLS)
        if pits != s["pitfalls"]:
            fields["pitfalls"], fields["version"] = pits, (s.get("version") or 1) + 1
    rate = (succ + 1) / (succ + fail + 2)
    if s["source"] == "auto" and fail >= DEPRECATE_AFTER and rate < DEPRECATE_RATE:
        fields["status"] = "deprecated"
    elif s.get("status") == "deprecated" and rate >= 0.5:
        fields["status"] = "active"
    if "triggers" in fields:
        fields["keywords"] = keywords_for({**s, **fields})
    S.update(skill_id, **fields)
    S.log_run(skill_id, "reinforced" if ok else "failed", ok, question, deps, steps, pitfall)
    return _public(S.get(skill_id))  # type: ignore[arg-type]


# --- learning ------------------------------------------------------------------------------------------------------

def _visual_of(answer_visual: Any, steps: list[dict[str, Any]]) -> str | None:
    vs = answer_visual if isinstance(answer_visual, list | tuple) else [answer_visual] if answer_visual else []
    for v in vs:
        t = v if isinstance(v, str) else (v.get("type") if isinstance(v, dict) else getattr(v, "type", None))
        if t and t != "none":
            return str(t)
    for st in reversed(steps):
        if st["tool"] in D.RENDER_TOOLS and st["args"].get("type"):
            return str(st["args"]["type"])
    return None


def match(steps: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    sig, prim = D.signature(steps), D.primary(steps)
    best, best_sim = None, 0.0
    for s in S.all_skills(include_deprecated=True):
        if not s["steps"]:
            continue
        if s.get("signature") == sig:
            return s, 1.0
        p2 = D.primary(s["steps"])
        if not prim or not p2 or p2["tool"] != prim["tool"]:
            continue
        sim = D.similarity(steps, s["steps"])
        if sim > best_sim:
            best, best_sim = s, sim
    return (best, best_sim) if best_sim >= MATCH_SIMILARITY else (None, best_sim)


def _pitfall(trace_steps: list[dict[str, Any]], steps: list[dict[str, Any]]) -> str:
    bad = next((t for t in trace_steps or [] if not t.get("ok", True) and t.get("name") != "verify"), None)
    if bad:
        st = next((s for s in steps if s["tool"] == bad.get("name")), None)
        return f"Step {D.render_step(st) if st else bad.get('name')} failed" + (f": {bad['detail']}" if bad.get("detail") else "")
    ver = next((t for t in trace_steps or [] if t.get("name") == "verify"), None)
    detail = (ver or {}).get("detail")
    p = D.primary(steps)
    return f"Answer failed verification after {D.render_step(p) if p else 'the tool path'}" + (f" ({detail})" if detail else "")


def learn_from_turn(question: str, trace_steps: list[dict[str, Any]] | None, tool_calls_with_args: list[dict[str, Any]],
                    verification_ok: bool, answer_visual: Any = None, deps=None) -> dict[str, Any]:
    """Distill or reinforce a skill from one finished turn. Returns {"action", "skill_id", "skill"?}.

    verified + new method  -> created (source=auto, the question is its first trigger)
    verified + known method -> reinforced (uses/successes +1, question added as a trigger, deduped)
    failed + known method   -> failed (failures +1, the failing step recorded as a pitfall)
    failed + unknown method -> skipped (nothing is learned from an unverified path)
    """
    steps = D.steps_from_calls(tool_calls_with_args or [])
    if not D.learnable(steps):
        return {"action": "skipped", "skill_id": None, "reason": "no learnable data step"}
    visual = _visual_of(answer_visual, steps)
    found, sim = match(steps)
    if found:
        if verification_ok:
            out = record_outcome(found["skill_id"], True, question, deps=deps, steps=steps)
            if visual and not found.get("visual"):
                S.update(found["skill_id"], visual=visual)
            return {"action": "reinforced", "skill_id": found["skill_id"], "similarity": round(sim, 3), "skill": out}
        out = record_outcome(found["skill_id"], False, question, _pitfall(trace_steps or [], steps), deps, steps)
        return {"action": "failed", "skill_id": found["skill_id"], "similarity": round(sim, 3), "skill": out}
    if not verification_ok:
        S.log_run(None, "skipped", False, question, deps, steps, _pitfall(trace_steps or [], steps))
        return {"action": "skipped", "skill_id": None, "reason": "unverified path with no matching skill"}
    name = D.name_for(steps)
    sid = S.unique_id(name)
    s = dict(skill_id=sid, name=sid, description=D.describe(steps, visual), triggers=[question.strip()], steps=steps,
             visual=visual, pitfalls=[], body=None, tools=list(dict.fromkeys(st["tool"] for st in steps)), source="auto",
             status="active", signature=D.signature(steps), version=1, uses=1, successes=1, failures=0,
             last_used=S.now())
    s["keywords"] = keywords_for(s)
    S.upsert(s)
    S.log_run(sid, "created", True, question, deps, steps)
    if settings.skills_llm_polish and settings.gemini_api_key:
        threading.Thread(target=_polish, args=(sid,), daemon=True).start()
    return {"action": "created", "skill_id": sid, "skill": get_skill(sid)}


def _polish(skill_id: str) -> None:
    """Optional: let a small model rewrite the description (and propose a clearer name) of a new auto skill. Never
    changes steps or the id; any failure leaves the deterministic text in place. Off unless SKILLS_LLM_POLISH=true."""
    try:
        import httpx
        s = S.get(skill_id)
        if not s:
            return
        prompt = ("Rewrite this oil-field copilot playbook's name (kebab-case, <=6 words, verb first) and a one-sentence "
                  "description of when to use it. Reply as JSON {\"name\": ..., \"description\": ...}.\n"
                  f"name: {s['name']}\ndescription: {s['description']}\nsteps: "
                  + " -> ".join(D.render_step(st) for st in s["steps"]) + f"\nexample questions: {s['triggers'][:3]}")
        r = httpx.post("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", timeout=10,
                       headers={"Authorization": f"Bearer {settings.gemini_api_key}"},
                       json={"model": settings.skills_llm_model, "temperature": 0.2,
                             "response_format": {"type": "json_object"},
                             "messages": [{"role": "user", "content": prompt}]})
        r.raise_for_status()
        out = json.loads(r.json()["choices"][0]["message"]["content"])
        desc = str(out.get("description") or "").strip()[:300]
        name = S.slug(str(out.get("name") or ""))
        if desc:
            S.update(skill_id, description=desc, **({"name": name} if name else {}),
                     keywords=keywords_for({**s, "description": desc, "name": name or s["name"]}))
    except Exception as e:  # noqa: BLE001 - polishing is cosmetic
        log.info("skill polish skipped for %s: %s", skill_id, e)


# --- the turn hook -------------------------------------------------------------------------------------------------

def on_turn_complete(deps, question: str, steps: list[dict[str, Any]] | None, ok: bool, visuals: Any = None,
                     tool_calls: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """Called once per finished live turn (after the verifier). Never raises.

    steps: the wellops.trace steps ([{"name", "ms", "ok", "detail"?}]). tool_calls: [{"name", "args"}] in order; when
    omitted they are taken from mcp_server.CALL_LOG (filled by the native agent), aligned to the trace's tool steps.
    visuals: the answer's visuals (AnswerSpec.visuals, dicts or type strings).
    """
    try:
        from wellops import mcp_server
        chat = getattr(deps, "chat_id", "default")
        logged = mcp_server.CALL_LOG.pop(chat, [])
        if not settings.skills_learn:
            return None
        if tool_calls is None:
            n_tools = sum(1 for s in steps or [] if s.get("name") != "verify")
            tool_calls = logged[-n_tools:] if n_tools else logged
        result = learn_from_turn(question, steps or [], tool_calls, bool(ok), visuals, deps)
        used = [str((c.get("args") or {}).get("skill_id")) for c in tool_calls
                if (c.get("name") or c.get("tool")) == "get_skill" and isinstance(c.get("args"), dict)]
        for sid in dict.fromkeys(used):  # the agent followed a retrieved playbook: credit or blame it too
            if sid and sid != result.get("skill_id") and S.get(sid):
                record_outcome(sid, bool(ok), question if ok else None,
                               None if ok else _pitfall(steps or [], D.steps_from_calls(tool_calls)), deps)
        return result
    except Exception as e:  # noqa: BLE001 - learning must never break a turn
        log.warning("skills.on_turn_complete failed: %s", e)
        return None


def export_skill_md(skill_id: str) -> str | None:
    """Render a skill as an agentskills.io SKILL.md (frontmatter + When to use / Steps / Visual / Pitfalls)."""
    s = S.get(skill_id) or S.get(S.slug(skill_id))
    if not s:
        return None
    import yaml
    fm = {"name": s["name"], "description": s["description"] or "",
          "metadata": {"source": s["source"], "version": s["version"], "uses": s["uses"], "successes": s["successes"],
                       "failures": s["failures"], "success_rate": s["success_rate"], "status": s["status"]}}
    if s["steps"]:
        fm["steps"] = [D.render_step(st) for st in s["steps"]]
    if s.get("visual"):
        fm["visual"] = s["visual"]
    if s["triggers"]:
        fm["triggers"] = s["triggers"][-10:]
    lines = ["---", yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, width=1000).strip(), "---",
             f"# {s['name'].replace('-', ' ').capitalize()} playbook"]
    if s["triggers"]:
        lines += ["", "## When to use"] + [f"- {t}" for t in s["triggers"][-10:]]
    if s["steps"]:
        lines += ["", "## Steps"] + [f"{i}. `{D.render_step(st)}`" for i, st in enumerate(s["steps"], 1)]
        slots = sorted({m for st in s["steps"] for m in re.findall(r"\{[\w.]+\}", json.dumps(st["args"]))})
        if slots:
            lines += ["", "Slots: " + ", ".join(slots) + " (fill from resolve_entity and the question)."]
    if s.get("visual"):
        lines += ["", "## Visual", f"- {s['visual']}"]
    if s["pitfalls"]:
        lines += ["", "## Pitfalls"] + [f"- {p}" for p in s["pitfalls"]]
    if s.get("body") and s["source"] == "manual":
        lines += ["", "## Rules", s["body"].split("\n", 1)[1].strip() if s["body"].startswith("#") else s["body"]]
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["create_skill", "export_skill_md", "find_skills", "get_skill", "learn_from_turn", "list_skills",
           "on_turn_complete", "record_outcome"]
