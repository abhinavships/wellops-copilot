import copy

import pytest

from evals.cases import CaseError, load_cases, validate_case
from evals.metrics.scorers import (aggregate, pass_k, score_conflict, score_evidence, score_facts, score_leak,
                                   score_memory, score_no_prior, score_trajectory, score_turn, score_unconfirmed,
                                   score_visual, has_date, numbers_in)


def test_all_cases_validate_and_counts():
    g = load_cases("golden")
    assert len(g) == 18 and {c["id"] for c in g} == {f"Q{i:02d}" for i in range(1, 19)}
    s = load_cases("synthetic")
    assert len(s) >= 60 and all(c["suite"] == "synthetic" for c in s)


def test_episode_structure_of_multi_turn_cases():
    g = {c["id"]: c for c in load_cases("golden")}
    q11 = g["Q11"]["turns"]
    assert (q11[0]["clock"], q11[1]["clock"]) == ("2026-10-17", "2026-10-18") and q11[0]["chat"] != q11[1]["chat"]
    assert {t["user"] for t in g["Q16"]["turns"]} >= {"U-102", "U-201", "U-202"}
    q14 = g["Q14"]["turns"]
    assert q14[0]["chat"] == q14[1]["chat"] != q14[2]["chat"]  # re-ask in a NEW chat
    assert g["Q12"]["turns"][1]["say"].startswith("Which wells in Northeast Block 8")


@pytest.mark.parametrize("mutate", [lambda c: c.pop("turns"), lambda c: c["turns"][0].update(user="michael"),
                                    lambda c: c["turns"][0]["expect"].update(bogus=1),
                                    lambda c: c["turns"][0]["expect"].update(visual=["pie"])])
def test_schema_rejects_bad_cases(mutate):
    c = copy.deepcopy(load_cases("golden")[0])
    mutate(c)
    with pytest.raises(CaseError):
        validate_case(c)


def A(md="", **kw):
    return dict(markdown=md, claims=[], visuals=kw.pop("visuals", []), citations=kw.pop("citations", []), conflicts=[],
                applied_overlays=[], **kw)


def test_facts_numbers_tolerance_ids_dates_sets():
    ans = A("WELL-0121: 49 days, WELL-0138: 42, WELL-0107: 42 on 15 Oct 2026. Cost USD 25,794.")
    ex = dict(set_equals=["WELL-0121", "WELL-0138", "WELL-0107"], numbers=[{"label": "a", "value": 49, "tol": 0},
              {"label": "b", "value": 25794, "tol": 0.5}], dates=["2026-10-15"])
    assert score_facts(ans, ex).value == 1.0
    assert score_facts(A("WELL-0121 had 50 days"), ex).value < 1.0
    s = score_facts(A("WELL-0121 WELL-0138 WELL-0999"), dict(set_equals=["WELL-0121", "WELL-0138", "WELL-0107"]))
    assert s.details["set_precision"] == pytest.approx(2 / 3, abs=1e-3) and s.details["set_recall"] == pytest.approx(2 / 3, abs=1e-3)


def test_set_ignores_negated_mentions_and_allowed_extra():
    ex = dict(set_equals=["WELL-0170"])
    assert score_facts(A("WELL-0170 qualifies.\nWELL-0102 is not added because it is T200."), ex).value == 1.0
    assert score_facts(A("WELL-0170 and WELL-0102"), ex).value < 1.0
    ex2 = dict(set_equals={"ids": ["WELL-0170"], "allowed_extra": ["WELL-0164"]})
    assert score_facts(A("WELL-0170; WELL-0164 corrected"), ex2).value == 1.0


def test_numbers_do_not_read_ids_or_dates():
    assert numbers_in("WELL-0121 on 2026-10-15 cost 1,200.5") == [1200.5]
    assert has_date("on 15 Oct 2026", "2026-10-15") and has_date("Oct 15", "2026-10-15") and not has_date("16 Oct", "2026-10-15")


def test_evidence_recall_and_off_target_precision():
    ex = dict(must_cite=["INS-0001-0203", "CTX-0001-003"])
    good = A(citations=[dict(kind="inspection", id="INS-0001-0203"), dict(kind="note", id="CTX-0001-003")])
    assert score_evidence(good, ex).value == 1.0
    assert score_evidence(A(citations=[dict(kind="inspection", id="INS-0001-0203")]), ex).value == 0.5
    off = A(citations=good["citations"] + [dict(kind="inspection", id="INS-0999-0001")])
    assert score_evidence(off, ex).value < 1.0
    assert not score_evidence(good, {}).applies


def test_visual_conflict_leak_unconfirmed_noprior():
    assert score_visual(A(visuals=[dict(type="bar")]), dict(visual=["bar"])).value == 1.0
    assert score_visual(A(visuals=[dict(type="line")]), dict(visual=["bar"])).value == 0.0
    ex = dict(must_flag_conflict=dict(required=True, keywords=["Tyler Hughes"]))
    c = dict(subject="x", sides=[dict(author="Tyler Hughes")], resolution="unresolved")
    assert score_conflict(dict(A(), conflicts=[c]), ex).value == 1.0
    assert score_conflict(A("Tyler Hughes disagrees"), ex).value == 0.0
    assert score_leak(A("WELL-0121 cost 25,794"), dict(must_not_contain=["25794"])).value == 0.0
    assert score_leak(A("nothing"), dict(must_not_contain=["WELL-0121"])).value == 1.0
    assert score_unconfirmed(A("This is unconfirmed."), dict(must_say_unconfirmed=True)).value == 1.0
    assert score_unconfirmed(A("Noted."), dict(must_say_unconfirmed=True)).value == 0.0
    assert score_no_prior(A("I have no earlier conversation with you."), dict(must_say_no_prior_conversation=True)).value == 1.0
    assert score_no_prior(A("Here are the costs."), dict(must_say_no_prior_conversation=True)).value == 0.0


def test_memory_and_trajectory():
    mw = dict(memory_write=dict(kind="value_correction", scope="inspection", key="INS-1", attr="wellhead_temp_c", value=41.0))
    call = dict(name="assert_fact", args=dict(kind="value_correction", scope_level="inspection", scope_key="INS-1",
                                              target_attr="wellhead_temp_c", value_num=41.0))
    assert score_memory(A(), mw, [call]).value == 1.0
    assert score_memory(A(), mw, []).value == 0.0
    bad = dict(call, args=dict(call["args"], value_num=14.0))
    assert score_memory(A(), mw, [bad]).value == 0.0
    ok = [dict(name="downtime", args={"area": "x"})]
    assert score_trajectory(A(), {}, ok).value == 1.0
    sql = [dict(name="sql", args={"query": "select sum(downtime_hours) from maintenance"})]
    assert score_trajectory(A(), {}, sql).value < 1.0
    assert score_trajectory(A(), dict(max_steps=2), ok * 3).value < 1.0


def test_aggregate_weights_leak_and_pass_k():
    ex = dict(numbers=[{"label": "n", "value": 1, "tol": 0}], must_cite=["WELL-0001"], visual=["bar"], must_not_contain=["SECRET"])
    ok = A("1", visuals=[dict(type="bar")], citations=[dict(kind="well", id="WELL-0001")])
    assert aggregate(score_turn(ok, ex, []))["score"] == 1.0
    half = A("2", visuals=[dict(type="bar")], citations=[dict(kind="well", id="WELL-0001")])  # facts wrong
    assert aggregate(score_turn(half, ex, []))["score"] == pytest.approx(0.5)
    leaky = A("1 SECRET", visuals=[dict(type="bar")], citations=[dict(kind="well", id="WELL-0001")])
    r = aggregate(score_turn(leaky, ex, []))
    assert r["score"] == 0.0 and r["leak"]
    assert pass_k([[True, True, True], [True, False, True]]) == dict(pass_hat_k=0.5, pass_at_k=1.0)
