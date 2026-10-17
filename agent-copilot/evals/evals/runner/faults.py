"""Meta-eval: inject known faults into good answers and assert the right scorer catches each one."""
from __future__ import annotations

import copy
import re
from typing import Callable

from evals.metrics.scorers import SCORERS, answer_text

BAD_VISUAL = {"table": "line", "bar": "map", "line": "table", "map": "bar", "heatmap": "table", "status_card": "heatmap",
              "none": "heatmap"}


def _replace_everywhere(ans: dict, old: str, new: str) -> None:
    ans["markdown"] = ans["markdown"].replace(old, new)
    for c in ans.get("claims", []):
        c["text"] = str(c.get("text", "")).replace(old, new)
        if str(c.get("value")) == old:
            c["value"] = new


def swap_number(ans, trace, ex):
    for n in ex.get("numbers", []):
        v = n["value"]
        for s in {f"{v:,.0f}", str(v), f"{v:g}", f"{v:.1f}"}:
            if s and re.search(rf"(?<![\d.]){re.escape(s)}(?![\d])", ans["markdown"]):
                wrong = f"{v * 7 + 13:g}"
                ans["markdown"] = re.sub(rf"(?<![\d.]){re.escape(s)}(?![\d])", wrong, ans["markdown"])
                return ans, trace
    return None


def drop_set_member(ans, trace, ex):
    se = ex.get("set_equals")
    ids = se["ids"] if isinstance(se, dict) else se
    if not ids or len(ids) < 2:
        return None
    ans["markdown"] = ans["markdown"].replace(ids[0], "WELL-9999" if ids[0].startswith("WELL") else "X-0000")
    return ans, trace


def drop_date(ans, trace, ex):
    from evals.metrics.scorers import has_date
    from evals.runner.targets import fmt_date
    for d in ex.get("dates", []):
        if has_date(ans["markdown"], d):
            ans["markdown"] = ans["markdown"].replace(fmt_date(d), "an earlier day").replace(d, "an earlier day")
            return ans, trace
    return None


def drop_conflict(ans, trace, ex):
    if not (ex.get("must_flag_conflict") or {}).get("required"):
        return None
    ans["conflicts"] = []
    return ans, trace


def leak_other_user(ans, trace, ex):
    if not ex.get("must_not_contain"):
        return None
    ans["markdown"] += f"\n(From another user's earlier chat: {ex['must_not_contain'][0]})"
    return ans, trace


def wrong_visual(ans, trace, ex):
    if not ex.get("visual"):
        return None
    cur = [v["type"] for v in ans.get("visuals", [])] or ["none"]
    bad = next((t for t in ("heatmap", "map", "bar", "line", "table", "status_card") if t not in ex["visual"]), None)
    ans["visuals"] = [dict(type=bad, handle="r1")]
    return ans, trace


def drop_citation(ans, trace, ex):
    must = ex.get("must_cite")
    if not must:
        return None
    ans["citations"] = [c for c in ans.get("citations", []) if c["id"] != must[0]]
    return ans, trace


def cite_unrelated_well(ans, trace, ex):
    if not ex.get("must_cite"):
        return None
    ans["citations"] = ans.get("citations", []) + [dict(kind="inspection", id="INS-9999-0001"),
                                                  dict(kind="maintenance", id="MNT-9998-0002")]
    return ans, trace


def drop_overlay_disclosure(ans, trace, ex):
    who = ex.get("must_disclose_overlay")
    if not who:
        return None
    ans["markdown"] = ans["markdown"].replace(who, "someone")
    ans["applied_overlays"] = []
    ans["conflicts"] = [{**c, "sides": [], "subject": "x", "ask": None} for c in ans.get("conflicts", [])]
    return ans, trace


def drop_unconfirmed(ans, trace, ex):
    if not ex.get("must_say_unconfirmed"):
        return None
    ans["markdown"] = re.sub(r"unconfirmed|not confirmed|unverified", "accepted", ans["markdown"], flags=re.I)
    for c in ans.get("conflicts", []):
        c["resolution"] = "unresolved"
        c["ask"] = None
    return ans, trace


def drop_no_prior(ans, trace, ex):
    if not ex.get("must_say_no_prior_conversation"):
        return None
    ans["markdown"] = "Here are the numbers you asked for."
    return ans, trace


def drop_memory_write(ans, trace, ex):
    if not ex.get("memory_write"):
        return None
    return ans, [c for c in trace if c["name"] != "assert_fact"]


def wrong_memory_value(ans, trace, ex):
    mw = ex.get("memory_write")
    if not mw or not isinstance(mw.get("value"), (int, float)):
        return None
    for c in trace:
        if c["name"] == "assert_fact":
            c["args"]["value_num"] = mw["value"] + 5
    return ans, trace


def use_downtime_hours(ans, trace, ex):
    trace.append(dict(name="sql", args={"query": "SELECT sum(downtime_hours)/24 FROM maintenance"}, result={}))
    return ans, trace


def too_many_steps(ans, trace, ex):
    trace += [dict(name="sql", args={"query": "SELECT 1"}, result={}) for _ in range(30)]
    return ans, trace


# fault name -> (scorer expected to catch it, injector)
FAULTS: dict[str, tuple[str, Callable]] = {
    "swap_number": ("facts", swap_number),
    "drop_set_member": ("facts", drop_set_member),
    "drop_date": ("facts", drop_date),
    "drop_conflict": ("conflict", drop_conflict),
    "leak_other_user_data": ("leak", leak_other_user),
    "wrong_visual": ("visual", wrong_visual),
    "drop_citation": ("evidence", drop_citation),
    "cite_unrelated_well": ("evidence", cite_unrelated_well),
    "drop_overlay_disclosure": ("overlay", drop_overlay_disclosure),
    "drop_unconfirmed": ("unconfirmed", drop_unconfirmed),
    "drop_no_prior": ("no_prior", drop_no_prior),
    "drop_memory_write": ("memory", drop_memory_write),
    "wrong_memory_value": ("memory", wrong_memory_value),
    "use_downtime_hours": ("trajectory", use_downtime_hours),
    "too_many_steps": ("trajectory", too_many_steps),
}


def meta_eval(cases: list[dict], target) -> dict:
    """For every (scored turn, fault) where the fault is applicable: scorer must drop below 1 (leak must hit 0)."""
    per_fault: dict[str, dict] = {k: dict(applied=0, caught=0, missed=[]) for k in FAULTS}
    baseline_flags = 0
    n_base = 0
    for c in cases:
        for t in c["turns"]:
            if t.get("setup"):
                continue
            ans, trace = target.respond(c, t)
            ex = t.get("expect") or {}
            base = {n: f(ans, ex, trace) for n, f in SCORERS.items()}
            n_base += 1
            baseline_flags += any(s.applies and s.value < 0.999 for s in base.values())
            for fname, (scorer, inj) in FAULTS.items():
                res = inj(copy.deepcopy(ans), copy.deepcopy(trace), ex)
                if res is None:
                    continue
                a2, tr2 = res
                if not base[scorer].applies or base[scorer].value < 0.999:
                    continue  # scorer not applicable / baseline already imperfect: not a clean test
                s = SCORERS[scorer](a2, ex, tr2)
                per_fault[fname]["applied"] += 1
                if s.applies and s.value < 0.999:
                    per_fault[fname]["caught"] += 1
                else:
                    per_fault[fname]["missed"].append(f"{c['id']}/{t.get('id', '?')}")
    per_scorer: dict[str, dict] = {}
    for fname, d in per_fault.items():
        sc = FAULTS[fname][0]
        agg = per_scorer.setdefault(sc, dict(applied=0, caught=0))
        agg["applied"] += d["applied"]
        agg["caught"] += d["caught"]
    for sc, d in per_scorer.items():
        d["recall"] = d["caught"] / d["applied"] if d["applied"] else None
    return dict(per_fault=per_fault, per_scorer=per_scorer, baseline_false_alarm_rate=baseline_flags / n_base if n_base else None,
                turns=n_base)
