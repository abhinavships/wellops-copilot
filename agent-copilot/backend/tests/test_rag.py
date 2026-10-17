"""RAG layer: chunking spans, BM25 retrieval, hybrid fusion with a fake embedder, degradation, incremental reindex."""

import hashlib
import math
import re
from datetime import date

import pytest

from wellops import rag
from wellops.contracts import RunDeps
from wellops.ingest import pipeline
from wellops.rag import embed as E
from wellops.rag.chunk import report_chunks
from wellops.rag.index import embed_missing


def fake_embed(texts: list[str]) -> list[list[float]]:
    """Hashed bag of 5-char stems (words of 4+ chars): deterministic, offline, related forms share a bucket."""
    out = []
    for t in texts:
        v = [0.0] * 512
        for w in set(re.findall(r"[a-z0-9]{4,}", t.lower())):
            v[int(hashlib.md5(w[:5].encode()).hexdigest(), 16) % 512] += 1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        out.append([x / n for x in v])
    return out


@pytest.fixture
def fake(db):
    rag.set_embedder(fake_embed)
    yield
    rag.set_embedder(None)
    db.run("DELETE FROM kb_vectors WHERE model='fake@test'")


def ids(out):
    return [r["id"] for r in out.preview]


def test_report_chunks_are_exact_slices(db):
    text = db.rows("SELECT text FROM reports WHERE maintenance_id='MNT-0151-025'")[0]["text"]
    chunks = [c for c in report_chunks(db.con, "sample") if c.src_id == "MNT-0151-025"]
    assert len(chunks) >= 6 and {c.section for c in chunks} >= {"header", "What happened", "Compliance check"}
    assert all(text[c.span_start:c.span_end] == c.text for c in chunks)
    n_docs = db.rows("SELECT count(*) n FROM docs WHERE dataset_id='sample'")[0]["n"]
    assert db.rows("SELECT count(*) n FROM kb_chunks WHERE dataset_id='sample' AND kind='doc'")[0]["n"] >= n_docs


def test_pump_jack_not_moving_finds_inspection_note(deps):
    out = rag.search_knowledge(deps(), "pump jack not moving")
    top = out.preview[0]
    assert top["id"] == "INS-0164-0205" and top["kind"] == "note"  # newest of the identical notes
    assert top["same_text_count"] > 100 and "Pump jack not moving" in top["snippet"]
    assert out.citations[0].kind == "inspection" and out.citations[0].id == "INS-0164-0205"
    assert ids(rag.search_knowledge(deps(), "pump jack not moving", asset_id="well-0164"))[0] == "INS-0164-0205"


def test_written_notice_finds_sop_sr_section_4(deps):
    out = rag.search_knowledge(deps(), "written notice within 24 hours")
    assert out.preview[0]["id"] == "SOP-SR §4"
    assert any(c.kind == "doc" and c.id == "SOP-SR §4" for c in out.citations)
    assert "written notice" in out.preview[0]["snippet"]


def test_containment_finds_report_with_span(db, deps):
    out = rag.search_knowledge(deps(), "containment", asset_id="WELL-0151", kind="report")
    assert "MNT-0151-025" in ids(out) and all(r["asset_id"] == "WELL-0151" for r in out.preview)
    c = next(c for c in out.citations if c.kind == "report" and c.id == "MNT-0151-025")
    text = db.rows("SELECT text FROM reports WHERE maintenance_id='MNT-0151-025'")[0]["text"]
    assert c.span and "contained" in text[c.span[0]:c.span[1]]


def test_filters_and_as_of(deps):
    assert {r["kind"] for r in rag.search_knowledge(deps(), "spill", kind="doc", k=10).preview} == {"doc"}
    early = RunDeps(principal="U-201", chat_id="c1", today=date(2026, 10, 1))
    assert "INS-0164-0205" not in ids(rag.search_knowledge(early, "pump jack not moving"))  # not yet inspected
    win = rag.search_knowledge(deps(), "flange gasket", kind="report", start="2026-09-01", end="2026-09-30", k=10)
    assert win.row_count and all("2026-09-01" <= r["date"] <= "2026-09-30" for r in win.preview)
    with pytest.raises(Exception):
        rag.search_knowledge(deps(), "x", kind="bogus")


def test_hybrid_fusion_with_fake_embedder(db, deps, fake):
    got = embed_missing(db, "sample", limit=100)
    assert got["embedded"] == 100 and got["embed_pending"] > 0 and "embed_capped" in got  # per-run cap
    got = embed_missing(db, "sample", limit=100_000)
    assert got["embedded"] > 1000 and got["embed_pending"] == 0
    assert embed_missing(db, "sample")["embedded"] == 0  # cached by sha1(text): never embedded twice
    out = rag.search_knowledge(deps(), "pump jack not moving", k=10)
    assert any("reciprocal-rank fusion" in n for n in out.notes)
    assert out.preview[0]["id"] == "INS-0164-0205"  # first in both rankings
    hits, _ = rag.retrieve(db, "sample", "pump jack not moving", 10)
    assert hits[0]["bm25"] and hits[0]["cosine"] and hits[0]["rrf"] > 1 / 61  # fused from both lists


def test_embedder_failure_degrades_to_bm25(db, deps, fake):
    embed_missing(db, "sample", limit=50)

    def boom(texts):
        raise E.EmbedError("HTTP 429")
    rag.set_embedder(boom)
    out = rag.search_knowledge(deps(), "written notice within 24 hours")
    assert out.preview[0]["id"] == "SOP-SR §4"
    assert any("BM25 only" in n for n in out.notes)


def test_incremental_reindex_on_new_note_and_upload(db, deps, tmp_path):
    db.run("""INSERT INTO notes VALUES ('sample','CTX-9999-001','WELL-0007', TIMESTAMPTZ '2026-10-16 08:00:00+00','U-201',
              'operator_note',NULL,NULL,'Polished rod clamp slipped twice this week; zebra-striped wear marks.',NULL,NULL)""")
    try:
        st = rag.reindex(db, "sample", embed=False)
        assert st["added"] == 1 and st["fts_rebuilt"]
        assert ids(rag.search_knowledge(deps(), "zebra striped wear on the rod clamp"))[0] == "CTX-9999-001"
    finally:
        db.run("DELETE FROM notes WHERE note_id='CTX-9999-001'")
    assert rag.reindex(db, "sample", embed=False)["removed"] == 1
    assert rag.reindex(db, "sample", embed=False)["fts_rebuilt"] is False  # nothing changed: no rebuild

    p = tmp_path / "flare_stack_guide.md"
    p.write_text("# Flare stack guide\n\n## 1. Pilot\n\nRelight the quokka-pattern pilot within 15 minutes of a flameout.\n")
    try:
        assert pipeline.ingest_file(p, p.name, "U-201", db=db).kind == "document"
        out = rag.search_knowledge(deps(), "quokka pilot flameout")
        assert out.preview[0]["id"] == "UP-flare_stack_guide §1"
    finally:
        db.run("DELETE FROM docs WHERE doc_id='UP-flare_stack_guide'")
        rag.reindex(db, "sample", embed=False)
