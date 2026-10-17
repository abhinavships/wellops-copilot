import json

import pytest
from fastapi.testclient import TestClient

from wellops.contracts import RunDeps
from wellops.ingest import pipeline
from wellops.tools import ingest_tools, sql

from conftest import TODAY


@pytest.fixture(autouse=True)
def uploads(tmp_path, monkeypatch, db):
    monkeypatch.setattr(pipeline, "UPLOADS", tmp_path / "uploads")
    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    monkeypatch.setattr(ingest_tools, "ROOT", tmp_path)
    yield
    for t in ("user_tables", "media", "ingest_log"):
        db.run(f"DELETE FROM {t}")
    db.run("DELETE FROM inspections WHERE inspection_id LIKE 'INS-0001-9%'")
    db.run("DELETE FROM docs WHERE doc_id LIKE 'UP-%'")
    db.run("DELETE FROM rules WHERE doc_id LIKE 'UP-%'")
    db.run("DELETE FROM type_inherits WHERE well_type='T900'")
    db.run("DELETE FROM dq_flags WHERE entity_id='T900'")


D = RunDeps(principal="U-201", chat_id="ing", today=TODAY)


def test_csv_matching_inspections_is_appended_and_flagged(tmp_path, db):
    p = tmp_path / "new_inspections.csv"
    p.write_text("inspection_id,asset_id,inspected_at,method,ambient_temp_c,wellhead_temp_c,spill_detected\n"
                 "INS-0001-9001,WELL-0001,2026-10-16T08:00:00Z,drone,20.0,21.0,false\n"
                 "INS-0001-0001,WELL-0001,2024-01-01T08:00:00Z,drone,20.0,60.0,false\n")  # existing id: skipped
    r = pipeline.ingest_file(p, p.name, "U-201", db=db)
    assert r.kind == "known_table" and r.tables == ["inspections"] and r.rows == 1
    assert any("already present" in w for w in r.warnings)
    st = db.rows("SELECT producing FROM latest_status('sample', TIMESTAMPTZ '2026-10-17 23:59:59+00') WHERE asset_id='WELL-0001'")
    assert st == [{"producing": False}]  # the new reading (delta 1 °C) is now the latest status


def test_unknown_csv_becomes_queryable_user_table(tmp_path, db):
    p = tmp_path / "Pump Vendors.csv"
    p.write_text("vendor,city,rating\nAcme Lift,Midland,4.5\nPetroServe,Odessa,3.9\n")
    r = pipeline.ingest_file(p, p.name, "U-201", db=db)
    assert r.kind == "table" and r.tables == ["user_pump_vendors"] and r.rows == 2
    out = sql(D, "SELECT vendor FROM user_pump_vendors WHERE rating > 4")
    assert out.preview == [{"vendor": "Acme Lift"}]
    assert "user_pump_vendors(vendor text, city text, rating number)" in pipeline.user_tables_catalog(db)


def test_markdown_manual_rules_apply_and_doc_is_searchable(tmp_path, db):
    from wellops.tools import read_doc, search_docs
    p = tmp_path / "T900 manual.md"
    p.write_text("# T900 operating manual\n\n## 1. Scope\n\nProcedures not listed here follow the T200 manual.\n\n"
                 "## 3. Production\n\nA T900 well is producing only when its wellhead is at least 30 °C above ambient air.\n")
    r = pipeline.ingest_file(p, p.name, "U-201", db=db)
    assert r.kind == "manual" and r.docs >= 2 and "production_delta_min_c=30" in r.summary
    assert db.rows("SELECT value, section FROM rules WHERE scope_key='T900'") == [{"value": 30.0, "section": "3"}]
    assert search_docs(D, "30 °C above ambient").preview[0]["doc_id"] == "UP-t900_manual"
    assert read_doc(D, "UP-t900_manual", "3").row_count == 1


def test_image_is_described_once_and_tools_work(tmp_path, monkeypatch, db):
    calls = []

    def fake_vision(image, mime, prompt, model=None):
        calls.append(prompt)
        return "test/vision:free", json.dumps({"caption": "Pump jack beside a wellhead", "text": "WELL-0042", "tags": ["pump jack"]})
    monkeypatch.setattr(pipeline, "vision", fake_vision)
    p = tmp_path / "site.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    r = pipeline.ingest_file(p, p.name, "U-201", db=db)
    assert r.kind == "image" and r.preview[0]["asset_id"] == "WELL-0042" and len(calls) == 1
    again = pipeline.ingest_file(p, "copy.png", "U-201", db=db)
    assert "Already ingested" in again.summary and len(calls) == 1  # same bytes: no second vision call
    mid = r.preview[0]["media_id"]
    assert ingest_tools.list_media(D, "WELL-0042")["media"][0]["media_id"] == mid
    assert ingest_tools.analyze_image(D, mid)["caption"] == "Pump jack beside a wellhead" and len(calls) == 1
    assert ingest_tools.analyze_image(D, mid, "Is there a spill?")["answer"] and len(calls) == 2


def test_api_upload_multipart_and_history(db):
    from wellops.api import app as api
    c = TestClient(api.app)
    r = c.post("/api/ingest", files={"file": ("notes.txt", b"Crew reported a loose flange at the north pad.", "text/plain")},
               headers={"X-User-Id": "U-201", "X-Chat-Id": "ing"})
    assert r.status_code == 200 and r.json()["kind"] == "document" and r.json()["docs"] == 1
    h = c.get("/api/ingest/history").json()["items"]
    assert h[0]["file"] == "notes.txt" and h[0]["principal"] == "U-201"
    assert c.post("/api/ingest", content=b"x").status_code == 400
    assert c.get("/api/media/MED-nope").status_code == 404
