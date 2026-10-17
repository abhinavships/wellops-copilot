import pytest
from fastapi.testclient import TestClient

from wellops import tools
from wellops.api import app as api
from wellops.tools.core import ToolOut

U1 = {"X-User-Id": "U-201"}
U2 = {"X-User-Id": "U-202"}
ROWS = [{"asset_id": "WELL-0175", "downtime_days": 21}, {"asset_id": "WELL-0012", "downtime_days": 4}]


@pytest.fixture
def client(db):
    yield TestClient(api.app)
    db.run("DELETE FROM canvases")


def put_handle(db, chat="src-chat"):
    out = ToolOut(handle="", shape="category_measure", columns=["asset_id", "downtime_days"], preview=ROWS,
                  row_count=2, sql="SELECT 1", citations=[{"kind": "well", "id": "WELL-0175"}])
    return tools.store(db).put(chat, out, ROWS)


def card(chat, h, type_="bar"):
    return {"id": f"{chat}:{type_}:{h}", "chatId": chat, "visual": {"type": type_, "handle": h, "title": "Downtime"},
            "x": 20, "y": 20, "w": 460, "h": 340, "added": 1}


def test_save_list_load_survives_lost_chat(client, db):
    h = put_handle(db)
    conflict = {"id": "src-chat:conflict_card:{}", "chatId": "src-chat", "visual": {"type": "conflict_card"},
                "conflicts": [], "x": 500, "y": 20, "w": 460, "h": 340, "added": 2}
    r = client.post("/api/canvases", json={"name": "Downtime review", "cards": [card("src-chat", h), conflict]}, headers=U1)
    assert r.status_code == 200
    cid = r.json()["canvas_id"]
    assert r.json()["card_count"] == 2

    lst = client.get("/api/canvases", headers=U1).json()["canvases"]
    assert [(c["canvas_id"], c["name"], c["card_count"]) for c in lst] == [(cid, "Downtime review", 2)]
    assert client.get("/api/canvases", headers=U2).json()["canvases"] == []

    # the source chat disappears and the process "restarts" (in-memory handles dropped)
    db.run("DELETE FROM handles WHERE chat_id='src-chat'")
    tools.store(db).forget()
    assert client.get(f"/api/handles/{h}?chat_id=src-chat").status_code == 404

    got = client.get(f"/api/canvases/{cid}", headers=U1).json()
    assert got["name"] == "Downtime review" and got["created_at"] and got["updated_at"]
    c0, c1 = got["cards"]
    assert c0["chatId"] == f"canvas-{cid}" and c0["visual"]["handle"] == "r1"
    assert c0["id"] == f"canvas-{cid}:bar:r1" and "snapshot" not in c0
    assert c1["chatId"] == f"canvas-{cid}" and c1["id"] == f"canvas-{cid}:conflict_card:{{}}"

    tools.store(db).forget()  # served from the handles table too
    hd = client.get(f"/api/handles/r1?chat_id=canvas-{cid}").json()
    assert hd["rows"] == ROWS and hd["shape"] == "category_measure" and hd["columns"] == ["asset_id", "downtime_days"]
    assert hd["citations"][0]["id"] == "WELL-0175"

    # re-saving the loaded canvas keeps its snapshot; loading twice is idempotent
    assert client.post("/api/canvases", json={"canvas_id": cid, "name": "Downtime review", "cards": got["cards"]},
                       headers=U1).status_code == 200
    again = client.get(f"/api/canvases/{cid}", headers=U1).json()
    assert again["cards"][0]["visual"]["handle"] == "r1"
    assert client.get(f"/api/handles/r1?chat_id=canvas-{cid}").json()["rows"] == ROWS


def test_rename_upsert_owner_and_delete(client, db):
    h = put_handle(db)
    cid = client.post("/api/canvases", json={"name": "A", "cards": [card("src-chat", h)]}, headers=U1).json()["canvas_id"]
    r = client.post("/api/canvases", json={"canvas_id": cid, "name": "Renamed"}, headers=U1).json()
    assert r == {"canvas_id": cid, "name": "Renamed", "card_count": 1}  # cards omitted = kept
    assert client.get(f"/api/canvases/{cid}", headers=U1).json()["name"] == "Renamed"

    # another principal can neither read, overwrite nor delete it
    assert client.get(f"/api/canvases/{cid}", headers=U2).status_code == 404
    assert client.post("/api/canvases", json={"canvas_id": cid, "name": "x", "cards": []}, headers=U2).status_code == 404
    assert client.delete(f"/api/canvases/{cid}", headers=U2).status_code == 404

    client.get(f"/api/canvases/{cid}", headers=U1)
    assert client.delete(f"/api/canvases/{cid}", headers=U1).json() == {"deleted": cid}
    assert client.get(f"/api/canvases/{cid}", headers=U1).status_code == 404
    assert db.rows("SELECT count(*) n FROM handles WHERE chat_id=?", [f"canvas-{cid}"])[0]["n"] == 0


def test_explicit_new_id_and_blank_name(client):
    r = client.post("/api/canvases", json={"canvas_id": "mine1", "name": "  ", "cards": []}, headers=U1).json()
    assert r == {"canvas_id": "mine1", "name": "Untitled canvas", "card_count": 0}
