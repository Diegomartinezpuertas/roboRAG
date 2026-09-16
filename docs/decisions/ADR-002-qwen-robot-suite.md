# ADR-002: Qwen Robot Suite integration status

**Date:** 2026-07-14
**Status:** Accepted — facts corrected 2026-09-16 (see the update below); its
perception half superseded by [ADR-014](ADR-014-classical-scene-descriptor.md)

## Context

Alibaba announced the Qwen-RobotNav / Qwen-RobotManip / Qwen-RobotWorld
family: robotics-specialized models that would accept camera images + an
instruction and return waypoints directly, replacing the current combination
of Qwen2.5-VL + Nav2.

## Decision

Do not integrate Qwen-RobotNav/Manip/World yet: their weights are not
publicly released. Use what is available today:

| Model | Public weights | Integrable now | Current alternative |
|--------|---------------|------------------|--------------------|
| Qwen-RobotNav-4B | Not released | No | Qwen2.5-VL-7B + Nav2 |
| Qwen-RobotManip | Not released | No | N/A (no manipulation) |
| Qwen-RobotWorld | Not released | No | N/A |
| Qwen2.5-VL-7B | Available | Yes | — |
| Qwen2.5-7B | Available | Yes | — |

## Rationale

- `perceive_skill.py` uses Qwen2.5-VL-7B via Ollama to describe the scene and
  `nav_skill.py` uses Nav2 SimpleCommander for motion — a combination that
  works today without depending on unpublished weights.

## Consequences

- When Qwen-RobotNav-4B weights are published (watch
  https://github.com/QwenLM/Qwen-RobotNav), replace `perceive_skill.py` +
  `nav_skill.py` with direct model calls (image + instruction → waypoints,
  ~200 ms per inference on Jetson-class hardware per the published numbers).
- Until then the perceive → update_map → navigate pipeline remains two
  separate steps instead of one.

## Update 2026-09-16 — what the published repositories actually say

Re-checked against the QwenLM GitHub organisation before publication. Three
statements above did not hold up, and are corrected here rather than rewritten
out of the record:

- **No weight release is planned.** `QwenLM/Qwen-RobotNav` and
  `QwenLM/Qwen-RobotManip` (both created 2026-06-29) state: *"There is currently
  no plan to release the model weights for Qwen-RobotManip or Qwen-RobotNav."*
  "Revisit when weights are released" is therefore not a trigger that is
  expected to fire.
- **There is no "Qwen-RobotWorld".** No repository of that name exists in the
  organisation. The closest name, `Qwen-AgentWorld`, is a *language* world model
  for software agents (MCP, terminal, web, OS), not a robotics model.
- **The "~200 ms on Jetson-class hardware" figure has no source** in the
  published Qwen-RobotNav material and is withdrawn.

What the repositories do publish: Qwen-RobotNav is built on Qwen3-VL with a
small action head that outputs 8 waypoints `(x, y, θ)`, reported at 4B and 8B,
covering instruction following, object search and tracking. That is a report,
not something this project can run.

The rationale's premise has also moved on: `perceive_skill.py` no longer uses
Qwen2.5-VL. The VLM was removed as unreliable on software-rendered frames, and
perception is a classical colour + LIDAR descriptor
([ADR-014](ADR-014-classical-scene-descriptor.md)). The decision itself stands:
nothing here depends on unpublished weights.

