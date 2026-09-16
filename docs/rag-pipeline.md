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

- **`environment_rules.md`** — the Gazebo world's layout and the robot's spawn
  pose, where coordinates come from (exploration, named zones, remembered
  places), the rule that a place with no known coordinates is explored for,
  never guessed, and what Nav2 will and will not do. It used to list a
  30-minute operating limit and a retry-by-exploring fallback that nothing in
  the code implements; both were removed on 2026-09-16, and the planning suites
  re-run, as ADR-031 requires.
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

A zone or a scene whose name denotes a **kind of room** carries what that room
is for, in Spanish and English:

```
kitchen at (x=1.75, y=-0.75) in cocina: User-defined zone "cocina" — a kitchen
(la cocina) — covering x[1.00, 2.50] y[-1.50, 0.00] in the map frame. This is
the kitchen: where meals are cooked and food is prepared, where the dishes are
washed... Es la cocina: donde se cocina y se prepara la comida, donde se suele
cocinar... The robot can navigate to it or explore inside it by name ("cocina").
```

That is what makes **"ve donde se suele cocinar"** resolve, when `cocina` plus a
bounding box did not: the name is the user's claim about the kind of room, and
the claim is expanded into the text that gets embedded. Names that match no
known room type (`estacion_a`) stay plain — measured effect and the reasoning in
[ADR-022](decisions/ADR-022-room-semantics.md).

**One memory per place and look.** A new scene observation carries a
`group_key` — its set of dominant colours plus clutter class — and `rag_node`
folds it into an existing memory of the same key, map session and zone within
`scene_merge_radius` (2 m), keeping that memory's id and anchor pose and
counting the observation. Only a genuinely new place gets a new id (the pose on
a 0.5 m grid, `scene-{x}-{y}`). Before this, that grid id was the only dedupe,
and a room was stored once per half-metre the robot reached: a real store held
28 scene memories with 12 distinct descriptions, one of them eleven times
([ADR-025](decisions/ADR-025-scene-memory-merging.md)). IDs are stable and
`upsert` is used throughout, which is what makes re-ingestion idempotent.

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
a whole batch, so ingesting the whole knowledge base (12 chunks today) is a
single request.

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
| `knowledge_base` | `rag_node` startup | **Compared with the files on disk.** Unchanged → nothing is embedded. Any chunk added, edited or removed → the whole collection is replaced (it holds nothing but these files). Before 2026-09-16 it was skipped whenever populated, so an edit never reached an existing store ([ADR-031](decisions/ADR-031-knowledge-base-is-planner-input.md)). |
| `task_history` | `rag_node` startup | **Always re-scans** `data/logs/`. Logs accumulate as tasks complete, and IDs come from filenames, so upsert picks up everything finished since the last start. |
| `semantic_map` | Live, per observation | Written through the `/rag/update_map` service whenever the robot perceives a place or you save a zone. |

The asymmetry is deliberate: the knowledge base changes only when someone edits
it, so re-embedding it on every launch would waste time, while task history is
append-only by nature.

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
RAG, each with its centre) and the goal.

### Retrieved coordinates are checked before the robot moves

Putting coordinates in the prompt gives the planner something to copy, and it
does not always copy the right one. Asked for "estacion_d" when only a, b and c
are remembered, it navigated to estacion_a's coordinates in every run. So the
plan is checked against the same context it was built from before anything
executes (`plan_validation.py`,
[ADR-032](decisions/ADR-032-plan-check-before-execution.md)):

- a `navigate(zone=...)` must name a zone that exists;
- a `navigate(x, y)` must land on a known place — a retrieved memory or a zone
  centre, within 0.25 m;
- a known place must not be borrowed: a second, narrow Qwen call lists the
  places the goal asks for and the ones it asks for that are missing, and a
  step that goes somewhere else while something is missing becomes `explore`.

The retrieval side is unchanged by this; what changes is that a retrieved
coordinate is no longer enough on its own to send the robot somewhere.

### An alternative lookup, measured

`memory_source: sql` replaces the similarity search over `semantic_map` with a
read-only SELECT that Qwen writes over a SQLite copy of the same places
(`sql_memory.py`). It exists for one experiment and is not the default:
[ADR-033](decisions/ADR-033-llm-to-sql-place-memory.md) measured it against RAG,
and at a dozen places it tied on every lookup and lost the spatial comparisons
([rag-analysis §2.10](rag-analysis.md)). Its prompt lists the scene
descriptor's phrases and colour names, so a change to that vocabulary has to
reach the prompt too.

---

## 5. Inspecting it yourself

The dashboard's **memory panel** is the direct route: pick a collection, see how
many entries it holds, read each one as a card with its coordinates, zone and
provenance, watch the coordinate memories appear as markers on the SLAM map, and
type a question to see what the robot would retrieve for it and with what score.
Memories from a dead map session are greyed out rather than hidden (see §6).

Underneath it is `/rag/inspect`, which is also usable directly — and unlike
`/rag/query`, it returns ids and metadata, and it can list without a query:

```bash
# Browse what is stored, as stored (no embedding call)
ros2 service call /rag/inspect robot_interfaces/srv/InspectMemory \
  "{collection_name: 'semantic_map', query_text: '', limit: 5, active_map_only: true}"

# Search it, with the metadata the planner never sees
ros2 service call /rag/inspect robot_interfaces/srv/InspectMemory \
  "{collection_name: 'semantic_map', query_text: 'donde se suele cocinar', limit: 3}"
```

To see exactly what the *planner* gets, including the score threshold's effect:

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

To fold duplicates stored before merge-on-write existed, stop the stack and run
`ros2 run robot_rag compact_memory` (dry run) and then with `--apply` (backs the
store up first; `--prune-untagged` also drops memories with no map session).

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

### An example that overrides a rule

It bit a second time, more quietly. A worked example added to
`task_templates.md` — "if the context holds a place whose description matches,
navigate straight to it. **Do not explore.**" — sat next to the rule in
`environment_rules.md` that a place with no known coordinates must be explored
for, never guessed. For "Ve al garaje", with no garage anywhere in memory, the
7B planner followed the example rather than the rule: explore, then
`navigate` to a *different* remembered station's coordinates. That cost the
impossible-goal control 3/3 → 0/3 and went unnoticed for two months: the change
landed ten minutes after the last benchmark run, and an existing store would not
even have received it (ingestion was skipped once populated). A prompt replay
changing only the knowledge files isolated it; removing the template
restored 3/3. Two consequences, recorded in
[ADR-031](decisions/ADR-031-knowledge-base-is-planner-input.md): the knowledge
base now re-syncs when its files change, and **any edit to `data/knowledge/`
is followed by a re-run of the planning suites** — for the planner it is a
prompt change, not a docs change.

### The same trap in `task_history`: absolute coordinates outlive their map

`task_history` stores what happened, and past outcomes often contain absolute
coordinates — "moved to the base at (x=-0.30, y=-1.06)". Those coordinates are
only meaningful relative to the SLAM map that was live when the task ran. This
sim builds its map from scratch on every launch unless a saved map is loaded, so
**a coordinate logged against one map can point somewhere else in the next.**

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

**The fix — map-session versioning ([ADR-019](decisions/ADR-019-map-session-memory-versioning.md)).**
Every coordinate memory is now tagged with a **map-session id** (the map it was
written against), and retrieval of `semantic_map` and `task_history` is filtered
to the active session — so a pose from a dead map is simply not returned. A
scene written under one map is invisible under any other, even though ChromaDB
still physically holds it. Which session is active is set by the launch: a map
built from scratch gets the id of its frame, `fresh_<world>_x<spawn x>_y<spawn y>`,
so fresh maps from the same spawn pose share memories — their coordinates
coincide — while another world or spawn pose starts empty
([ADR-028](decisions/ADR-028-memory-session-per-map-frame.md)). Saving the SLAM map (the `save_map`
skill, or "Guardar mapa" in the dashboard) and relaunching with `saved_map:=<id>`
preserves the pairing — SLAM loads the map and the memory session is pinned to
the same id — so a map's memories come back on purpose
([ADR-026](decisions/ADR-026-shipped-map-and-demo-launch.md)).

This means the benchmark's clean-state requirement (empty `data/logs/` and
`data/chroma_db`) is now a convenience, not a correctness crutch — a leftover
log from an old map is scoped out, not acted on. Starting clean is still the
simplest way to reproduce the published numbers exactly, and EVALUATION.md
keeps the steps.
