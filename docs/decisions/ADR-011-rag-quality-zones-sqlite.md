# ADR-011: RAG quality fixes and zones in SQLite (supersedes ADR-008)

**Date:** 2026-07-14
**Status:** Accepted

## Context

An audit with real queries against the existing `semantic_map` /
`knowledge_base` revealed three concrete problems (not just general
weakness):

1. **Chunking split headers from their content.** `knowledge_base.py` split
   the `.md` files on blank lines, so `"## Safety rules"` and the paragraph
   below it landed in separate chunks — querying for "safety rules" returned
   a bare heading with no rule text.
2. **`task_history` was a ghost collection.** It existed in the schema but
   nothing ever filled it — always 0 documents, even though `report_skill`
   writes one JSON per task to `data/logs/`.
3. **No relevance filter.** Everything retrieved by `/rag/query` was injected
   into Qwen's prompt without checking the score — a bad match (frequent
   given the language mismatch: English docs, Spanish queries) injected
   active noise instead of useful context.

Additionally, the user asked to persist dashboard-drawn zones in SQL instead
of the flat JSON of ADR-008, keeping deletion support.

## Decision

### Chunking
`knowledge_base.chunk_markdown()` groups each header (`#`) with all text up
to the next header instead of splitting on blank lines.

### Real `task_history`
New `robot_rag/task_history.py::TaskHistoryStore`, analogous to
`KnowledgeBase` but **without** the only-if-empty guard — task logs
accumulate over time, so `data/logs/*.json` is re-scanned on every
`rag_node` start and upserted (idempotent, IDs derived from filenames).

### Relevance filter
`RobotToolkit.call_rag_with_scores()` returns `(context, score)` pairs.
`llm_planner_node._retrieve_context()` drops anything below
`rag_score_threshold` (parameter, default 0.45) before building the prompt.
`task_history` was also added as a third collection queried per goal.

### Zones in SQLite (supersedes ADR-008)
New `robot_zones` package (`ZoneStore`, stdlib `sqlite3`, table
`zones(name, x_min, y_min, x_max, y_max, created_at)`) shared by
`robot_dashboard` (writer) and `robot_skills` / `robot_brain` (readers).
Indexing into ChromaDB `semantic_map` for natural-language lookup is
unchanged.

## Rationale

- A fresh, short-lived SQLite connection per operation (no persistent
  connection shared across threads/processes) — avoids any concurrency
  concern at this write volume without adding a server.
- `rag_score_threshold=0.45` was calibrated against measured scores: correct
  `semantic_map` matches scored 0.57–0.62, the worst incorrect
  `knowledge_base` match scored 0.40.

## Consequences

- The language mismatch (English docs, Spanish queries) remains — the
  knowledge base was not translated. With the threshold active, Spanish
  queries against `knowledge_base` sometimes retrieve nothing (better than
  noise, but a real limitation; see the embedding-model analysis in
  docs/rag-analysis.md).
