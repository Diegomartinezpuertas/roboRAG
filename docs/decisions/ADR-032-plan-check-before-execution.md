# ADR-032: Check every navigate step before the robot moves, and give the planner the zones' coordinates

**Date:** 2026-09-16
**Status:** Accepted

## Context

The re-run of [ADR-031](ADR-031-knowledge-base-is-planner-input.md) left one
failure open and documented as an operational hazard. Asked for a *plausible*
place that does not exist, the planner with RAG went to a real one. "Ve a
estacion_d" and "Ve a la estación central" both navigated to `estacion_a`'s
coordinates, 6 runs out of 6. The plan was valid JSON with a valid skill, so
nothing downstream could object. Two other variants of the same failure had
also been measured. Run 1 of ADR-031 invented a zone, `navigate(zone="estacion_d")`.
Without RAG, the planner invented a point, `navigate(x=-2, y=5)`. The prompt
already says "Never invent coordinates or zone names", and the planner's own
reasoning often acknowledged the place was missing before navigating anyway
("estacion_d, which is not listed in known zones or context coordinates.
Therefore, we should explore first to find it" → explore, then `estacion_a`).
A rule in the prompt was not going to hold.

Designing the check turned up a second, independent flaw, in the benchmark
rather than the robot. `KNOWN ZONES` gave the planner zone **names** only. For
"Ve a la estación más cercana a la zona base" it saw the stations'
coordinates but had no position for `base` at all. The spatial-relation tasks
could only be guessed. rag-analysis §2.6 read their 2/6 as "retrieves the right
candidates and skips the distance arithmetic", a reading the prompt did not
support.

Two smaller defects were fixed on the way:

- In offline hard seeding, the relational ground truth considered only
  stations a, b and c, although the memory holds five. The live layout gives the
  same answer either way.
- `environment_rules.md` stated a 30-minute operating limit and a
  retry-by-exploring fallback that nothing implements. It was the knowledge base
  telling the planner about behaviour the robot does not have, the failure class
  of rag-pipeline §6.

## Decision

1. **`robot_brain/plan_validation.py` checks every `navigate` step** between
   parsing the plan and publishing or executing it. The check is pure logic,
   tested in layer 1, with the one language judgement injected as a callable:
   1. `navigate(zone=Z)`: Z must be a zone. Otherwise the step is replaced —
      including when Z is the name of a remembered place (see run B).
   2. `navigate(x, y)`: the point must be a known place, meaning a retrieved
      memory or a zone centre within 0.25 m. Otherwise it is replaced.
   3. A known place must not be **borrowed**. A separate, narrow Qwen call
      (`PLACE_RESOLVER_SYSTEM_PROMPT`) returns the listed places the goal asks
      for and the places it asks for that are missing. The step is replaced
      only when something is missing and the point is none of the places asked
      for. A "missing" name that matches a known label is discarded.
   A replaced step becomes `explore`, which is what the prompt already demands
   for an unknown place. The correction is published as a status, carried in
   `/robot/plan` (`raw_steps`, `plan_corrections`) and passed to the final
   report, so the user hears why the robot explored.
2. **The check intervenes only when something is missing.** When the resolver
   merely names a different known place than the planner — a spatial relation,
   a confusable name — the planner's choice stands and the disagreement is
   logged. It fails open: a resolver error keeps the plan.
3. **`plan_validation` is a parameter** (default true), re-read per goal like
   `rag_enabled`, and the benchmark gains a third condition, `rag_unchecked`,
   so the check's effect is measured on the same memory as the ablation.
4. **Zones reach the prompt with their centres**: `base (centre x=…, y=…)`.
5. **The relational ground truth covers every seeded station**, and the two
   unimplemented statements leave `environment_rules.md`.
6. **Designed on goals the suites do not contain.** The resolver prompt and the
   rules were shaped in a replay over a separate set of goals, including
   "Llévame a la estación de carga", "Go to estacion_b_norte", "Ve a la
   estación que está más al este", "Take me somewhere with lots of clutter" and
   "Which station is nearest to the base? Go there.". They were measured on the
   suites afterwards, twice: **run A** is this design; **run B** added two rules
   that were then reverted (Rationale).

## Rationale

**Why a check after planning, not a better prompt.** The prompt already forbids
inventing coordinates and zone names, and ADR-031 showed what adding worked
examples does to a 7B planner. A check outside the model is testable, keeps the
planner's prompt unchanged, and can be switched off to measure what it adds.

**Why an LLM call for the third check.** The exact checks cannot tell a
borrowed place from a described one. "Llévame a donde había muchos objetos"
legitimately goes to an `area` memory whose text shares no word with the goal;
it is written in English, the goal in Spanish. And "la estación central" is a
name, not a description. Lexical overlap would reject the first and accept the
second. A narrow question — which of these places does the goal ask for, and
what does it ask for that is not here — is a different and easier task than
planning.

**Rejected after measuring — repair a remembered place named as a zone.**
Run A found the planner writing `navigate(zone="estacion_a")` for a remembered
place, the wrong-reference-type error of rag-analysis §2.2. Two rules were added
and measured in run B: repair such a step to the memory's coordinates, and skip
the resolver when the goal names the destination outright. The repair made the
check less safe. "Ve a la estación central" was planned as
`navigate(zone="estacion_c")`, repaired to estacion_c's coordinates, and the
resolver then accepted estacion_c as "the central station". Without the repair
the same plan is an unknown zone and becomes `explore`, as in run A. A step
rejected into exploration is a safe failure; a step repaired into a confident
wrong destination is not. Run B also produced no case the repair fixed. Both rules
were reverted, so the code that ships is the code run A measured. Run B stays
archived in `eval/results/zone-repair-experiment-2026-09-17/`.

**Rejected — let the resolver override the planner** (retarget to the place the
resolver picked). In spatial relations and confusable names that trades one
model's error for another's, with no measurement to say which is better. The
check stays a veto on missing places, not a second planner.

**Rejected — replan with the correction as feedback.** That is a loop: extra
calls, a termination problem, and the agent-loop roadmap's territory. Replacing
the step with `explore` is what the rules already ask for, and it is safe.

## Consequences

Measured following EVALUATION.md in a clean scratch workspace, three conditions
per suite on the same map and memory. **Run A** is the shipped code:

| Suite | RAG + check | RAG, check off | No RAG + check |
|---|---|---|---|
| Full (63 runs) | **21/21** | 21/21 | 6/21 |
| Phrasing (54 runs) | **18/18** | 18/18 | 2/18 |
| Hard (90 runs) | **23/30** | 19/30 | 6/30 |

| Hard suite, by type | RAG + check | RAG, check off | No RAG + check |
|---|---|---|---|
| Disambiguation | 9/9 | 9/9 | 0/9 |
| Ordered multi-step | 7/9 | 7/9 | 0/9 |
| Spatial relation | 1/6 | 3/6 | 0/6 |
| Plausible nonexistent place | **6/6** | 0/6 | 6/6 |

- **What the check did, run by run** (the two RAG conditions are paired by task
  and repetition):
  - It caught 6 destinations: `navigate(zone="estacion_d")` ×3 (no such zone),
    and "la estación central" → `estacion_a`'s coordinates ×3 (resolver:
    missing).
  - It rejected 2 correct plans: "la estación más lejana de la zona base" went
    to `estacion_a`, which *was* the farthest, and the resolver listed the whole
    phrase as a missing place.
  - It made 5 more corrections on plans that failed either way: remembered
    places written as zones.
  - With RAG it changed nothing in the main or phrasing suites: no correction,
    no success lost.
- **Latency:** median goal→plan 2.4 s with the check vs 2.0 s without on the
  main suite (the extra Qwen call), 2.4 vs 1.9 s on phrasing, equal (2.6 s) on
  the hard suite.
- **Without RAG** the check replaces invented destinations: 6 corrections on
  the main suite and 7 on the hard suite, where plausible nonexistent places go
  from 3/6 (ADR-031 run 2) to 6/6.
- **Zone centres make the relational tasks answerable, not solved.** In run A
  the planner chose the right station in all 6 relational plans with the check
  off (3 of them written as a zone). In run B it chose the wrong one in all 6,
  with every station and the base's centre in the prompt. Spatial reasoning
  over retrieved coordinates remains unreliable for this 7B planner.
- **Run-to-run variation is as large as the effects.** With the check off, the
  code was identical in runs A and B, yet the hard suite moved 19 → 21/30,
  spatial relation 3/6 → 0/6, and "Ve a estacion_d" was planned as an invented
  zone in A and as exploration in B. The check's effect is established by the
  paired runs within a session, not by comparing totals across sessions.
- **A fixture coincidence, disclosed.** The `base` zone is seeded on top of
  `estacion_b`. With zone centres in the prompt, the no-RAG planner copied
  base's centre for "Navigate to estacion_b" and landed on the station (the
  phrasing suite's 2/18). The resolver accepted base as estacion_b, so the check
  did not catch it. Moving the control zone changes the relational ground
  truth; it is left for a suite revision with a new baseline.
- **Run B** (repair and named-destination shortcut, reverted): full 21/21 ·
  21/21 · 6/21, phrasing 18/18 · 18/18 · 0/18, hard 21/30 · 21/30 · 6/30, with
  "la estación central" repaired into a miss 3 times.
- **The check is itself a model call.** It narrows the unsafe case the
  measurement found; it does not guarantee a plan. It fails open when Ollama
  errors, and its resolver can both miss (base accepted as estacion_b) and
  over-reject (a relational phrase listed as missing). The agent loop, which
  could look before committing, stays the roadmap's next step.
- Tests: `tests/test_plan_validation.py` (exact checks, the borrowed-place rule,
  missing names that are known labels, fail-open, no model call when not
  needed, the run B regression) and prompt tests for zone centres, the resolver
  prompt and report corrections.
