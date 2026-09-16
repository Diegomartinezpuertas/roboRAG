# ADR-025: One memory per place and look — merge re-observations on write

**Date:** 2026-09-16
**Status:** Accepted

## Context

The memory viewer (ADR-024) made a problem visible that retrieval had been
quietly suffering from: **the self-built memory was mostly copies.** A real
store from normal use held 38 `semantic_map` entries, and the scene memories
among them told the story:

- 28 scene memories, **12 distinct descriptions** — "predominantly gray and
  brown, an open, uncluttered space" stored **eleven** times.
- The same look described two ways: "gray and brown" ×11 and "brown and gray"
  ×3. Dominant colours are ranked by pixel share, and the order flips on a few
  pixels.
- 11 entries with no map session at all, written before ADR-019 and
  unreachable by any retrieval, still in the store.

The cause was the scene id: the robot's pose rounded to a **0.5 m grid**
(ADR-012). That dedupes a re-visit of the *same half-metre*, but a room is many
half-metres, and exploring reaches a new frontier inside it every metre or so.
Each one became another memory of the same room.

This is not cosmetic. The planner retrieves `semantic_map` with `top_k=3`; when
three hits are the same grey room at three nearby coordinates, the other two
slots are wasted, and anything else relevant never reaches the prompt.

## Decision

A new observation is **folded into an existing memory** when all of these hold:

1. **Same look.** Its *group key* — the set of dominant colours (order-free)
   plus the clutter class — matches. The obstacle-group count is sensor noise
   and is left out.
2. **Same map session and same zone.** Coordinates from another map are not
   comparable (ADR-019), and a kitchen never merges with the living room next
   door, however alike they look.
3. **Within `scene_merge_radius`** (default 2.0 m, a `rag_node` parameter) of
   that memory's anchor.

Merging keeps the existing memory's **id and anchor pose**, refreshes its
description and timestamp, and increments an `observations` count.

The pieces:

- `SemanticObject.msg` gains `group_key`. The **producer** sets it from the
  descriptor's *structured* output (`colors`, `clutter`), never from its text:
  the skills node knows what it measured. Objects without a key — zones,
  landmarks, seeded benchmark fixtures — keep plain upsert-by-id semantics.
- The **store** decides. `SemanticMap.upsert_object` looks up same-key,
  same-session, same-zone memories and merges into the nearest one within the
  radius. The decision itself lives in `robot_rag/scene_merge.py`, pure Python,
  unit-tested in layer 1 (ADR-018).
- `UpdateMap.srv` returns `object_id` and `merged`, so the skill reports the id
  the observation really ended up under.
- **Existing duplicates** are collapsed by `ros2 run robot_rag compact_memory`,
  which applies the same rule to stored data: a dry run by default, `--apply`
  refuses while `rag_node` runs and copies the store aside first. Keys for old
  memories are recovered from the descriptor's historical sentence format — a
  one-off migration of known data, not a runtime path. `--prune-untagged` also
  deletes the memories with no map session. Memories tagged with another
  session are never touched: reloading that map brings them back.
- **The viewer groups what storage rightly keeps apart:** facts about the
  exact same spot (a benchmark landmark and its seeded description share
  coordinates on purpose) and identical documents (the same task logged run
  after run) show as one card, with the facts listed. It also shows how many
  observations each memory stands for.

Measured on a copy of that store: **38 → 26** memories from merging alone,
**38 → 18** with pruning, and running the tool a second time changes nothing.

## Rationale

**Why the anchor never moves.** If the kept pose followed each new observation,
a long corridor of one colour would drag a single memory along with the robot
and lose every place it had been. Anchored, a live group covers at most one
radius. The anchor is also a pose the robot actually reached, which a centroid
is not: the mean of an L-shaped room can be inside a wall.

**Why compaction had to iterate.** The first version clustered greedily in id
order. On the real store, a second run folded two more memories, because two
survivors of the first pass lay 1.86 m apart. Merge-on-write guarantees one
invariant live — *no two memories of the same look, zone and session within
one radius* — so compaction now repeats until that invariant holds. It is
idempotent, which a test pins.

**Why the producer supplies the key and the store applies the radius.** Only
the skills node knows what was measured; only `rag_node` knows what is already
stored. Parsing the description in `rag_node` would have coupled the store to
another package's English sentences. A single `group_key` field keeps each side
honest about what it knows.

**Why 2 m.** A room in the TurtleBot3 house is 3–4 m across and frontiers are
reached roughly a metre apart. At 2 m a room's look collapses to one or two
memories, while distinct spots in a large open area stay distinct. It is a
parameter because a different house is a different answer.

**Alternatives rejected:**

- *A coarser grid (2 m cells).* No query needed, but two observations 10 cm
  apart on either side of a cell border stay duplicates forever, and the colour
  order bug survives untouched.
- *Dedupe by exact document text.* Misses "brown and gray" vs "gray and brown",
  and merges look-alike rooms at opposite ends of the house.
- *Deduplicate at query time only.* Hides the copies from the planner and
  leaves the store growing without bound, with the viewer still full of noise.
- *Delete memories from other map sessions.* Irreversible, and contradicts
  ADR-019: a saved map's memories come back when the map does.

## Consequences

- Retrieval's top-k is no longer spent on copies of one place, and the store
  stops growing with every exploration of an already-known room.
- Two interface changes, both backward-compatible: `SemanticObject.group_key`
  (empty = old behaviour) and `UpdateMap` response `object_id`/`merged`.
  Existing callers — dashboard zone indexing, the benchmark harness,
  `seed_memory.py` — set no key and behave exactly as before.
- Benchmark fixtures (`scene-seed-*`, `landmark-*`) are never merged or
  compacted, so the published benchmark conditions are unchanged. The
  self-built memory a live run accumulates is smaller: fewer, better entries.
- Merging is spatial and categorical, not semantic: two observations that look
  the same to the descriptor are one memory. That is exactly as coarse as the
  descriptor itself (ADR-014), and becomes finer the day perception does.
- `perceive`/`scan_360` results gain `merged`, and `object_id` now names the
  memory the observation was stored under.
