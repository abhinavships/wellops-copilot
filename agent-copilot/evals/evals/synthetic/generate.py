"""Seeded generator of unseen-style questions with ground truth computed from the substrate (templates x entities x windows).

Writes cases/synthetic.yaml in the same schema as the golden cases. The expectations are derived from the truth
functions, so a synthetic case can never disagree with the substrate (it tests the agent, not the key).
"""
from __future__ import annotations

import argparse
import random
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import yaml

from evals import CASES_DIR
from evals.cases import validate_case
from evals.truth.compute import compute
from evals.truth.db import DS, get_con, q

TODAY = "2026-10-17"
TEAM_USER = {"TEAM-SW": "U-101", "TEAM-NE": "U-201"}
AREAS = {"SW-23": "Southwest Block 23", "SW-24": "Southwest Block 24", "NE-07": "Northeast Block 7", "NE-08": "Northeast Block 8"}
MONTH = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def _user(area_name: str) -> str:
    return "U-101" if area_name.startswith("Southwest") else "U-201"


def _expect_from_truth(tr: dict, visual: list[str], number_filter=None, cite_n: int = 4, dates=True) -> dict:
    ex: dict = {"visual": visual}
    nums = [{"label": k, "value": float(v), "tol": 0.05 if not float(v).is_integer() else 0}
            for k, v in tr["numbers"].items() if not number_filter or number_filter(k)]
    if nums:
        ex["numbers"] = nums
    if dates and tr["dates"]:
        ex["dates"] = tr["dates"]
    if tr["cites"]:
        ex["must_cite"] = tr["cites"][:cite_n]
    return ex


def _case(cid: str, title: str, tags: list[str], user: str, say: str, fn: str, args: dict, expect: dict) -> dict:
    return validate_case(dict(
        id=cid, suite="synthetic", title=title, tags=["synthetic"] + tags, source="evals.synthetic.generate",
        turns=[dict(user=user, chat=f"chat-{cid.lower()}", clock=TODAY, say=say, truth=dict(fn=fn, args=args), expect=expect)]), cid)


def generate(n: int = 72, seed: int = 7) -> list[dict]:
    rnd = random.Random(seed)
    con = get_con()
    wells = [r[0] for r in q(con, "SELECT asset_id FROM wells WHERE dataset_id=? ORDER BY 1", DS)]
    by_area = {a: [r[0] for r in q(con, "SELECT asset_id FROM wells WHERE dataset_id=? AND area_id=? ORDER BY 1", DS, a)] for a in AREAS}
    cases, seen = [], set()

    def add(c):
        if c and c["id"] not in seen:
            seen.add(c["id"])
            cases.append(c)

    def tmpl_top_downtime(i):
        area = rnd.choice(list(AREAS.values()))
        k, d = rnd.choice([2, 3, 5]), rnd.choice([30, 60, 90, 120, 180])
        since = (date.fromisoformat(TODAY) - timedelta(days=d)).isoformat()
        args = dict(area=area, k=k, since=since, until=TODAY)
        tr = compute(dict(fn="top_downtime", args=args))
        full = compute(dict(fn="top_downtime", args=dict(args, k=50)))
        days = [v for kk, v in full["numbers"].items() if kk.startswith("days:")]
        if len(days) <= k or days[k - 1] == days[k] or len(tr["set"]) < k:  # boundary tie => ambiguous truth
            return None
        ex = _expect_from_truth(tr, ["bar"])
        ex["set_equals"] = tr["set"]
        return _case(f"S-TOPDT-{i:03d}", f"Top {k} downtime in {area}, last {d} days", ["downtime", "bar"], _user(area),
                     f"Which {k} wells in {area} had the most downtime in the last {d} days? Show a bar chart.",
                     "top_downtime", args, ex)

    def tmpl_heatmap(i):
        year = rnd.choice([2023, 2024, 2025, 2026])
        tr = compute(dict(fn="spills_heatmap", args=dict(year=year)))
        keys = sorted(k for k in tr["numbers"] if k.startswith("total|"))
        cells = rnd.sample([k for k in tr["numbers"] if not k.startswith("total|")], 6)
        ex = {"visual": ["heatmap"], "numbers": [{"label": k, "value": tr["numbers"][k], "tol": 0} for k in keys + cells],
              "text": tr["text"]}
        return _case(f"S-HEAT-{i:03d}", f"Spills by area x month {year}", ["spills", "heatmap"], "U-201",
                     f"Show a heatmap of {year} spills by geographic area and month.", "spills_heatmap", dict(year=year), ex)

    def tmpl_status(i):
        w = rnd.choice(wells)
        tr = compute(dict(fn="well_status", args=dict(well=w)))
        ex = _expect_from_truth(tr, ["status_card"])
        ex["ids_exact"] = tr["ids"]
        ex["text"] = tr["text"]
        a = q(con, "SELECT team_id FROM wells WHERE dataset_id=? AND asset_id=?", DS, w)[0][0]
        return _case(f"S-STATUS-{i:03d}", f"Is {w} producing right now", ["status", "card"], TEAM_USER.get(a, "U-201"),
                     f"Is {w} producing oil right now? When was it last inspected, and by whom? Show me its status card.",
                     "well_status", dict(well=w), ex)

    def tmpl_cost(i):
        area = rnd.choice(list(AREAS))
        ws = sorted(rnd.sample(by_area[area], 3))
        year = rnd.choice([2024, 2025, 2026])
        tr = compute(dict(fn="repair_costs", args=dict(wells=ws, year=year)))
        ex = _expect_from_truth(tr, ["table", "bar"])
        ex["set_equals"] = ws
        for n in ex["numbers"]:
            n["tol"] = 0.5
        return _case(f"S-COST-{i:03d}", f"Repair cost for 3 wells in {year}", ["costs", "table"], _user(AREAS[area]),
                     f"What did repairs cost in {year} for {', '.join(ws[:-1])} and {ws[-1]}?", "repair_costs", dict(wells=ws, year=year), ex)

    def tmpl_breach(i):
        area = rnd.choice(list(AREAS.values()))
        d = rnd.choice([60, 90, 120, 180])
        args = dict(area=area, days=d, today=TODAY)
        tr = compute(dict(fn="containment_breaches", args=args))
        if not tr["set"]:
            return None
        ex = _expect_from_truth(tr, ["table"])
        ex["set_equals"] = tr["set"]
        return _case(f"S-BREACH-{i:03d}", f"SR-02 containment breaches in {area}, {d} days", ["compliance", "report-only-facts"],
                     _user(area), f"Which wells in {area} breached the SOP containment time limit (SR-02) in the last {d} days?",
                     "containment_breaches", args, ex)

    def tmpl_spills_well(i):
        w = rnd.choice(wells)
        yrs = rnd.choice([1, 2, 3])
        args = dict(well=w, years=yrs, today=TODAY)
        tr = compute(dict(fn="spills_well", args=args))
        ex = _expect_from_truth(tr, ["table"], cite_n=3)
        return _case(f"S-SPILLW-{i:03d}", f"Spills of {w}, last {yrs}y", ["spills", "table"], "U-201",
                     f"How many oil spills has {w} had in the last {yrs} years, and how much oil in total? List them in a table.",
                     "spills_well", args, ex)

    def tmpl_dt_year(i):
        w = rnd.choice(wells)
        y = rnd.choice([2024, 2025])
        tr = compute(dict(fn="downtime_days", args=dict(well=w, year=y)))
        ex = _expect_from_truth(tr, ["none", "table", "line"], cite_n=3)
        return _case(f"S-DTYEAR-{i:03d}", f"Downtime of {w} in {y}", ["downtime"], "U-201", f"How many days was {w} down in {y}?",
                     "downtime_days", dict(well=w, year=y), ex)

    def tmpl_notprod(i):
        area = rnd.choice(list(AREAS.values()))
        tr = compute(dict(fn="area_not_producing", args=dict(area=area, today=TODAY)))
        ex = _expect_from_truth(tr, ["table"], cite_n=3)
        ex["set_equals"] = tr["set"]
        return _case(f"S-NOTPROD-{i:03d}", f"Not producing now in {area}", ["status", "table"], _user(area),
                     f"Which wells in {area} are not producing oil right now? Show them in a table.", "area_not_producing",
                     dict(area=area, today=TODAY), ex)

    def tmpl_map(i):
        area = rnd.choice(list(AREAS.values()))
        d = rnd.choice([30, 60, 120, 180])
        args = dict(area=area, days=d, today=TODAY)
        tr = compute(dict(fn="spills_map", args=args))
        if not tr["set"]:
            return None
        ex = _expect_from_truth(tr, ["map"], cite_n=3)
        ex["set_equals"] = tr["set"]
        return _case(f"S-MAP-{i:03d}", f"Spill map {area}, {d} days", ["spills", "map"], _user(area),
                     f"Show on a map every well in {area} with a real spill in the last {d} days.", "spills_map", args, ex)

    def tmpl_rca(i):
        area = rnd.choice(list(AREAS.values()))
        eq = rnd.choice(["pump_jack", "flowline", "wellhead_valve"])
        args = dict(area=area, equipment=eq, today=TODAY)
        tr = compute(dict(fn="rca_candidates", args=args))
        if not tr["set"]:
            return None
        ex = _expect_from_truth(tr, ["table"], cite_n=3)
        ex["set_equals"] = tr["set"]
        return _case(f"S-RCA-{i:03d}", f"RCA candidates {eq} in {area}", ["rca", "table"], _user(area),
                     f"Which wells in {area} need a root-cause analysis on their {eq.replace('_', ' ')}?", "rca_candidates", args, ex)

    tmpls = [tmpl_top_downtime, tmpl_heatmap, tmpl_status, tmpl_cost, tmpl_breach, tmpl_spills_well, tmpl_dt_year,
             tmpl_notprod, tmpl_map, tmpl_rca]
    i = 0
    guard = 0
    while len(cases) < n and guard < n * 20:
        t = tmpls[guard % len(tmpls)]
        guard += 1
        try:
            add(t(i := i + 1))
        except (KeyError, IndexError):
            continue
    return cases


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=72)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", type=Path, default=CASES_DIR / "synthetic.yaml")
    a = ap.parse_args()
    cs = generate(a.n, a.seed)
    a.out.write_text("# GENERATED by evals.synthetic.generate (seed=%d). Truth computed from the substrate.\n" % a.seed
                     + yaml.safe_dump(cs, sort_keys=False, width=140))
    print(len(cs), "cases", dict(Counter(c["id"].split("-")[1] for c in cs)), "->", a.out)


if __name__ == "__main__":
    main()
