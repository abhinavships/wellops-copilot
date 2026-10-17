"""Pluggable targets. A target answers one turn -> (AnswerSpec dict, tool trace list)."""
from __future__ import annotations

import json
import os
import uuid
from datetime import date
from typing import Any, Protocol

from evals.truth.compute import compute, user_name
from evals.truth.db import get_con

Turn = dict
TRACE_TOOL = {"well_status": "well_status", "area_not_producing": "well_status", "series": "inspection_series",
              "spills_well": "spills", "spills_map": "spills", "spills_heatmap": "spills", "reportable_spills": "spills",
              "downtime_days": "downtime", "top_downtime": "downtime", "rules_broken": "obligations",
              "spill_reassessment": "obligations", "containment_breaches": "report_facts",
              "repair_costs": "repair_costs", "rca_candidates": "rca_candidates", "spill_detection_state": "evidence_chain"}


class Target(Protocol):
    name: str

    def respond(self, case: dict, turn: Turn) -> tuple[dict, list[dict]]: ...


def fmt_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day} {d.strftime('%b %Y')}"


def _cite_kind(i: str) -> str:
    return {"WELL": "well", "INS": "inspection", "MNT": "maintenance", "CTX": "note", "REG": "reg_event"}.get(i.split("-")[0], "doc")


def _fmt(v) -> str:
    return f"{v:,.0f}" if isinstance(v, (int, float)) and float(v).is_integer() and abs(v) >= 1000 else str(v)


class FakeTarget:
    """Answers from computed truth (substrate), echoing only non-computable expectations from the key.

    It exists to test the eval pipeline end to end, offline. `echoed` counts expectation items that were not derivable
    from the substrate and were therefore copied from the key (so a fake score is an upper bound, not a quality claim).
    """

    name = "fake"

    def respond(self, case: dict, turn: Turn) -> tuple[dict, list[dict]]:
        ex = turn.get("expect") or {}
        tr = compute(turn.get("truth"))
        md, trace, cites = ["**Answer (fake target, computed from substrate)**"], [], []
        echoed = 0
        con = get_con()
        if tr:
            for lab, v in tr["numbers"].items():
                md.append(f"- {lab}: {_fmt(v)}")
            if tr["set"]:
                md.append("- Result set: " + ", ".join(tr["set"]))
            if tr["ids"]:
                md.append("- Records: " + ", ".join(tr["ids"]))
            if tr["dates"]:
                md.append("- Dates: " + ", ".join(fmt_date(d) for d in tr["dates"]))
            if tr["text"]:
                md.append("- Notes: " + ", ".join(tr["text"]))
            cites += tr["cites"]
            trace.append(dict(name=TRACE_TOOL.get(turn["truth"]["fn"], "sql"),
                              args=dict(turn["truth"].get("args") or {}), result=dict(row_count=len(tr["rows"]))))
        # echo expectation items the substrate could not provide (conflicts, memory, people, extra dates...)
        have = "\n".join(md).lower()
        for s in ex.get("text", []):
            if s.lower() not in have:
                md.append(f"- {s}")
                echoed += 1
        for i in ex.get("ids_exact", []):
            if i not in have.upper() and i.lower() not in have:
                md.append(f"- {i}")
                echoed += 1
        se = ex.get("set_equals")
        if se is not None and not tr:
            ids = se["ids"] if isinstance(se, dict) else se
            md.append("- Result set: " + ", ".join(ids))
            echoed += 1
        for n in ex.get("numbers", []):
            if not tr or n["label"] not in tr["numbers"]:
                md.append(f"- {n['label']}: {_fmt(n['value'])}")
                echoed += 1
        for d in ex.get("dates", []):
            if not tr or d not in tr["dates"]:
                md.append(f"- Date: {fmt_date(d)}")
                echoed += 1
        cites += ex.get("must_cite", [])
        conflicts, overlays_applied = [], []
        spec = ex.get("must_flag_conflict")
        if spec and spec.get("required"):
            conflicts.append(dict(subject=" / ".join(spec.get("keywords", ["conflict"])), resolution="claim_unconfirmed"
                                  if ex.get("must_say_unconfirmed") else "unresolved",
                                  sides=[dict(source="record", author=k, says=k, ref=k) for k in spec.get("keywords", [])],
                                  ask="Can you confirm?"))
            echoed += 1
        if ex.get("must_disclose_overlay"):
            md.append(f"- Value corrected by {ex['must_disclose_overlay']} (original kept).")
            overlays_applied = ["A-fake-1"]
            echoed += 1
        if ex.get("must_say_unconfirmed"):
            md.append("- This claim is unconfirmed and both statements are kept on record; please confirm.")
            echoed += 1
        if ex.get("must_say_no_prior_conversation"):
            md.append("I have no earlier conversation with you about that, so I cannot answer from history.")
            echoed += 1
        mw = ex.get("memory_write")
        if mw:
            trace.append(dict(name="assert_fact", args=dict(kind=mw["kind"], scope_level=mw["scope"], scope_key=mw["key"],
                                                         target_attr=mw.get("attr"), value_num=mw.get("value")
                                                         if isinstance(mw.get("value"), (int, float)) else None,
                                                         value_text=mw.get("value") if isinstance(mw.get("value"), str) else None),
                              result=dict(original_num=mw.get("original"))))
        if "recall_episodes" in ex.get("tools_any", []):
            trace.insert(0, dict(name="recall_episodes", args={"query": turn["say"]}, result={}))
        allowed = ex.get("visual")
        vt = (tr or {}).get("visual") if tr else None
        vt = vt if vt and (not allowed or vt in allowed) else (allowed[0] if allowed else (vt or "none"))
        cites = list(dict.fromkeys(cites))
        ans = dict(markdown="\n".join(md), claims=[], visuals=[dict(type=vt, handle="r1", title="Result")],
                   citations=[dict(kind=_cite_kind(c), id=c) for c in cites], conflicts=conflicts,
                   applied_overlays=overlays_applied, assumptions=["as-of: simulated clock", f"dataset {case.get('dataset', 'sample')}"],
                   follow_ups=[])
        ans["_meta"] = dict(echoed=echoed)
        return ans, trace


class ApiTarget:
    """AG-UI over HTTP against the real backend: POST /api/agui, SSE stream of events."""

    name = "api"

    def __init__(self, base_url: str | None = None, timeout: float = 300.0):
        self.base_url = (base_url or os.environ.get("WELLOPS_API_URL", "http://localhost:8000")).rstrip("/")
        self.timeout = timeout

    def respond(self, case: dict, turn: Turn) -> tuple[dict, list[dict]]:
        import httpx

        body = dict(threadId=turn["chat"], runId=str(uuid.uuid4()), state={}, tools=[], context=[], forwardedProps={},
                    messages=[dict(id=str(uuid.uuid4()), role="user", content=turn["say"])])
        headers = {"X-User-Id": turn["user"], "X-Chat-Id": turn["chat"], "X-Clock-Today": turn["clock"],
                   "Accept": "text/event-stream", "Content-Type": "application/json"}
        events = []
        with httpx.stream("POST", f"{self.base_url}/api/agui", json=body, headers=headers, timeout=self.timeout) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    if payload:
                        try:
                            events.append(json.loads(payload))
                        except ValueError:
                            pass
        answer, trace = parse_agui(events)
        err = answer.get("_meta", {}).get("run_error")
        if err and answer["_meta"].get("no_answer_event"):
            raise RuntimeError(f"RUN_ERROR: {err}")
        return answer, trace


OUTPUT_TOOLS = {"final_result"}  # pydantic-ai output tool: the answer itself, counted as attempts, not a trajectory step


def parse_agui(events: list[dict]) -> tuple[dict, list[dict]]:
    """CUSTOM 'wellops.answer' -> AnswerSpec; TOOL_CALL_* -> trace. Falls back to streamed text if no answer event."""
    answer, text, calls, order, extras, run_error = None, [], {}, [], {}, None
    for e in events:
        t = e.get("type")
        if t == "RUN_ERROR":
            run_error = f"{e.get('code') or ''} {e.get('message') or ''}".strip()
        elif t == "CUSTOM":
            if e.get("name") == "wellops.answer":
                v = e.get("value")
                answer = json.loads(v) if isinstance(v, str) else v
            else:
                extras.setdefault(e.get("name"), []).append(e.get("value"))
        elif t == "TOOL_CALL_START":
            cid = e.get("toolCallId")
            calls[cid] = dict(name=e.get("toolCallName"), args="", result=None)
            order.append(cid)
        elif t == "TOOL_CALL_ARGS":
            if e.get("toolCallId") in calls:
                calls[e["toolCallId"]]["args"] += e.get("delta", "")
        elif t == "TOOL_CALL_RESULT":
            c = calls.get(e.get("toolCallId"))
            if c is not None:
                try:
                    c["result"] = json.loads(e.get("content", ""))
                except ValueError:
                    c["result"] = e.get("content")
        elif t == "TEXT_MESSAGE_CONTENT":
            text.append(e.get("delta", ""))
    trace, output_attempts = [], 0
    for cid in order:
        c = calls[cid]
        try:
            c["args"] = json.loads(c["args"]) if c["args"] else {}
        except ValueError:
            pass
        if c["name"] in OUTPUT_TOOLS:
            output_attempts += 1
        else:
            trace.append(c)
    if answer is None:
        answer = dict(markdown="".join(text))
        answer["_meta"] = dict(no_answer_event=True)
    meta = answer.setdefault("_meta", {})
    meta["output_attempts"] = output_attempts
    if extras:
        meta["events"] = extras
    if run_error:
        meta["run_error"] = run_error
    return answer, trace


def make_target(name: str, **kw) -> Target:
    if name == "fake":
        return FakeTarget()
    if name == "api":
        return ApiTarget(**kw)
    raise ValueError(name)
