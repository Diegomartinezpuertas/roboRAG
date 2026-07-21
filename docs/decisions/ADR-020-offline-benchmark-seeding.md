# ADR-020: Offline benchmark seeding (reproduce without a simulator)

**Date:** 2026-07-21
**Status:** Accepted

## Context

The planning benchmark ([ADR-013](ADR-013-planning-level-benchmark.md)) needs
the landmarks (`estacion_a/b/c`) present in the `semantic_map` collection, and
[ADR-019](ADR-019-map-session-memory-versioning.md) scopes every coordinate
memory to the *active map session* — retrieval drops anything not tagged for it.

`seed_memory.py` derived each landmark's pose from the **live SLAM occupancy
grid** (nearest obstacle-free cell), so seeding — and therefore reproducing
*any* benchmark number — required bringing up Gazebo + Nav2 + SLAM at least
once. Because `data/chroma_db/` is git-ignored (a binary, embedding-specific
store, [ADR-014](ADR-014-classical-scene-descriptor.md)), a **fresh clone has no
memory at all** and must seed from scratch. The net effect: "clone the repo and
reproduce the headline result" was gated on a heavyweight, WSL2-flaky simulator
bring-up, even though the measurement itself (`dry_run` planning) never drives
the robot.

A second, related trap: a `data/chroma_db` seeded *before* ADR-019 holds
untagged memories that the current `rag_node` hides, so the benchmark silently
reports 0/6 with RAG until re-seeded.

## Decision

Add `seed_memory.py --offline`: seed the three landmarks (plus the optional
`--hard` distractors, the two scene descriptors, and the control zone) at
**fixed, well-separated coordinates**, through the same `/rag/update_map`
service path as online seeding — so `rag_node` still stamps each memory with the
active map session (ADR-019). No SLAM map, no simulator.

Documented as **Option C** in [EVALUATION.md](../EVALUATION.md): a downloader
reproduces the `full` / `phrasing` / `hard` suites with only Ollama +
`agent.launch.py`.

Also harden `run_benchmark.py`'s `set_param`: it now reads the parameter back
and retries, warning on failure, so a missed `rag_enabled` flip fails loud
instead of silently running a condition under the wrong ablation.

## Rationale

The benchmark scores the planner's *decision* (ADR-013), for which the landmarks
"only need to be distinct, plausible free-space coordinates the planner can be
told about via RAG" — wording already in `seed_memory.py`. The live map only
ever supplied obstacle-free cells; fixed coordinates satisfy the same
requirement without the sim.

Alternatives rejected:
- **Commit `data/chroma_db`.** Binary, tied to one embedding model (dims differ
  per embedder, ADR-014), and it rots the moment the schema or embedder changes.
- **Commit a saved SLAM map + a map loader.** Heavier, and still needs a running
  stack to seed the vector store from it.
- **Ship a pre-baked chroma per embedder.** Same rot problem, multiplied.

Fixed coordinates are canonical and map-independent, so they cannot drift out of
sync the way map-derived poses tied to a specific SLAM run can.

## Consequences

- A fresh clone reproduces every planning suite with no Gazebo — the "works on
  my WSL2" barrier drops for the part reviewers actually check.
- `--offline` doubles as the fix for a stale/untagged local memory: re-seeding
  re-registers the landmarks under the current session.
- Physical driving (SR/SPL) still needs the full sim — unchanged, and still
  out of scope for the benchmark by ADR-013.
- Online seeding is unchanged and remains the default; offline is a strict
  addition behind a flag.
