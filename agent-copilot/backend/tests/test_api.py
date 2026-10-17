import json
import uuid

import pytest
from fastapi.testclient import TestClient

from wellops.agent import build_agent
from wellops.api import app as api

from test_agent import final, handle_of, scripted

H = {"X-User-Id": "U-201", "X-Chat-Id": "api1", "X-Clock-Today": "2026-10-17", "Accept": "text/event-stream"}


def body(text, chat="api1"):
    return dict(threadId=chat, runId=str(uuid.uuid4()), state={}, tools=[], context=[], forwardedProps={},
                messages=[dict(id=str(uuid.uuid4()), role="user", content=text)])


def events(resp):
    return [json.loads(l[5:]) for l in resp.text.splitlines() if l.startswith("data:") and l[5:].strip()]


@pytest.fixture
def client():
    api.HISTORY.clear()
    yield TestClient(api.app)
    api.AGENT_OVERRIDE.clear()


def test_agui_streams_answer_verification_and_serves_handle(client):
    model, _ = scripted(
        ("well_status", {"scope": {"kind": "wells", "ids": ["WELL-0175"]}}),
        lambda m: final("WELL-0175 is not producing: wellhead is 0.4 °C above air.", (h := handle_of(m, "well_status")),
                        [{"text": "0.4 °C", "value": 0.4, "handle": h, "column": "delta_c", "row_key": {"asset_id": "WELL-0175"}}],
                        citations=[{"kind": "inspection", "id": "INS-0175-0204"}]))
    api.AGENT_OVERRIDE["agent"] = build_agent(model)
    r = client.post("/api/agui", json=body("Is WELL-0175 producing?"), headers=H)
    assert r.status_code == 200
    ev = events(r)
    types = [e["type"] for e in ev]
    assert types[0] == "RUN_STARTED" and types[-1] == "RUN_FINISHED" and "TOOL_CALL_START" in types
    custom = {e["name"]: e["value"] for e in ev if e["type"] == "CUSTOM"}
    ans, ver = custom["wellops.answer"], custom["wellops.verification"]
    assert ans["claims"][0]["value"] == 0.4 and ver["ok"] and ver["retries"] == 0
    h = ans["claims"][0]["handle"]
    d = client.get(f"/api/handles/{h}", params={"chat_id": "api1"}).json()
    assert d["shape"] and any(row.get("asset_id") == "WELL-0175" for row in d["rows"])
    assert client.get("/api/handles/r999", params={"chat_id": "api1"}).status_code == 404
    chats = client.get("/api/chats", params={"principal": "U-201"}).json()["chats"]
    assert chats[0]["chat_id"] == "api1" and chats[0]["title"] == "Is WELL-0175 producing?"
    assert client.get("/api/chats", params={"principal": "U-101"}).json()["chats"] == []  # principal-scoped
    assert len(api.HISTORY["api1"]) >= 4


def test_rest_endpoints(client):
    assert client.get("/api/health").json()["wells"] == 200
    users = client.get("/api/users").json()["users"]
    assert {"user_id": "U-201", "name": "Michael Turner", "role": "production_supervisor", "team_id": "TEAM-NE"} in users
    rec = client.get("/api/records/inspection/INS-0175-0204").json()
    assert rec["record"]["asset_id"] == "WELL-0175"
    assert client.get("/api/records/inspection/INS-9999-9999").status_code == 404
    assert client.post("/api/assertions/A-deadbeef/undo", headers=H).status_code == 404
