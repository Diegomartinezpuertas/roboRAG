# The RAG pipeline — what is stored, how it is embedded, how it is retrieved

This document describes the *mechanics* of the robot's semantic memory: what
goes into it, how text becomes vectors, and how those vectors become part of a
planning prompt.

For the separate question of **whether any of it helps** — measured with an
ablation — see [rag-analysis.md](rag-analysis.md). For the service and message
signatures, see [api_reference.md](api_reference.md).

---

## 1. Three collections

All three live in one ChromaDB persistent store at `$ROBOT_WS/data/chroma_db`,
created on `rag_node` startup and configured with **cosine** distance
(`metadata={'hnsw:space': 'cosine'}`).

| Collection | Holds | Written by | Survives a wipe? |
|---|---|---|---|
| `knowledge_base` | Static facts about the world and the robot's own capabilities | Ingested from `data/knowledge/*.md` | Yes — the Markdown is committed |
| `semantic_map` | Places and objects **with their map-frame coordinates** | The robot itself, while exploring; the dashboard, when you name a zone | No — rebuilt by re-exploring or re-seeding |
| `task_history` | What was asked, and what actually happened | `report_skill` writes a JSON log per task; `rag_node` ingests them | No — regenerated from `data/logs/` |

`data/chroma_db/` and `data/logs/` are **not** committed (they are runtime
state); `data/knowledge/` **is**.

### What is actually in `knowledge_base`

Three committed Markdown files:

- **`environment_rules.md`** — the Gazebo world's layout, the robot's spawn
  pose, navigation constraints (doorway widths, the software-rendered physics
  caveat), and the rule that the robot must never invent coordinates.
- **`perception_capabilities.md`** — what `perceive` and `scan_360` can and
  cannot report. This one matters: it is what stops the planner assuming an
  object detector exists (see §6).
- **`task_templates.md`** — worked goal→plan examples, which act as few-shot
  guidance for the planner's JSON output.

### What is actually in `semantic_map`

Two sources, the same format:

1. **Self-built.** Every place the robot reaches while exploring is described
   by the classical scene descriptor (dominant colours + LIDAR clutter, no ML —
   [ADR-014](decisions/ADR-014-classical-scene-descriptor.md)) and stored with
   the pose where it was observed. This is what makes "go to the white, open
   room" resolvable without anyone seeding it by hand.
2. **User zones.** Naming an area in the dashboard upserts it here too, so a
   zone is reachable both by SQLite lookup and by semantic query.

The document ID is derived from the pose rounded to a 0.5 m grid
(`scene-{x}-{y}`), so **revisiting a place updates its description** rather
than accumulating near-duplicates. IDs are stable and `upsert` is used
throughout, which is what makes re-ingestion idempotent.

---

## 2. From text to a stored vector

```
Markdown / observation / task log
        │
        ▼  chunking (knowledge_base only)
   chunk_markdown()  — split on headers, each header kept with its body
        │
        ▼  embedding
   OllamaEmbedder.embed_batch()  → POST /api/embed on the Ollama server
        │                            model: bge-m3 (default)
        ▼
   ChromaManager.add()  → collection.upsert(ids, documents, embeddings, metadatas)
```

### Chunking

Only `knowledge_base` is chunked, by `chunk_markdown()`
(`robot_rag/knowledge_base.py`). It splits on Markdown headers and **keeps each
header with everything up to the next one**.

The obvious alternative — splitting on blank lines — was rejected because it
separates `## Safety rules` from the rules beneath it, so a query for "safety
rules" retrieves a bare heading with no rule text: a perfect lexical match that
carries zero information. Sections are small enough here that no secondary
size-based split is needed.

`semantic_map` and `task_history` are not chunked: each observation and each
task log is already one short, self-contained document.

### Embedding

`OllamaEmbedder` is a thin wrapper over the Ollama client. One HTTP call embeds
a whole batch, so ingesting 30 knowledge chunks is a single request.

The model is a ROS parameter (`rag_node/embedding_model`), default **`bge-m3`**.
That default is a measured decision, not a preference: with Spanish queries over
this project's English documents, `nomic-embed-text` puts the right document
first only **43%** of the time and its mean separation margin is *negative*,
while `bge-m3` reaches **86%** with a positive margin. Both are ~100% in
English. See [rag-analysis.md §2.4](rag-analysis.md).

> **If you change the embedding model, delete `data/chroma_db` and re-ingest.**
> Vector dimensions differ between models, and a collection built with one
> cannot be queried with another. Consider recalibrating `rag_score_threshold`
> too — it is tuned per embedder.

### Storage

`ChromaManager.add()` always `upsert`s. Callers pass stable IDs
(`{file_stem}-{chunk_index}`, `scene-{x}-{y}`, `task-{filename}`), so
re-ingesting the same source updates in place instead of duplicating. Metadata
(source file, pose, confidence, zone) is stored alongside but — see §4 — is not
what the planner sees.

---

## 3. When each collection is (re)ingested

| Collection | Trigger | Behaviour |
|---|---|---|
| `knowledge_base` | `rag_node` startup | **Skipped if already populated.** Edit a knowledge file and you must wipe `data/chroma_db` (or the collection) for it to take effect. |
| `task_history` | `rag_node` startup | **Always re-scans** `data/logs/`. Logs accumulate as tasks complete, and IDs come from filenames, so upsert picks up everything finished since the last start. |
| `semantic_map` | Live, per observation | Written through the `/rag/update_map` service whenever the robot perceives a place or you save a zone. |

The asymmetry is deliberate: the knowledge base is static and re-embedding it
on every launch would waste time, while task history is append-only by nature.

---

## 4. Retrieval, and how context reaches the prompt

On every goal, `llm_planner_node` queries **all three collections**:

```python
rag_context += self._retrieve_context(goal_text, 'knowledge_base', top_k=3)
rag_context += self._retrieve_context(goal_text, 'semantic_map',   top_k=3)
rag_context += self._retrieve_context(goal_text, 'task_history',   top_k=2)
```

Each call embeds the goal with the same model, asks ChromaDB for the nearest
documents, and converts distance to similarity — ChromaDB returns cosine
*distance*, so `similarity = 1 - distance`, clamped to `[0, 1]`.

### The relevance threshold

Hits scoring below `rag_score_threshold` (default **0.40**, calibrated for
bge-m3) are **dropped**, not passed to the planner.

This is the single most important retrieval decision in the project. Top-k
always returns *something*; with no floor, an off-topic document gets stuffed
into the prompt as though it were ground truth. An irrelevant context is
strictly worse than no context, because the planner treats what it is given as
fact. Correct matches with bge-m3 start around 0.43, so 0.40 sits just below
the real signal. See [ADR-011](decisions/ADR-011-rag-quality-zones-sqlite.md).

### Coordinates travel inside the document text

`QueryRAG` returns document **text only** — not metadata. So `SemanticMap`
writes the pose into the document itself:

```
area at (x=1.85, y=-0.42) in kitchen: predominantly white, an open, uncluttered space
```

This is not decoration. It is the **only channel** by which the planner can
learn where a remembered place is, and therefore the mechanism the whole
benchmark measures: with this text in the prompt the planner emits
`navigate(x=1.85, y=-0.42)`; without it, it falls back to blind `explore`. See
[ADR-012](decisions/ADR-012-navigable-rag-post-execution-report.md).

The surviving fragments are formatted into the prompt as a bulleted
`RETRIEVED CONTEXT:` block, alongside `KNOWN ZONES:` (read from SQLite, not
RAG) and the goal.

---

## 5. Inspecting it yourself

```bash
# Query a collection directly
ros2 service call /rag/query robot_interfaces/srv/QueryRAG \
  "{query_text: 'where is the kitchen', collection_name: 'knowledge_base', top_k: 3}"

# What does the robot remember about places?
ros2 service call /rag/query robot_interfaces/srv/QueryRAG \
  "{query_text: 'white open room', collection_name: 'semantic_map', top_k: 5}"
```

The response carries `contexts` and the `scores` the threshold is applied to,
which is the quickest way to see whether a disappointing plan was a retrieval
problem or a planning problem.

To reset the memory entirely: stop the stack, `rm -rf data/chroma_db`, restart.
`knowledge_base` re-ingests automatically; `semantic_map` needs re-exploration
or `eval/seed_memory.py`.

---

## 6. A failure mode worth knowing about

The knowledge base is *retrieved as fact*, so a stale entry is not a
documentation problem — it is a live input to the planner.

This bit the project: `object_catalog.md` described a Qwen2.5-VL vision model
recognising fifteen object classes. The VLM was removed in
[ADR-014](decisions/ADR-014-classical-scene-descriptor.md) and replaced by a
classical colour + clutter descriptor that cannot name objects at all — but the
document stayed, and kept being retrieved, telling the planner about a
capability the robot no longer had. It has been replaced by
`perception_capabilities.md`, which states what perception actually reports.

The general rule: **when a capability changes, the knowledge base is part of
the code that has to change with it.**

### The same trap in `task_history`: absolute coordinates outlive their map

`task_history` stores what happened, and past outcomes often contain absolute
coordinates — "moved to the base at (x=-0.30, y=-1.06)". Those coordinates are
only meaningful relative to the SLAM map that was live when the task ran. This
sim builds its map fresh on every launch (no saved map — see the roadmap), so
**a coordinate logged in one session points somewhere else in the next.**

Retrieval does not know that. A later goal like "go to the base" can match the
old log, and the planner will copy a coordinate from a map that no longer
exists — over the correct, freshly-observed entry sitting right next to it in
`semantic_map`.

This surfaced while running the benchmark end-to-end: with development logs
from earlier sessions present, the `zone_nav` **control** task failed in both
conditions, because the planner navigated to a stale logged coordinate instead
of resolving the zone by name. It had nothing to do with RAG on vs off — it was
session state leaking into the measurement, the same class of problem as a
leftover zone name (see `seed_memory.py --reset-zones` and
[EVALUATION.md](EVALUATION.md)). `data/logs/` is not committed, so a fresh
clone does not hit it; a development machine that has run real tasks does.

Two honest takeaways:

1. **For the benchmark:** start from clean session state — empty `data/logs/`
   and `data/chroma_db`, exactly what a fresh clone has. EVALUATION.md says so.
2. **For the design:** storing absolute coordinates in a memory that outlives
   the map is a latent correctness bug, not just a benchmark nuisance. The
   real fix is a persistent, versioned map (roadmap item 4) so that a logged
   pose keeps meaning what it meant. Until then, `task_history` is best treated
   as *episodic* memory — useful for "have I done something like this before",
   not as a source of coordinates to navigate to.
