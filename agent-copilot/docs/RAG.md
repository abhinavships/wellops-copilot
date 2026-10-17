# RAG layer (`wellops.rag`)

`search_knowledge` answers "why / what does the manual say / what did the report or note say" questions with
verbatim passages and citations. Numbers, counts and statuses stay with the typed tools; this layer only finds text.

## Architecture

```
docs, reports, notes, inspections.notes, media ──chunk.py──▶ kb_chunks (DuckDB) ──PRAGMA create_fts_index──▶ BM25
                                                                │ sha1(text)
                                                                ▼
                                    Gemini embeddings (OpenAI-compatible) ──▶ kb_vectors (sha, model, FLOAT[])
query ─▶ BM25 top 60 ─┐
query ─▶ embed (memoised) ─▶ cosine top 60 ─┴─▶ reciprocal-rank fusion (k=60) ─▶ collapse identical text ─▶ top k
```

- Store: DuckDB only (`kb_chunks`, `kb_vectors`, FTS schema `fts_main_kb_chunks`).
- `rag.search_knowledge(deps, query, k=6, asset_id=None, kind=None, start=None, end=None)` returns a `ToolOut`
  (`finish()`), so handles, DQ flags, the verifier and the evidence drawer work as for every other tool.
  Rows: `rank, score, kind, id, title, section, asset_id, date, author, span, snippet, same_text_count`.
  Citations come first: `doc` (`SOP-SR §4`), `report` (`MNT-0151-025`, span), `note` (`CTX-…`), `inspection` (`INS-…`).
- MCP: one tool, `search_knowledge`, in `mcp_server.py`.

## Chunking (`rag/chunk.py`)

| Source | Chunk | Kept |
|---|---|---|
| `docs` (manuals, SOPs, uploaded `UP-*`) | one per section; sections > 1650 chars split on blank lines (≤ 1100 chars) | doc_id, section, title, span when split |
| `reports` | one per `##` section ("header" = preamble); long sections split on paragraphs | maintenance_id, asset, technician, started_at, **char span into `reports.text`** |
| `notes` | one per note | note_id, asset_id, author, created_at |
| `inspections.notes` | one per non-empty note (kind `note`, cited as the inspection) | inspection_id, asset, inspector, inspected_at |
| `media` (if present) | caption + legible text + tags | media_id, asset_id, uploader |

Every chunk text is an exact slice of its source (`report.text[s:e] == chunk.text`, tested), so a span highlights
the passage verbatim.

## Ranking

- BM25: DuckDB `fts`, porter stemmer, English stopwords, digits kept (`H2S`, `SR-01`, `24`). Title + text indexed.
- Dense (optional): `gemini-embedding-001`, 768 dims, cosine via `list_cosine_similarity` (brute force; 6.7k distinct
  texts in the sample fleet is milliseconds).
- Fusion: RRF, `1/(60 + rank)` summed over both lists. Without vectors, or when the API fails, BM25 alone (the tool notes say which).
- Each ranker keeps one candidate per distinct text (newest copy), so 217 identical "Pump jack not moving." notes
  cannot fill the pool; the result shows the newest and `same_text_count` says how many others match.
- Filters: `asset_id` (that well's records; manuals/SOPs stay eligible), `kind` (doc|report|note|media),
  `start`/`end` dates, and the run's as-of clock (records written after it are invisible; docs/media are timeless).

## Index maintenance

- `rag.reindex(db, ds)` rebuilds chunks from the source tables, diffs them by chunk id + a revision hash, deletes or
  inserts only what changed, and rebuilds the FTS index only if something changed (about 0.7 s for 16k chunks; a no-op
  reindex is about 0.1 s).
- Called at the end of `substrate.ingest.ingest()` (BM25 only) and from `ingest/pipeline.py` after a document, image or
  known-table upload (embeds only the chunks that upload added).
- First `search_knowledge` call on a DB without an index builds the BM25 index (no embeddings on the question path).
- Backfill vectors: `uv run python -m wellops.rag.index [dataset]`, or `python -m wellops.substrate.ingest` (its
  `main()` fills vectors after ingest).

## Cost guards

- Each distinct chunk text is embedded once: vectors are cached in `kb_vectors` keyed by `(sha1(text), model@dims)`.
  Templated text helps here: 16.2k chunks → 6.7k distinct texts (≈ 1.1M tokens for the whole sample fleet).
- Requests are batched (100 inputs each) and committed every 500, so a failure keeps what was already paid for.
- `RAG_EMBED_MAX` (default 4000) caps new embeddings per run; docs, media and notes go first, then newest reports.
- `RAG_EMBED=false` turns the dense layer off (tests set this; they use a fake embedder via `rag.set_embedder`).
- Query vectors are memoised (512 entries) and use an 8 s timeout; one retry on 429/5xx/disconnect, then the dense
  layer is off for 120 s and search runs on BM25 alone.
