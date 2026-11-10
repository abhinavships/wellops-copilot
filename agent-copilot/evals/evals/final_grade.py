"""Grade a final-questions run against facts computed independently from the raw test-data files.

    uv run python -m evals.final_grade evals/results/final-<stamp>/answers.json
Each check is (question, description, predicate over the answer text + visual rows). Prints PASS/FAIL per check.
"""

from __future__ import annotations

import json
import re
import sys


def blob(r: dict) -> str:
    rows = [json.dumps(v.get("data"), default=str) for v in r.get("visuals", [])]
    return (r.get("answer", "") + " " + " ".join(rows)).lower()


def has(*xs):
    return lambda t, a: all(x.lower() in t for x in xs)


def lacks(*xs):
    return lambda t, a: not any(x.lower() in a.lower() for x in xs)


def num_near(word: str, n: int):
    return lambda t, a: bool(re.search(rf"\b{n}\b[^.]{{0,40}}{word}|{word}[^.]{{0,40}}\b{n}\b", a, re.I))


CHECKS = [
    ("Q1", "says pressure data does not exist", lambda t, a: bool(re.search(r"(no|not|does not|doesn't|isn't)[^.]{0,60}pressure|pressure[^.]{0,60}(not (recorded|tracked|available)|does not exist)", a, re.I))),
    ("Q2", "says oil volumes are not recorded", lambda t, a: bool(re.search(r"(not|no)[^.]{0,60}(volume|barrels|production)", a, re.I))),
    ("Q3", "26 gas reports", lambda t, a: "26" in a),
    ("Q3", "9 missed", num_near("miss", 9)),
    ("Q3", "WELL-0332 had no response", has("well-0332")),
    ("Q4", "WELL-0405 not approved, still in", has("well-0405", "not approved")),
    ("Q4", "WELL-0249 expired", has("well-0249", "expired")),
    ("Q4", "WELL-0248 allowed", has("well-0248", "allowed")),
    ("Q4", "past violations WELL-0087, WELL-0158, WELL-0561", has("well-0087", "well-0158", "well-0561")),
    ("Q5", "non-compliant WELL-0123, WELL-0182, WELL-0262", has("well-0123", "well-0182", "well-0262")),
    ("Q5", "compliant WELL-0119, WELL-0168, WELL-0277", has("well-0119", "well-0168", "well-0277")),
    ("Q6", "15 wells", lambda t, a: "15" in a),
    ("Q6", "shut-in notes win: WELL-0044, 0323, 0422, 0481", has("well-0044", "well-0323", "well-0422", "well-0481")),
    ("Q6", "restart = producing (unconfirmed)", has("unconfirmed")),
    ("Q7.1", "ranked table of 10 wells with quotes", lambda t, a: len(set(re.findall(r"well-\d{4}", t))) >= 10),
    ("Q8.1", "WELL-0333 recorded as replaced, attributed", has("well-0333")),
    ("Q8.3", "WELL-0333 shown as replaced per Nathan (unconfirmed)", has("well-0333", "nathan")),
    ("Q8.4", "hearsay about WELL-0577 not applied", lambda t, a: "well-0577" in t and bool(re.search(r"second-?hand|cannot|can't|not (applied|removed|taken off)|needs?|confirm", a, re.I))),
    ("Q8.5", "Nathan's area: WELL-0305, 0426, 0428 in the ground", has("well-0305", "well-0426", "well-0428")),
    ("Q8.5", "costs in $k", lambda t, a: bool(re.search(r"\$\s?\d+(\.\d+)?\s?k|k\$|thousand", a, re.I))),
    ("Q9.2", "Nathan Pierce, 150 wells", has("nathan pierce", "150")),
    ("Q9.3", "a December miss: WELL-0310/0354/0486/0531", lambda t, a: any(w in a.upper() for w in ("WELL-0310", "WELL-0354", "WELL-0486", "WELL-0531"))),
    ("Q9.4", "no H2S reading (nobody responded)", lambda t, a: bool(re.search(r"no (h2s )?reading|not (taken|recorded)|nobody|no one|no response", a, re.I))),
    ("Q10", "names parts, crews, contractors, weather", has("parts", "crew", "contractor", "weather")),
    ("Q11", "explains the well-type mix (T500) confound", has("t500")),
]


def main() -> None:
    R = {r["id"]: r for r in json.load(open(sys.argv[1]))}
    ok = 0
    for q, desc, f in CHECKS:
        r = R.get(q)
        res = bool(r) and f(blob(r), r.get("answer", ""))
        ok += res
        print(f"{'PASS' if res else 'FAIL'}  {q:5} {desc}")
    print(f"\n{ok}/{len(CHECKS)} checks pass")


if __name__ == "__main__":
    main()
