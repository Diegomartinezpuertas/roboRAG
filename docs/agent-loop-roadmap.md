# Roadmap: from plan-then-execute to a real agent loop

**Status:** design, not yet built. This is the plan for the project's headline
next step — the one the hard benchmark suite was built to measure.

---

## Why

Today the cognitive core is **open-loop**: `llm_planner_node` retrieves context,
asks Qwen for a full plan, dispatches the steps in order, and reports. Once the
plan is emitted, execution feedback changes nothing — a step that fails, a
`navigate` that lands somewhere unexpected, a `perceive` that reveals the goal
was wrong: none of it re-enters the planner. The plan is a prediction made
before the robot moved.

A **closed-loop agent** re-plans from what actually happened: execute a step,
observe the result, decide the next step in light of it. That is the difference
between "follow these directions" and "figure it out as you go".

The [hard benchmark suite](rag-analysis.md#26-a-harder-suite-because-the-main-one-is-saturated)
already localises where this would pay off. With RAG, the current system scores:

| Capability | Score | Closed loop should help? |
|---|---|---|
| Disambiguation (confusable memories) | 9/9 | No — already solved at plan time |
| Ordered multi-step plan | 9/9 | No — already solved |
| Plausible hallucination refusal | 6/6 | No |
| **Spatial reasoning** ("nearest to base") | **3/6** | **Yes — the target** |

The 3/6 is the number to move. The planner retrieves the right candidates and
then mis-computes which is nearest; a loop that could *check* its choice against
the retrieved coordinates — or navigate, observe, and correct — is exactly the
mechanism that closes that gap. **Take the baseline before building this**
(it exists: `results/hard/`), so the improvement is measured, not asserted.

---

## Design

### The loop

```
goal ──▶ retrieve context ──▶ propose ONE next action (LLM)
              ▲                          │
              │                          ▼
        observation ◀── execute ──▶ result (success / failure / data)
              │                          │
              └──────── still working? ──┤
                                         ▼
                                    done ──▶ report
```

Instead of "emit N steps, run them", the planner emits **one action at a time**,
each conditioned on the accumulated results so far, and decides after each
whether the goal is met. This is the ReAct pattern (reason → act → observe),
scoped to this robot's real skills.

### What each turn sees

The prompt for turn *k* carries:

- the original goal;
- the retrieved RAG context (re-retrieved when the situation changes — e.g.
  after exploring, new places exist in `semantic_map`);
- **the history of actions taken and their real results** (this is the new
  part — currently thrown away);
- the current robot pose and known zones.

And returns exactly one of: a next action (`{skill, params}`), or `done` with a
final answer.

### Termination — the part that actually needs care

Open-loop can't loop forever; closed-loop can. Guards:

- **Hard step budget** (`max_agent_steps`, default ~8) — a backstop, not the
  primary stop.
- **No-progress detection** — if two consecutive actions produce the same
  observation (e.g. `navigate` to the same failed pose, `explore` that adds no
  frontier), stop and report the impasse rather than retry identically. The
  toolkit already returns structured results that make "same observation"
  detectable.
- **Explicit `done`** — the model's own signal, trusted but bounded by the
  budget above.

### Where it lives

`llm_planner_node._on_goal` is the seam. Today it is: retrieve → `generate_plan`
→ `execute_plan` → report. It becomes: retrieve → **agent loop** → report, where
the loop calls `generate_next_action` and `execute_plan` (one step) alternately.

Keep it as a **pure-logic core** (ADR-018): an `agent_step(goal, history,
context) -> action | done` function with no `rclpy` in it, unit-tested against a
fake toolkit exactly like `execute_plan` is. The node wires it to the real
services. This is what makes the loop testable without a simulator.

### Prompts

A second system prompt (the "next action" prompt) alongside the existing
planner prompt. It states the ReAct contract, the same skill catalogue, and —
critically — that the model must **reason over the results block**, not re-derive
from the goal alone. The spatial-reasoning failures suggest an explicit
"if the goal is relational, list the candidate coordinates and compare them
before choosing" instruction is worth testing.

---

## Build order

1. **`agent_step` pure-logic core + prompt**, with unit tests over fake
   histories (no ROS). Covers: proposes a sensible first action; consumes a
   result to propose the next; emits `done` when the goal is met; stops on
   no-progress.
2. **Wire it into `llm_planner_node`** behind a parameter
   (`agent_loop_enabled`, default false), so the open-loop path stays the
   measured baseline and the loop is A/B-comparable live — the same discipline
   the RAG ablation already uses.
3. **Re-run the hard suite** in both modes. The headline result is the
   spatial-reasoning delta (3/6 → ?). Add an `agent` condition to the scorer
   if the loop changes what a "plan" looks like (it emits a trace, not a
   single plan — `scoring.py` may need to score the final navigate of a trace).
4. **New chart**: open-loop vs agent-loop on the hard suite, same style as the
   existing ablation charts.
5. **ADR** recording the loop design, the termination guards, and the measured
   delta.

## Explicit non-goals (for this step)

- Not a multi-agent system, not tool-calling via an external framework
  (LangChain was removed for good reason — ADR-012). The dispatch stays direct.
- Not learned planning. Same Qwen2.5-7B, `temperature=0`, so the comparison is
  clean: only the loop changes.
- Not physical SR/SPL. Still planning-level (ADR-013); the point is the decision
  quality, measured reproducibly.

## Risks

- **Latency.** N sequential LLM calls instead of one. Acceptable if it buys
  correctness on the hard tasks; measure it (the harness already records
  goal→plan latency, extend to goal→done).
- **Looping / oscillation.** The no-progress guard is load-bearing; test it
  hardest.
- **Benchmark scoring.** A trace is not a single plan. Decide early whether the
  scorer grades the final action, the whole trace, or both — and keep the
  open-loop numbers comparable.
