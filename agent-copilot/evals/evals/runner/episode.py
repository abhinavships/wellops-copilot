"""Run an episode (multi-turn, multi-principal, simulated clock) against a target and score every turn."""
from __future__ import annotations

import time
import traceback
from dataclasses import asdict

from evals.metrics.scorers import aggregate, score_turn


def run_case(case: dict, target, run_idx: int = 0) -> dict:
    turns = []
    for i, t in enumerate(case["turns"]):
        t0 = time.time()
        try:
            answer, trace = target.respond(case, t)
            err = None
        except Exception as e:  # a target crash is a failed turn, not a crashed eval
            answer, trace, err = {"markdown": ""}, [], f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}"
        rec = dict(turn_id=t.get("id", f"t{i + 1}"), user=t["user"], chat=t["chat"], clock=t["clock"], say=t["say"],
                   setup=bool(t.get("setup")), expect=t.get("expect") or {}, truth=t.get("truth"), answer=answer, trace=trace,
                   latency_s=round(time.time() - t0, 3), error=err)
        if not rec["setup"]:
            sc = score_turn(answer, rec["expect"], trace)
            rec["scores"] = {k: dict(value=v.value, reason=v.reason, details=v.details) for k, v in sc.items()}
            rec["agg"] = aggregate(sc)
            if err:
                rec["agg"] = dict(score=0.0, passed=False, leak=False)
        turns.append(rec)
    scored = [t for t in turns if not t["setup"]]
    return dict(case_id=case["id"], suite=case["suite"], title=case["title"], run=run_idx, turns=turns,
                score=sum(t["agg"]["score"] for t in scored) / len(scored) if scored else 1.0,
                passed=all(t["agg"]["passed"] for t in scored), leak=any(t["agg"]["leak"] for t in scored))
