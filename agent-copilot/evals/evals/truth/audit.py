"""Key audit: recompute each expectation from the substrate and compare with the YAML key.

Status per expectation item: agrees | disagrees_with_key | not_computable.
disagrees_with_key is triaged by a human (our bug / key error / ambiguity), never auto-fixed.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from evals.cases import load_cases
from evals.truth.compute import compute

AGREES, DISAGREES, NC = "agrees", "disagrees_with_key", "not_computable"
BEHAVIOUR = ["must_flag_conflict", "must_disclose_overlay", "must_not_contain", "must_say_unconfirmed",
             "must_say_no_prior_conversation", "memory_write", "max_steps", "tools_any"]


def _kind(i: str) -> str:
    return re.match(r"[A-Za-z]+", i).group(0) if re.match(r"[A-Za-z]+", i) else ""


def _item(case, turn, kind, item, expected, computed, status, note=""):
    return dict(case=case, turn=turn, kind=kind, item=item, expected=expected, computed=computed, status=status, note=note)


def audit_turn(cid: str, t: dict, tr: dict | None) -> list[dict]:
    tid = t.get("id", "t")
    ex = t.get("expect") or {}
    out = []
    pool = set(tr["ids"] + tr["cites"] + tr["set"]) if tr else set()

    def nc(kind, item, note="no computable ground truth for this expectation"):
        out.append(_item(cid, tid, kind, item, item, None, NC, note))

    se = ex.get("set_equals")
    if se is not None:
        ids = se["ids"] if isinstance(se, dict) else se
        if tr is None:
            nc("set_equals", ids)
        else:
            comp = set(tr["set"]) - set(se.get("allowed_extra", []) if isinstance(se, dict) else [])
            ok = set(ids) == comp
            out.append(_item(cid, tid, "set_equals", sorted(ids), sorted(ids), sorted(comp), AGREES if ok else DISAGREES))
    for i in ex.get("ids_exact", []):
        if tr is None or not pool:
            nc("ids_exact", i)
        elif i in pool:
            out.append(_item(cid, tid, "ids_exact", i, i, i, AGREES))
        elif any(_kind(p) == _kind(i) for p in tr["ids"]) and tr["ids"]:
            out.append(_item(cid, tid, "ids_exact", i, i, tr["ids"], DISAGREES))
        else:
            nc("ids_exact", i)
    for n in ex.get("numbers", []):
        lab = n["label"]
        v = None if tr is None else tr["numbers"].get(lab)
        if v is None and tr is not None and "|" in lab and not lab.startswith("total"):
            a = lab.split("|")[0]
            if any(k.startswith(a + "|") for k in tr["numbers"]):
                v = 0  # heatmap cell absent => zero spills
        if v is None:
            out.append(_item(cid, tid, "number", lab, n["value"], None, NC))
        else:
            ok = abs(v - n["value"]) <= max(n.get("tol", 0), 1e-9)
            out.append(_item(cid, tid, "number", lab, n["value"], v, AGREES if ok else DISAGREES))
    dates = ex.get("dates", [])
    if dates:
        tdates = set(tr["dates"]) if tr else set()
        same_size = tr is not None and len(tdates) == len(dates)
        for d in dates:
            if d in tdates:
                out.append(_item(cid, tid, "date", d, d, d, AGREES))
            elif same_size:
                out.append(_item(cid, tid, "date", d, d, sorted(tdates), DISAGREES))
            else:
                out.append(_item(cid, tid, "date", d, d, None, NC))
    for c in ex.get("must_cite", []):
        out.append(_item(cid, tid, "must_cite", c, c, None, AGREES if c in pool else NC,
                         "" if c in pool else "citation not derivable from computed truth"))
    low = {x.lower() for x in (tr["text"] if tr else [])}
    for s in ex.get("text", []):
        out.append(_item(cid, tid, "text", s, s, None, AGREES if s.lower() in low else NC))
    if ex.get("visual"):
        tv = tr["visual"] if tr else None
        if tv:
            out.append(_item(cid, tid, "visual", ex["visual"], ex["visual"], tv, AGREES if tv in ex["visual"] else DISAGREES))
        else:
            nc("visual", ex["visual"])
    for b in BEHAVIOUR:
        if b in ex:
            nc(b, ex[b], "scenario/memory behaviour: verified by running the agent, not by SQL")
    return out


def audit(cases: list[dict] | None = None) -> dict:
    cases = cases if cases is not None else load_cases("golden")
    items = []
    for c in cases:
        for t in c["turns"]:
            if t.get("setup"):
                continue
            items += audit_turn(c["id"], t, compute(t.get("truth")))
    counts = Counter(i["status"] for i in items)
    by_case = {}
    for i in items:
        by_case.setdefault(i["case"], Counter())[i["status"]] += 1
    return dict(summary=dict(counts), by_case={k: dict(v) for k, v in by_case.items()}, items=items)


def to_markdown(rep: dict) -> str:
    L = ["# Key audit (expected answers are hypotheses)", "",
         "Totals: " + ", ".join(f"{k}={v}" for k, v in sorted(rep["summary"].items())), "",
         "| case | agrees | disagrees_with_key | not_computable |", "|---|---|---|---|"]
    for c, v in rep["by_case"].items():
        L.append(f"| {c} | {v.get(AGREES, 0)} | {v.get(DISAGREES, 0)} | {v.get(NC, 0)} |")
    dis = [i for i in rep["items"] if i["status"] == DISAGREES]
    L += ["", "## disagrees_with_key (human triage: our bug / key error / ambiguity)", ""]
    if not dis:
        L.append("None.")
    for i in dis:
        L.append(f"- {i['case']}/{i['turn']} {i['kind']} `{i['item']}`: key={i['expected']} computed={i['computed']}")
    return "\n".join(L) + "\n"


def main() -> None:
    rep = audit()
    print(to_markdown(rep))
    from evals import RESULTS_DIR
    RESULTS_DIR.mkdir(exist_ok=True)
    (RESULTS_DIR / "key_audit.json").write_text(json.dumps(rep, indent=1, default=str))
    (RESULTS_DIR / "key_audit.md").write_text(to_markdown(rep))


if __name__ == "__main__":
    main()
