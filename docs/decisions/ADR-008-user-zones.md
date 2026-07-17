# ADR-008: User-defined zones (interactive map)

**Date:** 2026-07-14
**Status:** Superseded by [ADR-011](ADR-011-rag-quality-zones-sqlite.md)
(the JSON store described below moved to SQLite; the rest of this document
remains valid as context for the original decision).

## Context

The robot needs a spatial vocabulary shared with the user ("go to the
kitchen", "explore zone A"). Hardcoded coordinates do not correspond to the
actually mapped world and are not user-editable.

## Decision

Zones are created by drag-selecting a rectangle on the dashboard's SLAM map
and naming it. Dual persistence:

1. **Zones store** — geometric source of truth
   (`{name: {x_min, y_min, x_max, y_max}}`). Written by `dashboard_node`
   (`POST /api/zones`); read on every use by `skills_executor_node`
   (resolution of `navigate(zone=...)` and bounds for `explore(zone=...)`)
   and `llm_planner_node` (KNOWN ZONES list injected into the prompt).
2. **ChromaDB `semantic_map`** — each zone is additionally indexed as a
   `SemanticObject` (`object_id=zone-<name>`) via `/rag/update_map`, so
   natural-language RAG queries ("where is the kitchen?") retrieve it as
   context.

`explore(zone=...)` restricts frontier search to the bounding box and, if the
robot is outside, navigates to the zone center first.

## Rationale

- A plain store for geometry: trivial reads from any node without coupling
  them to the dashboard, hand-editable, and no dependence on embeddings for
  an exact lookup.
- ChromaDB only as the semantic layer (fuzzy retrieval by the LLM), never as
  the source of coordinates.

## Consequences

- Deleting a zone removes it from the store but not from ChromaDB (the
  indexed entry becomes orphaned; harmless because navigate validates against
  the store). Cleanup pending if it ever becomes annoying.
