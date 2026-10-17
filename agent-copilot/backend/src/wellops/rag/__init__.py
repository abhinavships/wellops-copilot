"""Retrieval-augmented knowledge layer: chunk docs/reports/notes/media into DuckDB, BM25 + optional dense, RRF fusion."""

from .embed import set_embedder
from .index import reindex, retrieve
from .search import search_knowledge

__all__ = ["reindex", "retrieve", "search_knowledge", "set_embedder"]
