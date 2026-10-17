"""Contract with the real backend stream (fixture captured from backend/src/wellops/api/app.py with a scripted model)."""
import json
from pathlib import Path

import pytest

from evals.metrics.scorers import score_memory, score_trajectory
from evals.runner.targets import ApiTarget, parse_agui

FIX = Path(__file__).parent / "fixtures" / "agui_well_status.sse"


def sse(text):
    return [json.loads(l[5:]) for l in text.splitlines() if l.startswith("data:") and l[5:].strip()]


def test_parse_real_stream():
    ans, trace = parse_agui(sse(FIX.read_text()))
    assert ans["claims"][0]["value"] == 0.4 and ans["citations"][0]["id"] == "INS-0175-0204"
    assert [c["name"] for c in trace] == ["well_status"]  # output tool kept out of the trajectory
    assert trace[0]["args"] == {"scope": {"kind": "wells", "ids": ["WELL-0175"]}} and trace[0]["result"]["handle"] == "r1"
    m = ans["_meta"]
    assert m["output_attempts"] == 1 and m["events"]["wellops.verification"][0]["ok"]


MW = {"kind": "status_claim", "scope": "well", "key": "WELL-0175"}
INNER = {"kind": "status_claim", "scope_level": "well", "scope_key": "WELL-0175", "value_text": "producing"}


@pytest.mark.parametrize("args", [{"assertion": INNER}, {"assertion": json.dumps(INNER)}, INNER])
def test_memory_write_nested_args(args):
    assert score_memory({}, {"memory_write": MW}, [dict(name="assert_fact", args=args, result="err")]).value == 1.0


def test_memory_write_prefers_validated_result():
    out = dict(INNER, assertion_id="A-1", status="unconfirmed")
    assert score_memory({}, {"memory_write": MW}, [dict(name="assert_fact", args="{bad", result=out)]).value == 1.0


def test_skill_reads_do_not_spend_step_budget():
    trace = [dict(name=n, args={}) for n in ["list_skills", "load_skill", "read_skill_file"] * 3 + ["well_status"]]
    s = score_trajectory({}, {"tools_any": ["well_status"], "max_steps": 2}, trace)
    assert s.value == 1.0 and s.details["steps"] == 1 and not s.details["non_canonical"]


def test_run_error_is_a_turn_error(monkeypatch):
    ev = [{"type": "RUN_STARTED"}, {"type": "RUN_ERROR", "message": "429 rate limited", "code": "ModelHTTPError"}]
    ans, _ = parse_agui(ev)
    assert ans["_meta"]["run_error"] == "ModelHTTPError 429 rate limited"
    import evals.runner.targets as T
    monkeypatch.setattr(T, "parse_agui", lambda events: (ans, []))

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def raise_for_status(self): pass
        def iter_lines(self): return iter(())
    import httpx
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: R())
    with pytest.raises(RuntimeError, match="RUN_ERROR"):
        ApiTarget("http://x").respond({}, dict(chat="c", say="hi", user="U-201", clock="2026-10-17"))
