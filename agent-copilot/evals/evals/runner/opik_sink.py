"""Opik integration: datasets, experiments (evaluate), per-turn traces with thread_id = chat id, feedback scores.

Only active when OPIK_API_KEY and OPIK_WORKSPACE are set (loaded from agent-copilot/.env). Never prints the key.
"""
from __future__ import annotations

import os
from typing import Any, Callable

from evals import REPO_ROOT

PROJECT = os.environ.get("OPIK_PROJECT_NAME", "wellops-copilot")


def load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(REPO_ROOT / ".env", override=False)
    except ImportError:  # pragma: no cover
        pass


def opik_enabled() -> bool:
    load_env()
    return bool(os.environ.get("OPIK_API_KEY") and os.environ.get("OPIK_WORKSPACE"))


def get_client(project: str = PROJECT):
    """Configured cloud client, or None when keys are absent (caller falls back to offline JSON)."""
    if not opik_enabled():
        return None
    import opik
    opik.configure(use_local=False, api_key=os.environ["OPIK_API_KEY"], workspace=os.environ["OPIK_WORKSPACE"],
                   force=True, automatic_approvals=True)
    return opik.Opik(project_name=project)


def case_to_item(case: dict) -> dict:
    return dict(case_id=case["id"], suite=case["suite"], title=case["title"], tags=case.get("tags", []),
                origin=case.get("source", ""), n_turns=len(case["turns"]), episode=case)


def push_dataset(client, name: str, cases: list[dict]):
    ds = client.get_or_create_dataset(name=name, description="WellOps copilot eval episodes (multi-turn, multi-principal)")
    ds.insert([case_to_item(c) for c in cases])
    return ds


def log_turn_traces(client, case_result: dict, project: str, experiment: str) -> None:
    """One trace per turn; every chat is an Opik thread (thread_id = chat id), so multi-turn / multi-user episodes read as conversations."""
    for t in case_result["turns"]:
        fb = []
        if not t["setup"]:
            fb = [dict(name=f"wellops_{k}", value=float(v["value"]), reason=(v["reason"] or "")[:500])
                  for k, v in t["scores"].items() if v["value"] is not None]
            fb.append(dict(name="wellops_aggregate", value=float(t["agg"]["score"])))
        tr = client.trace(
            name=f"{case_result['case_id']}:{t['turn_id']}", project_name=project, thread_id=t["chat"],
            input=dict(user_id=t["user"], clock=t["clock"], say=t["say"]),
            output=dict(markdown=t["answer"].get("markdown", ""), visuals=t["answer"].get("visuals", []),
                        conflicts=t["answer"].get("conflicts", [])),
            metadata=dict(case_id=case_result["case_id"], setup=t["setup"], experiment=experiment, run=case_result["run"],
                          latency_s=t["latency_s"]),
            tags=["wellops-eval", case_result["suite"]], feedback_scores=fb or None)
        for c in t["trace"]:
            tr.span(name=str(c.get("name")), type="tool", input={"args": c.get("args")}, output={"result": c.get("result")})
        tr.end()


def run_experiment(client, *, dataset_name: str, cases: list[dict], runner: Callable[[dict, int], dict], k: int,
                   experiment_name: str, project: str, config: dict) -> tuple[Any, list[dict]]:
    import opik
    from opik.evaluation import evaluate
    from evals.metrics.opik_metrics import all_metrics

    ds = push_dataset(client, dataset_name, cases)
    by_id = {c["id"]: c for c in cases}
    results: list[dict] = []
    counter: dict[str, int] = {}

    def task(item: dict) -> dict:
        cid = item["case_id"]
        run_idx = counter.get(cid, 0)
        counter[cid] = run_idx + 1
        res = runner(by_id[cid], run_idx)
        results.append(res)
        try:
            opik.opik_context.update_current_trace(thread_id=res["turns"][0]["chat"], tags=["wellops-eval", res["suite"]],
                                                  metadata=dict(case_id=cid, run=run_idx))
        except Exception:
            pass
        log_turn_traces(client, res, project, experiment_name)
        last = [t for t in res["turns"] if not t["setup"]][-1]
        return dict(turns=res["turns"], output=last["answer"].get("markdown", ""), case_passed=res["passed"])

    ev = evaluate(dataset=ds, task=task, scoring_metrics=all_metrics(), experiment_name=experiment_name,
                  project_name=project, experiment_config=config, trial_count=k, task_threads=1, verbose=0)
    client.flush()
    return ev, results
