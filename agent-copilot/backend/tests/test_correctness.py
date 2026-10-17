from wellops import tools as T
from wellops.qa.correctness import check

Q6 = "Which 3 wells in Northeast Block 7 had the most downtime in the last 4 months? Show a bar chart."


def test_ranking_answer_right_and_wrong(deps):
    d = deps("U-201", "qa1")
    r = T.downtime(d, {"kind": "area", "ids": ["Northeast Block 7"]}, "2026-06-17", "2026-10-17", "well", 3)
    args = [{"start": "2026-06-17", "end": "2026-10-17"}]
    ok = check(d, Q6, "The top 3 are WELL-0121 with 49 days, WELL-0107 with 42 days and WELL-0138 with 42 days.", [r], args)
    assert ok.verdict == "correct", ok.as_dict()
    bad = check(d, Q6, "The top 3 are WELL-0121 with 42 days, WELL-0107 with 42 days and WELL-0138 with 35 days.", [r], args)
    assert bad.verdict == "incorrect" and "WELL-0121" in bad.mismatches()


def test_wrong_window_is_caught(deps):
    d = deps("U-201", "qa2")
    r = T.downtime(d, {"kind": "area", "ids": ["Northeast Block 7"]}, "2026-07-17", "2026-10-17", "well", 3)
    v = check(d, Q6, "WELL-0121 had 42 days.", [r], [{"start": "2026-07-17", "end": "2026-10-17"}])
    assert v.verdict == "incorrect" and any(c.name == "window" and not c.ok for c in v.checks)


def test_status_and_list_completeness(deps):
    d = deps("U-201", "qa3")
    s = T.well_status(d, {"kind": "wells", "ids": ["WELL-0001"]})
    assert check(d, "Is WELL-0001 producing?", "Yes, WELL-0001 is producing oil.", [s], []).verdict == "incorrect"
    assert check(d, "Is WELL-0001 producing?", "No, WELL-0001 is not producing.", [s], []).verdict == "correct"
    a = T.well_status(d, {"kind": "area", "ids": ["Northeast Block 8"]}, None, "not_producing")
    q = "Which wells in Northeast Block 8 are not producing oil right now?"
    full = "WELL-0164, WELL-0170, WELL-0172 and WELL-0175 are not producing."
    assert check(d, q, full, [a], []).verdict == "correct"
    v = check(d, q, "WELL-0164, WELL-0170 and WELL-0172 are not producing.", [a], [])
    assert v.verdict == "incorrect" and "WELL-0175" in v.mismatches()
