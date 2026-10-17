import json

import pytest
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from wellops import tools as T
from wellops.agent import build_agent, run_turn
from wellops.agent import skills as sk
from wellops.contracts import AnswerSpec, AssertionIn
from wellops.memory import assert_fact
from wellops.verify import VERIFICATIONS


def scripted(*steps):
    """steps: list of (tool_name, args) or callables(messages)->(tool_name,args). Last step is the final_result."""
    calls = {"n": 0}

    def fn(messages, info):
        i = min(calls["n"], len(steps) - 1)
        calls["n"] += 1
        s = steps[i]
        name, args = s(messages) if callable(s) else s
        return ModelResponse(parts=[ToolCallPart(name, args)])

    return FunctionModel(fn), calls


def handle_of(messages, tool):
    for m in messages:
        for p in getattr(m, "parts", []):
            if getattr(p, "tool_name", None) == tool and hasattr(p, "content") and not isinstance(p.content, str):
                c = p.content
                return c["handle"] if isinstance(c, dict) else c.handle
    raise AssertionError("no tool return")


def final(md, handle, claims=(), **kw):
    return "final_result", {"markdown": md, "claims": list(claims), "assumptions": ["as-of 2026-10-17, dataset sample"], **kw}


@pytest.mark.asyncio
async def test_agent_answers_with_bound_claims_and_records_episode(deps):
    d = deps("U-201", "ag1")
    model, calls = scripted(
        ("well_status", {"scope": {"kind": "wells", "ids": ["WELL-0175"]}}),
        lambda m: final("WELL-0175 is not producing: wellhead is 0.4 °C above air.", (h := handle_of(m, "well_status")),
                        [{"text": "0.4 °C", "value": 0.4, "handle": h, "column": "delta_c", "row_key": {"asset_id": "WELL-0175"}}],
                        citations=[{"kind": "inspection", "id": "INS-0175-0204"}]))
    agent = build_agent(model)
    spec, v, _ = await run_turn(agent, d, "Is WELL-0175 producing?")
    assert isinstance(spec, AnswerSpec) and v.ok and calls["n"] == 2
    with pytest.raises(T.ToolError):  # episodes are never readable through open SQL
        T.sql(deps("U-201", "x"), "SELECT * FROM episodes")


@pytest.mark.asyncio
async def test_fabricated_number_triggers_retry(deps):
    d = deps("U-201", "ag2")
    state = {}

    def first_bad_then_good(m):
        h = handle_of(m, "downtime")
        retried = any("Verification failed" in str(getattr(p, "content", "")) for x in m for p in getattr(x, "parts", []))
        claim = [{"text": "21 days", "value": 21, "handle": h, "column": "downtime_days"}]
        if not retried:
            state["bad"] = True
            return final("WELL-0001 was down 37 days in 2025.", h, claim)
        return final("WELL-0001 was down 21 days in 2025.", h, claim)

    model, calls = scripted(("downtime", {"scope": {"kind": "wells", "ids": ["WELL-0001"]}, "start": "2025-01-01",
                                          "end": "2025-12-31"}), first_bad_then_good)
    spec, v, _ = await run_turn(build_agent(model), d, "How many days was WELL-0001 down in 2025?")
    assert state["bad"] and "21" in spec.markdown and calls["n"] == 3
    attempts = VERIFICATIONS["ag2"]
    assert [a.ok for a in attempts] == [False, True]
    assert "37" in attempts[0].diff() and attempts[0].gates[0].name == "grounding"


@pytest.mark.asyncio
async def test_wrong_claim_value_and_visual_fit_and_disclosure(deps):
    d = deps("U-201", "ag3")
    out = assert_fact(d, AssertionIn(kind="value_correction", scope_level="inspection", scope_key="INS-0164-0205",
                                     target_attr="wellhead_temp_c", value_num=41.0, reason="faulty gun"))
    seen = {"n": 0}

    def answer(m):
        h = handle_of(m, "well_status")
        seen["n"] += 1
        if seen["n"] == 1:  # wrong value, wrong visual, no author, no conflict
            return final("WELL-0164 delta is 5.0 °C.", h, [{"text": "5.0 °C", "value": 5.0, "handle": h, "column": "delta_c",
                                                           "row_key": {"asset_id": "WELL-0164"}}],
                         visuals=[{"type": "line", "handle": h}])
        r = next(p.content for x in m for p in getattr(x, "parts", [])
                 if getattr(p, "tool_name", "") == "well_status" and p.part_kind == "tool-return")
        conf = r["conflicts"] if isinstance(r, dict) else [c.model_dump() for c in r.conflicts]
        return final("WELL-0164 delta is 21.1 °C after the correction by Michael Turner.", h,
                     [{"text": "21.1 °C", "value": 21.1, "handle": h, "column": "delta_c", "row_key": {"asset_id": "WELL-0164"}}],
                     visuals=[{"type": "status_card", "handle": h}], applied_overlays=[out.assertion_id], conflicts=conf)

    model, _ = scripted(("well_status", {"scope": {"kind": "wells", "ids": ["WELL-0164"]}}), answer)
    spec, v, _ = await run_turn(build_agent(model), d, "Is WELL-0164 producing?")
    a = VERIFICATIONS["ag3"]
    assert not a[0].ok
    failed = {g.name for g in a[0].gates if not g.ok}
    assert {"claims", "disclosure", "conflicts", "visual_fit"} <= failed
    assert a[-1].ok and spec.conflicts


def test_skills_progressive_disclosure():
    skills = sk.load_skills()
    assert {"well-status", "downtime-analysis", "spill-accounting", "sop-compliance-audit", "rca-escalation",
            "feedback-intake", "conflict-handling", "memory-recall", "visual-choice"} <= set(skills)
    assert "[ws-03]" in skills["well-status"].body and skills["well-status"].description
    assert "unknown skill" in sk.read_skill_file(skills, "nope", "x")
    assert "no such file" in sk.read_skill_file(skills, "well-status", "../downtime-analysis/SKILL.md")
    assert "# Well status" in sk.read_skill_file(skills, "well-status", "SKILL.md")
