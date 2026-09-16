# ADR-014: Classical scene descriptor replaces the VLM; self-building memory

**Date:** 2026-07-17
**Status:** Accepted — extended by
[ADR-025](ADR-025-scene-memory-merging.md) (re-observations of a place merge into one memory)

## Context

Perception used Qwen2.5-VL to detect objects in camera frames. On this
simulator's software-rendered (llvmpipe) images the VLM was unreliable —
detections were sparse and inconsistent — which had a structural consequence:
the semantic memory's only *autonomous* population source barely worked, so
in practice `semantic_map` did not build itself during operation (landmarks
had to be seeded manually for the benchmark).

Separately, the project wants goals like *"go to the white room with many
objects"* to work: the robot should characterize places as it explores
(colors, how cluttered they are) and remember them at their coordinates.

A YOLO detector was considered as the VLM replacement. Rejected for now:
pretrained COCO weights also degrade on Gazebo's synthetic textures, and
`ultralytics` drags in PyTorch (~2 GB, plus VRAM contention with Ollama) — a
heavy price for a portfolio repo. It stays on the roadmap as an optional
comparison.

## Decision

**Remove the VLM entirely.** `perceive` now computes a **classical scene
descriptor** with no ML models (`robot_skills/scene_descriptor.py`, pure
numpy, unit-tested):

- **Dominant colors** from the camera: vectorized RGB→HSV, pixels classified
  into 10 named colors (white/gray/black + hue bins, dark-orange → brown),
  top-2 by share.
- **Clutter** from the LIDAR scan: obstacle-cluster count (consecutive
  near-beams split by range jumps), near fraction, median clearance →
  "cluttered" / "moderate" / "open".

The description text (e.g. *"predominantly brown, a cluttered space with
many objects (9 obstacle groups nearby)"*) is upserted into `semantic_map`
with the robot's coordinates. The object_id is the pose rounded to a 0.5 m
grid, so re-visiting a place **updates** its description instead of
accumulating near-duplicates.

**Self-building memory:** `explore` calls this store step at every frontier
it reaches (best-effort; failures never abort exploration). The memory now
grows autonomously while the robot explores — descriptive goals become
resolvable without any manual seeding.

Alongside this, the default embedder switched to **bge-m3** (measured:
Spanish top-1 retrieval 43%→86% vs nomic-embed-text, docs/rag-analysis.md
§2.4) — descriptive queries are exactly the cross-lingual-sensitive case —
and `rag_score_threshold` was recalibrated to 0.40 (bge-m3's correct-match
scores start at ~0.43; the threshold is a noise floor, disambiguation within
the top-k is the LLM's job).

## Rationale

- Pixel and range statistics are **exact in simulation** — the same renderer
  fidelity that breaks VLMs makes color histograms trivial.
- Zero new dependencies, zero VRAM, ~milliseconds per description.
- The descriptor's vocabulary (colors, clutter, openness) matches how people
  refer to rooms, which is what the RAG's fuzzy retrieval needs.

## Consequences

- Object-level detection ("find the fridge") is out of scope until a real
  detector lands (YOLO on the roadmap; VLM worth revisiting on real-camera
  hardware — this decision is simulator-specific, not anti-VLM).
- Room-level naming does not come from perception either, and
  [ADR-022](ADR-022-room-semantics.md) is where it does come from: the name a
  human gives a zone. A scene stored inside a named room inherits what that
  room is for, so "where does one usually cook" resolves without the robot
  ever recognising a kitchen.
- The `attribute_nav` benchmark category covers the new capability
  ("go to the white, open room" → direct nav to the remembered coordinates).
- qwen2.5vl:7b is no longer required; the "don't load both models" VRAM
  constraint disappears.
