# ADR-001: ChromaDB instead of Qdrant

**Date:** 2026-07-14
**Status:** Accepted — the embedding model it mentions (`nomic-embed-text`) was
later replaced by `bge-m3` on measured data ([rag-analysis §2.4](../rag-analysis.md))

## Context

The robot needs a local vector store for its semantic RAG memory: detected
objects (`semantic_map`), static environment documents (`knowledge_base`),
and task history (`task_history`).

## Decision

Use ChromaDB with `PersistentClient` and on-disk persistence at
`data/chroma_db/`. Every collection is created with `hnsw:space: cosine`.

## Rationale

- Simplest API for prototyping (`get_or_create_collection`, `upsert`, `query`).
- Automatic persistence with no external server — fits the requirement of
  running everything locally on WSL2 with no network dependencies.
- Straightforward integration with embeddings generated via Ollama
  (`nomic-embed-text`), no extra adapter needed.

## Consequences

- Single-process only (not distributed). Acceptable: `rag_node` is the only
  process opening the `PersistentClient`.
- Migrate to Qdrant if scaling to multiple robots or concurrent access from
  several processes ever becomes necessary.
