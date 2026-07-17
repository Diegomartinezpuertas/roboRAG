# ADR-002: Qwen Robot Suite integration status

**Date:** 2026-07-14
**Status:** Accepted (revisit when weights are released)

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
