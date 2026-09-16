# ADR-012: Navigable RAG, post-execution report, and ablation switches

**Date:** 2026-07-16
**Status:** Accepted — extended by
[ADR-032](ADR-032-plan-check-before-execution.md) (retrieved coordinates are
checked before the robot is sent to them)

## Context

The project's hypothesis is that semantic memory (RAG) improves
natural-language navigation. While preparing the quantitative evaluation we
found that, as built, **RAG could not improve navigation even when it
worked**:

1. `semantic_map` documents were indexed as `"{label} in {zone}: {desc}"` —
   **without coordinates**. And `QueryRAG` returns only document text (no
   metadata), so the planner never received a remembered object's pose and
   could not navigate to it.
2. The `report` step's message was written by the LLM **at planning time**,
   before anything executed ("found: [objects]"), so "tell me what's there"
   tasks never reflected reality.
3. There was no way to disable RAG for an A/B experiment.
4. The LLM ran at default temperature → not reproducible.

## Decision

**Coordinates in the document text (Fase 0.1).** `SemanticMap.upsert_object`
now indexes `"{label} at (x=.., y=..) in {zone}: {desc}"`. It is the only
channel through which the planner can learn coordinates (QueryRAG exposes no
metadata). The system prompt instructs: if the context provides coordinates,
`navigate(x, y)` directly; otherwise explore. Verified: with context the plan
is `navigate(x, y)`; without it, `explore`.

**Truthful knowledge base (Fase 0.2).** `environment_rules.md` contained
**fabricated** room coordinates (scaffolding) that do not match the
`turtlebot3_house` world (a single mesh, no extractable furniture poses).
Rewritten without invented coordinates: real coordinates live in
`semantic_map`/zones, measured by driving the robot.

**Post-execution report (Fase 0.3).** The planner no longer emits `report`
steps (they are stripped if present). After executing
navigate/explore/perceive, a **second LLM call** receives the real results
and writes the user-facing response, in the goal's language (keyword-based
language detection, because qwen2.5:7b ignores a soft "same language"
instruction when the rest of the prompt is English).

**Reproducibility (Fase 0.4).** `QwenClient` accepts `temperature` (default
0.0) and `seed`. Verified: two identical calls produce identical output.

**Ablation switches (Fase 0.5).** `llm_planner_node` has `rag_enabled`
(default true) and `zones_in_prompt` (default true), re-read per goal so the
benchmark can flip conditions live.

**LangChain removal.** Its only use was the `@tool` decorator for a
name→callable registry the LLM never tool-called (plan JSON is parsed
manually). Removed (four heavy dependencies gone); skills are dispatched
directly in `toolkit.py` (formerly `langchain_agent.py`). A real LangChain
agent loop remains future work.

## Consequences

- The A/B evaluation is now possible and meaningful (RAG can genuinely change
  the navigation plan).
- Object ground truth for the benchmark must be measured manually (the world
  is a mesh without furniture poses).
- Changing `temperature`/`seed`/`rag_enabled` changes benchmark results: pin
  them in each condition's configuration.
- `toolkit.py` no longer depends on langchain; if an agent loop is adopted
  later, it will be reintroduced deliberately, not vestigially.
