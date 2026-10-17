"""Smoke test: run real agent turns on 5 eval questions; print tool calls, args, retries and verification.

    uv run python scripts/smoke_turn.py [--model ollama:qwen3-vl-8b-32k] [--only Q1,Q5]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import date

from pydantic_ai.messages import RetryPromptPart, ToolCallPart

from wellops.agent import build_agent, run_turn
from wellops.agent.agent import build_model
from wellops.contracts import RunDeps
from wellops.verify import VERIFICATIONS

QUESTIONS = {
    "Q1": "Is WELL-0001 producing oil right now? When was it last inspected, and by whom? Show me its status card.",
    "Q2": "Which wells in Northeast Block 8 are not producing oil right now, and why? Show them in a table.",
    "Q4": "How many oil spills has WELL-0001 had in the last 4 years, and how much oil in total? List them in a table.",
    "Q5": "How many days was WELL-0001 down in 2025?",
    "Q6": "Which 3 wells in Northeast Block 7 had the most downtime in the last 4 months? Show a bar chart.",
}


def trace(res) -> tuple[list[tuple[str, str]], list[str]]:
    calls, retries = [], []
    for m in res.all_messages():
        for p in getattr(m, "parts", []):
            if isinstance(p, ToolCallPart):
                args = p.args if isinstance(p.args, str) else json.dumps(p.args)
                calls.append((p.tool_name, args))
            elif isinstance(p, RetryPromptPart):
                c = p.content if isinstance(p.content, str) else json.dumps(p.content, default=str)
                retries.append(f"{p.tool_name or '-'}: {c[:400]}")
    return calls, retries


async def one(agent, qid: str, q: str) -> bool:
    print(f"\n=== {qid}: {q}")
    deps = RunDeps(principal="U-201", chat_id=f"smoke-{qid}", today=date(2026, 10, 17))
    t0 = time.time()
    try:
        spec, v, res = await run_turn(agent, deps, q)
    except Exception as e:  # noqa: BLE001 - report and keep going
        print(f"  FAIL ({time.time() - t0:.0f}s) {type(e).__name__}: {str(e)[:600]}")
        cause = e.__cause__ or e.__context__
        if cause:
            print(f"  cause: {type(cause).__name__}: {str(cause)[:600]}")
        for a in VERIFICATIONS.get(deps.chat_id, []):
            print(f"  verify attempt {a.attempt}: ok={a.ok} {a.diff()[:400]}")
        return False
    calls, retries = trace(res)
    for name, args in calls:
        print(f"  call {name} {args[:300]}")
    for r in retries:
        print(f"  retry {r}")
    attempts = VERIFICATIONS.get(deps.chat_id, [])
    for a in attempts:
        print(f"  verify attempt {a.attempt}: ok={a.ok} {a.diff()[:400]}")
    ok = bool(v and v.ok)
    print(f"  {'PASS' if ok else 'FAIL'} ({time.time() - t0:.0f}s) model={res.response.model_name} "
          f"visuals={[x.type for x in spec.visuals]}\n  answer: {spec.markdown[:300]!r}")
    return ok


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", help="comma-separated model specs; default MODEL_FALLBACKS")
    ap.add_argument("--only", help="comma-separated question ids")
    a = ap.parse_args()
    agent = build_agent(build_model(a.model.split(",") if a.model else None))
    qs = {k: v for k, v in QUESTIONS.items() if not a.only or k in a.only.split(",")}
    results = {qid: await one(agent, qid, q) for qid, q in qs.items()}
    print("\nSUMMARY", " ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in results.items()))


if __name__ == "__main__":
    asyncio.run(main())
