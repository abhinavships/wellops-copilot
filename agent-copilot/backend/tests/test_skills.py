"""Skills: seeding from SKILL.md, auto-distillation from a verified trace, dedupe/reinforcement, failure -> pitfall,
decay, retrieval, MCP registration and the REST surface. No network (LLM polish is off)."""

import json

import pytest
from fastapi.testclient import TestClient

import wellops.skills as K
from wellops import mcp_server
from wellops.settings import settings
from wellops.skills import distill as D
from wellops.skills import store as S

Q6 = "Which 3 wells in Northeast Block 7 had the most downtime in the last 4 months? Show a bar chart."
Q6_CALLS = [
    {"name": "resolve_entity", "args": {"text": "Northeast Block 7"}},
    {"name": "resolve_entity", "args": {"text": "last 4 months"}},
    {"name": "downtime", "args": {"scope": {"kind": "area", "ids": ["NE-07"]}, "start": "2026-06-17",
                                  "end": "2026-10-17", "group_by": "well", "top_k": 3}},
    {"name": "show_visual", "args": {"type": "bar", "handle": "r3", "title": "Downtime by well"}},
]
TRACE_OK = [{"name": "resolve_entity", "ms": 4, "ok": True}, {"name": "resolve_entity", "ms": 3, "ok": True},
            {"name": "downtime", "ms": 9, "ok": True}, {"name": "show_visual", "ms": 1, "ok": True},
            {"name": "verify", "ms": 0, "ok": True, "detail": "7/7 gates"}]


@pytest.fixture(autouse=True)
def fresh_skills(db):
    settings.skills_llm_polish = False
    with db.lock:
        db.con.execute("DROP TABLE IF EXISTS skills")
        db.con.execute("DROP TABLE IF EXISTS skill_runs")
    db._skills_ready = False
    mcp_server.CALL_LOG.clear()
    yield


def test_seed_imports_manual_skill_md():
    skills = {s["skill_id"]: s for s in K.list_skills()}
    assert {"downtime-analysis", "spill-accounting", "well-status"} <= set(skills)
    assert all(s["source"] == "manual" for s in skills.values())
    full = K.get_skill("downtime-analysis")
    assert "downtime" in full["tools"] and full["body"].startswith("# Downtime playbook")
    assert any("NEVER" in p for p in full["pitfalls"])
    assert S.seed_manual() == 0  # idempotent: unchanged files are not rewritten


def test_generalize_literals_into_slots():
    steps = D.steps_from_calls(Q6_CALLS)
    assert [D.render_step(s) for s in steps] == [
        "resolve_entity(text={area})",
        "resolve_entity(text={period})",
        'downtime(scope={area_scope}, start={window.start}, end={window.end}, group_by="well", top_k={n})',
        'show_visual(type="bar")',
    ]
    assert D.generalize("well_status", {"scope": "WELL-0151"}) == {"scope": "{well_scope}"}
    assert D.generalize("evidence_chain", {"id": "INS-0001-0203"}) == {"id": "{record_id}"}
    assert D.generalize("spills", {"scope": "Southwest Block 23", "group_by": "month"}) == \
        {"scope": "{area_scope}", "group_by": "month"}
    assert D.parse_step('downtime(scope={area_scope}, group_by="well", top_k={n})') == \
        {"tool": "downtime", "args": {"scope": "{area_scope}", "group_by": "well", "top_k": "{n}"}}


def test_auto_distill_from_verified_q6_trace():
    r = K.learn_from_turn(Q6, TRACE_OK, Q6_CALLS, True, [{"type": "bar", "handle": "r3"}])
    assert r["action"] == "created" and r["skill_id"] == "rank-downtime-in-area"
    s = K.get_skill("rank-downtime-in-area")
    assert s["source"] == "auto" and s["visual"] == "bar" and s["triggers"] == [Q6]
    assert s["uses"] == 1 and s["successes"] == 1 and s["failures"] == 0
    assert 'downtime(scope={area_scope}, start={window.start}, end={window.end}, group_by="well", top_k={n})' in s["steps"]
    assert "NE-07" not in json.dumps(s["steps_structured"]) and "2026" not in json.dumps(s["steps_structured"])
    md = K.export_skill_md("rank-downtime-in-area")
    assert md.startswith("---\nname: rank-downtime-in-area") and "## Steps" in md and "{area_scope}" in md


def test_similar_question_reinforces_and_dedupes_triggers():
    K.learn_from_turn(Q6, TRACE_OK, Q6_CALLS, True, [{"type": "bar"}])
    q2 = "Top 5 wells by downtime in Southwest Block 23 over the past 6 months"
    calls2 = [{"name": "find_skills", "args": {"question": q2}},  # meta tools are not part of the method
              {"name": "resolve_entity", "args": {"text": "Southwest Block 23 past 6 months"}},
              {"name": "downtime", "args": {"scope": "Southwest Block 23", "start": "2026-04-17", "end": "2026-10-17",
                                            "group_by": "well", "top_k": 5}},
              {"name": "show_visual", "args": {"type": "bar", "handle": "r2"}}]
    r = K.learn_from_turn(q2, TRACE_OK, calls2, True, ["bar"])
    assert r["action"] == "reinforced" and r["skill_id"] == "rank-downtime-in-area"
    K.learn_from_turn(q2.upper(), TRACE_OK, calls2, True, ["bar"])  # same question again: no duplicate trigger
    s = K.get_skill("rank-downtime-in-area")
    assert s["uses"] == 3 and s["successes"] == 3 and len(s["triggers"]) == 2
    assert len([x for x in K.list_skills(source="auto")]) == 1
    # a different method (monthly trend, no ranking) is a new skill
    r3 = K.learn_from_turn("Downtime per month in Northeast Block 8 this year", TRACE_OK,
                           [{"name": "downtime", "args": {"scope": "NE-08", "start": "2026-01-01", "group_by": "month"}},
                            {"name": "show_visual", "args": {"type": "line"}}], True, None)
    assert r3["action"] == "created" and r3["skill_id"] == "downtime-trend-in-area"


def test_failed_verification_records_pitfall_and_decays():
    K.learn_from_turn(Q6, TRACE_OK, Q6_CALLS, True, ["bar"])
    bad = [{"name": "resolve_entity", "ms": 3, "ok": True}, {"name": "downtime", "ms": 5, "ok": False, "detail": "bad window"},
           {"name": "verify", "ms": 0, "ok": False, "detail": "4/7 gates, 2 retried"}]
    r = K.learn_from_turn("top downtime wells NE-08 last 3 months", bad, Q6_CALLS, False, None)
    assert r["action"] == "failed"
    s = K.get_skill("rank-downtime-in-area")
    assert s["failures"] == 1 and s["version"] == 2
    assert len(s["triggers"]) == 1  # failing questions are not triggers
    assert s["pitfalls"] and s["pitfalls"][0].startswith("Step downtime(scope={area_scope}") and "bad window" in s["pitfalls"][0]
    for _ in range(4):
        K.learn_from_turn("again", bad, Q6_CALLS, False, None)
    s = K.get_skill("rank-downtime-in-area")
    assert s["status"] == "deprecated" and len(s["pitfalls"]) == 1  # pitfalls dedupe
    assert all(x["skill_id"] != "rank-downtime-in-area" for x in K.find_skills(Q6))
    # an unverified path with no matching skill teaches nothing
    r = K.learn_from_turn("spills by month", bad, [{"name": "spills", "args": {"group_by": "month"}}], False, None)
    assert r["action"] == "skipped" and K.get_skill("spills-trend-across-fleet") is None


def test_retrieval_ranks_relevant_and_reliable_skills():
    K.learn_from_turn(Q6, TRACE_OK, Q6_CALLS, True, ["bar"])
    hits = K.find_skills("Which wells in Southwest Block 24 were down the most in the last quarter?", k=3)
    assert hits[0]["skill_id"] == "rank-downtime-in-area"
    assert "downtime-analysis" in [h["skill_id"] for h in hits]
    assert K.find_skills("How many spills were there and what was the total volume?")[0]["skill_id"] == "spill-accounting"
    # a manual skill with the same keywords but a worse record falls below a proven one
    K.create_skill("rank-downtime-copy", "rank wells by downtime in an area", triggers=[Q6],
                   steps=['downtime(scope={area_scope}, group_by="well", top_k={n})'])
    for _ in range(3):
        K.record_outcome("rank-downtime-copy", False, pitfall="wrong window")
    ids = [h["skill_id"] for h in K.find_skills(Q6)]
    assert ids.index("rank-downtime-in-area") < ids.index("rank-downtime-copy")


def test_create_and_edit_skill_manually():
    s = K.create_skill("Spill map in area", "Where spills happened in an area",
                       triggers=["Map the spills in Northeast Block 8"],
                       steps=["resolve_entity(text={area})", "spills(scope={area_scope})", 'show_visual(type="map")'],
                       visual="map")
    assert s["skill_id"] == "spill-map-in-area" and s["version"] == 1 and s["source"] == "manual"
    e = K.create_skill("spill-map-in-area", "", pitfalls=["Corrected detections are not spills"], skill_id=s["skill_id"])
    assert e["version"] == 2 and e["pitfalls"] == ["Corrected detections are not spills"] and e["description"]
    # a turn that follows the hand-written steps reinforces it instead of creating a duplicate
    r = K.learn_from_turn("show spills on a map for SW-23", TRACE_OK,
                          [{"name": "resolve_entity", "args": {"text": "SW-23"}},
                           {"name": "spills", "args": {"scope": {"kind": "area", "ids": ["SW-23"]}}},
                           {"name": "show_visual", "args": {"type": "map", "handle": "r2"}}], True, ["map"])
    assert r["action"] == "reinforced" and r["skill_id"] == "spill-map-in-area"


def test_on_turn_complete_uses_call_log_and_never_raises(deps):
    d = deps(chat="sk1")
    mcp_server.CALL_LOG["sk1"] = [{"name": "get_skill", "args": {"skill_id": "downtime-analysis"}}, *Q6_CALLS]
    steps = [{"name": "get_skill", "ms": 1, "ok": True}, *TRACE_OK]
    r = K.on_turn_complete(d, Q6, steps, True, [{"type": "bar"}])
    assert r["action"] == "created" and "sk1" not in mcp_server.CALL_LOG
    assert K.get_skill("downtime-analysis")["successes"] == 1  # the retrieved playbook is credited too
    K.on_turn_complete(None, None, None, True, object())  # garbage in: no exception
    assert K.on_turn_complete(d, Q6, [{"bad": 1}], True, None, tool_calls=[{"name": 3}])["action"] == "skipped"


def test_skill_tools_registered_and_rest_api():
    names = {t.name for t in mcp_server.mcp._tool_manager.list_tools()}
    assert {"find_skills", "get_skill"} <= names
    from wellops.skills.server import skills_mcp
    assert {t.name for t in skills_mcp._tool_manager.list_tools()} == {
        "list_skills", "get_skill", "find_skills", "create_skill", "record_outcome", "export_skill_md"}
    assert json.loads(mcp_server.get_skill(None, "well-status"))["skill_id"] == "well-status"

    from wellops.api import app as api
    c = TestClient(api.app)
    assert any(s["skill_id"] == "downtime-analysis" for s in c.get("/api/skills").json())
    r = c.post("/api/skills", json={"name": "rank-spills", "description": "Areas ranked by spill count",
                                    "steps": ['spills(group_by="area")', 'show_visual(type="bar")']})
    assert r.status_code == 200 and r.json()["steps"][0] == 'spills(group_by="area")'
    assert c.get("/api/skills/rank-spills").json()["runs"][0]["action"] == "created"
    md = c.get("/api/skills/rank-spills/md")
    assert md.status_code == 200 and md.text.startswith("---\nname: rank-spills")
    assert c.get("/api/skills?q=spill count by area").json()[0]["skill_id"] == "rank-spills"
    assert c.get("/api/skills/nope").status_code == 404
