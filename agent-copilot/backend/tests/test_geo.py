from fastapi.testclient import TestClient

from wellops.api import app as api


def test_locations_by_ids_and_area(db):
    c = TestClient(api.app)
    r = c.get("/api/wells/locations?ids=WELL-0001, well-0002,WELL-9999")
    assert r.status_code == 200
    j = r.json()
    assert [w["asset_id"] for w in j["wells"]] == ["WELL-0001", "WELL-0002"] and j["count"] == 2
    w = j["wells"][0]
    assert {"asset_id", "name", "area", "lat", "lon", "recorded_status"} <= w.keys()
    assert w["area"] == "Southwest Block 23" and abs(w["lat"] - 31.85425) < 1e-6 and abs(w["lon"] + 102.50232) < 1e-6
    assert w["last_inspected_at"] and isinstance(w["producing"], bool)

    by_id = c.get("/api/wells/locations?area=NE-08").json()["wells"]
    by_name = c.get("/api/wells/locations?area=Northeast Block 8").json()["wells"]
    assert by_id and by_id == by_name and {w["area_id"] for w in by_id} == {"NE-08"}

    assert c.get("/api/wells/locations").status_code == 400
    assert c.get("/api/wells/locations?area=Atlantis").status_code == 404
