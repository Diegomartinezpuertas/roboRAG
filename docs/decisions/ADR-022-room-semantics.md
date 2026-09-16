# ADR-022: Zones carry what a room is for, not only what it is called

**Date:** 2026-09-15
**Status:** Accepted

## Context

The robot could already answer "ve a la cocina" — the zone name is in the
planner's prompt and resolves to coordinates through the SQLite zone store
(ADR-011). It could not answer **"ve donde se suele cocinar"**.

The reason is visible in what was stored. A zone created in the dashboard was
indexed into `semantic_map` as:

```
cocina at (x=1.75, y=-0.75) in cocina: User-defined zone "cocina"
covering x[1.00, 2.50] y[-1.50, 0.00] in the map frame.
The robot can navigate to it or explore inside it by name.
```

One occurrence of the word, and then coordinates. Embedded with bge-m3, that
document is not close to a sentence about cooking, and `rag_score_threshold`
(0.40, ADR-011) is calibrated to drop weak hits — so the kitchen either loses to
an unrelated landmark or never reaches the prompt at all.

The same gap affects what exploration stores. Scene descriptions come from the
classical descriptor (ADR-014): dominant colors and clutter. "Predominantly
white, an open space" is retrievable by appearance and by nothing else. Nothing
the robot records on its own says *kitchen*, because nothing it measures does.

The obvious fix — a vision-language model that names rooms — was already tried
and rejected for this simulator's software-rendered frames (ADR-014). So the
knowledge has to come from where it actually exists: the name a human chose.

## Decision

A zone name is treated as a **claim about the kind of room it is**, and that
claim is expanded into the text that gets embedded.

`robot_zones/room_semantics.py` (pure stdlib, no ROS) holds a table of eleven
room types. Each carries the name fragments that identify it in Spanish and
English (`cocina`, `kitchen`, `cocinilla`…), and one sentence per language
saying what happens there and what it holds. Matching is token-based, accent-
and plural-tolerant, so `Cocina Grande`, `bano_1` and `sala_de_estar` all
resolve.

Three places use it:

- **`dashboard_node.index_zone_in_memory`** describes a zone with
  `describe_zone()`: bounds (coordinates still travel in the document text —
  ADR-012), then the room's purpose in both languages, then the name it answers
  to. The semantic object's label becomes the room type (`kitchen`).
- **`skills_executor_node._store_scene_result`** appends `scene_context()` to
  every scene it stores inside a named zone, so an observation is retrievable
  both by how it looks and by what the room is for, and labels it with the room
  type.
- **The dashboard** suggests the recognized room names as autocomplete when a
  zone is being named, and reports back which room type it understood.

**A name that matches nothing is left alone.** `estacion_a` keeps the plain
description. The robot says only what it has grounds to say.

## Rationale

**Why the name and not perception.** Naming a room is the one moment a human
tells the robot what a place *is*. Perception here measures color and clutter;
inferring "kitchen" from "predominantly white" would be invention. The name is
real evidence, freely given, and previously thrown away after being used as a
lookup key.

**Why both languages in one document.** bge-m3 is multilingual but not free of
a cross-lingual penalty — §2.4 of [rag-analysis](../rag-analysis.md) measured
top-1 going from 43% to 86% purely by switching embedding model, which is the
size of the effect at stake. Carrying both sentences costs a few hundred bytes
per zone and removes the question.

**Why it was measured, not assumed.** `eval/room_semantics_bench.py` embeds five
zones described both ways and queries them with functional phrases in Spanish
and English. Old descriptions: **top-1 6/11**, mean score of the correct zone
0.488, and 3 of 11 queries below the planner's 0.40 threshold. With the room's
purpose attached: **top-1 11/11**, mean 0.585, none below the threshold. The
failures the old text produced are the instructive part — "donde me puedo
duchar" retrieved the *living room*, "ve al sitio donde se estudia" the
*bedroom*. On a live stack the same zone is retrieved at 0.631, ahead of every
landmark in a store of 36 memories.

**Why a table and not an LLM call.** Qwen is already loaded and could generate a
description per zone. That would make zone creation depend on the LLM being up,
make the stored text non-deterministic, and put a generation step on the path of
a UI action that must return immediately. Eleven rooms of vocabulary is a table;
tables do not have a bad day.

**Why in `robot_zones`.** Both users — the dashboard (writer) and the skills
executor (observer) — already depend on that package for the zone store, and
neither depends on the other. It is the existing shared point.

**Alternatives rejected:**

- *Enrich the planner's prompt instead of the memory.* The prompt is the input
  to the published ablation (ADR-013 / rag-analysis); changing it silently
  invalidates those numbers. Retrieval is also the right layer: the failure was
  a retrieval failure, not a reasoning one.
- *Store aliases as separate documents ("cocina" → "donde se cocina").* More
  entries competing for the same top-k, all pointing at one place, and the
  coordinates would have to be duplicated into each. One richer document per
  zone keeps `semantic_map` one-entry-per-place.
- *Ask the user for a description when naming a zone.* Nobody types a paragraph
  about a kitchen. The point of typing "cocina" is that it already means all of
  it.

## Consequences

**Verified end to end, not only in retrieval.** With a `cocina` zone created
through the dashboard on a live stack and **zone names withheld from the
prompt** (`zones_in_prompt:=false`, so nothing but retrieval can supply the
answer), the goal "ve donde se suele cocinar" produced:

```json
{"reasoning": "The goal is to go where cooking usually takes place. The kitchen
(la cocina) is identified as the place where meals are cooked and food is
prepared, which aligns with the goal.",
 "steps": [{"skill": "navigate", "params": {"x": 1.75, "y": -0.75}}]}
```

(1.75, -0.75) is the zone's center, and the reasoning quotes the functional
description back. The same goal with `rag_enabled:=false` returns
`explore(duration_sec=10)` — "since no known zones or coordinates are provided".
The retrieval is what carries it, and the planner acts on it.

- Functional goals work for named rooms: "donde se suele cocinar", "donde se ve
  la televisión", "donde me puedo duchar" resolve to zones and to the scenes
  observed inside them.
- The vocabulary is a fixed, documented list. A user who names a zone
  `laboratorio` gets no functional retrieval, by design — and the dashboard says
  so when the name is saved, instead of failing silently later.
- Zone documents are longer (~4x). At one document per user-named zone this is
  irrelevant to storage and to query latency.
- Scene descriptions stored inside a named zone now inherit that zone's meaning.
  A zone renamed after the fact does not retroactively relabel scenes already
  stored; re-exploring refreshes them (a re-observation updates the stored
  memory, ADR-025).
- `perceive`/`scan_360` results gained a `zone` field, so the final report can
  say *where* something was seen.
