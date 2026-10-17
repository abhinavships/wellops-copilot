"""Per-principal episodic memory. The principal filter is applied in SQL from RunDeps, never from model args."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, time, timezone

from wellops.contracts import RunDeps
from wellops.tools.core import ToolOut, finish, get_db
from wellops.tools.entity import NUMW, parse_dates, resolve

TYPE_WORDS = {"well": "WELL", "spill": "INS", "inspection": "INS", "repair": "MNT", "maintenance": "MNT", "event": "REG"}


def record_episode(deps: RunDeps, utterance: str, entities: list[str], result_refs: list[str], summary: str,
                   ts: datetime | None = None) -> str:
    db = get_db()
    eid = "E-" + uuid.uuid4().hex[:8]
    if ts is None:
        w = datetime.now(timezone.utc)
        ts = datetime.combine(deps.today, w.time(), tzinfo=timezone.utc)
    ents = list(dict.fromkeys(entities))[:40]
    db.run("INSERT INTO episodes (episode_id, principal, chat_id, ts, utterance, entities, result_refs, summary) "
           "VALUES (?,?,?,?,?,?,?,?)", [eid, deps.principal, deps.chat_id, ts, utterance, ents, result_refs, summary])
    return eid


def recall_episodes(deps: RunDeps, query: str, when: str | None = None, limit: int = 5) -> ToolOut:
    """Past turns of THIS user only, scored by time match + cardinality + entity type + keyword overlap."""
    db = get_db()
    eps = db.rows("""SELECT episode_id, chat_id, ts, utterance, entities, result_refs, summary FROM episodes
                     WHERE principal = ? AND NOT forgotten AND ts <= ?::TIMESTAMPTZ ORDER BY ts DESC LIMIT 500""",
                  [deps.principal, deps.asof])
    text = f"{query} {when or ''}"
    wins = [w for w in parse_dates(text, deps.today)]
    if re.search(r"\blast week\b|\bearlier this week\b", text.lower()):
        pass
    want_n = None
    m = re.search(rf"\b(\d+|{'|'.join(k for k in NUMW if k not in ('a', 'an'))})\s+(wells?|spills?|repairs?|events?|inspections?)",
                  text.lower())
    types = {TYPE_WORDS[t] for t in TYPE_WORDS if re.search(rf"\b{t}s?\b", text.lower())}
    if m:
        want_n = int(m[1]) if m[1].isdigit() else NUMW[m[1]]
    ents = {r["id"] for r in resolve(db, deps, query) if r["kind"] in ("well", "area", "inspection", "maintenance")}
    kw = {w for w in re.findall(r"[a-z]{5,}", query.lower())} - {"discussed", "yesterday", "wells", "about", "earlier", "asked"}
    rows = []
    for e in eps:
        day = str(e["ts"])[:10]
        t_score = 0.0
        for w in wins:
            if w["start"] <= day <= w["end"]:
                t_score = 1.0
        if wins and not t_score:
            continue  # asked about a specific time and nothing happened then: return nothing, don't guess
        es = [x for x in (e["entities"] or [])]
        n_wells = sum(1 for x in es if x.startswith("WELL-"))
        score = 2 * t_score
        if want_n and ((n_wells == want_n) or (len(es) == want_n)):
            score += 1.0
        if types and any(x.startswith(tuple(types)) for x in es):
            score += 0.5
        score += min(1.0, 0.5 * len(ents & set(es)))
        score += min(0.6, 0.2 * sum(1 for k in kw if k in (e["utterance"] or "").lower() + (e["summary"] or "").lower()))
        if score > 0:
            rows.append({**e, "score": round(score, 2)})
    rows.sort(key=lambda r: (-r["score"], r["ts"]), reverse=False)
    rows = rows[:limit]
    cols = ["episode_id", "chat_id", "ts", "utterance", "entities", "result_refs", "summary", "score"]
    notes = [f"only conversations of principal {deps.principal} are searched"]
    if not rows:
        notes.append("no matching earlier conversation for this user - say so; do not guess or use anyone else's history")
    return finish(db, deps, "entity_list", cols, rows, sql=None, notes=notes)
