"""One chat turn: run the agent, then record the episode (principal-scoped memory)."""

from __future__ import annotations

from typing import Any

from wellops import memory
from wellops.contracts import AnswerSpec, RunDeps
from wellops.tools.core import WELL_RE, store
from wellops.verify import VERIFICATIONS, Verification


def episode_entities(deps: RunDeps, utterance: str, spec: AnswerSpec) -> list[str]:
    ents = list(WELL_RE.findall(utterance.upper()))
    st = store()
    for h in {c.handle for c in spec.claims} | {v.handle for v in spec.visuals if v.handle}:
        s = st.get(deps.chat_id, h)
        if s:
            ents += [r["asset_id"] for r in s.rows[:25] if isinstance(r.get("asset_id"), str)]
    for c in spec.claims:
        if c.row_key and isinstance(c.row_key.get("asset_id"), str):
            ents.append(c.row_key["asset_id"])
    return list(dict.fromkeys(ents))


def record(deps: RunDeps, utterance: str, spec: AnswerSpec) -> str:
    refs = sorted({c.handle for c in spec.claims} | {v.handle for v in spec.visuals if v.handle})
    return memory.record_episode(deps, utterance, episode_entities(deps, utterance, spec), refs, spec.markdown[:300])


async def run_turn(agent, deps: RunDeps, prompt: str, history: list | None = None, **kw: Any) -> tuple[AnswerSpec, Verification | None, Any]:
    res = await agent.run(prompt, deps=deps, message_history=history, **kw)
    record(deps, prompt, res.output)
    v = (VERIFICATIONS.get(deps.chat_id) or [None])[-1]
    return res.output, v, res
