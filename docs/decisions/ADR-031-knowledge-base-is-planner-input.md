# ADR-031: The knowledge base is planner input — sync it, re-measure it, publish what it costs

**Date:** 2026-09-16
**Status:** Accepted — its open hazard (borrowed coordinates for a nonexistent
place) addressed by [ADR-032](ADR-032-plan-check-before-execution.md)

## Context

Every published planning number — `tasks_full`, `tasks_phrasing`, `tasks_hard`
— was measured in commit df9ac2a (2026-07-20 23:26). Ten minutes later, 35dc1a1
rewrote `data/knowledge/` to match the robot's real capabilities. Nothing
re-measured it. Two months of changes followed on top: map sessions (ADR-019),
offline seeding (ADR-020), zones indexed into memory (ADR-026), one memory
session per map frame (ADR-028).

All three suites were re-run on 2026-09-16, following
[EVALUATION.md](../EVALUATION.md) in a scratch workspace (fresh `chroma_db`,
fresh map, `ROBOT_WS` pointed at it, the user's own memory untouched).

**Run 1, knowledge files as committed.** Full suite **18/21** with RAG: the
impossible-goal control dropped from 3/3 to **0/3**. For "Ve al garaje", with
no garage anywhere in memory, the planner explored and then navigated to
`estacion_a`'s coordinates. Hard suite 19/30.

**Diagnosis.** A prompt replay held everything fixed except the knowledge
files: the same system prompt, the same `semantic_map` context retrieved live
from `/rag/query`, knowledge chunks retrieved from each variant, Qwen at
`temperature=0`, one plan per goal. The garage goal came out:

| Knowledge files | Plan for "Ve al garaje" |
|---|---|
| July (df9ac2a) | `explore` |
| As committed | `explore → navigate(x=-2.05, y=-1.57)` |
| As committed, minus one template | `explore` |

The template was one 35dc1a1 added:

> **Template: go to a remembered place by description** — if the retrieved
> context contains a place whose stored description matches, navigate(x, y)
> straight to it. **Do not explore.**

It contradicts the rule in `environment_rules.md` ("if a requested place has no
known coordinates, the robot must explore to find it rather than guessing a
position"), and when the two meet in the prompt the 7B planner follows the
worked example. Nobody saw it for two reasons. The change was never measured.
And `KnowledgeBase.ingest()` skipped any populated collection, so an existing
store never received the new files at all; only a wiped `data/chroma_db` did.

**Run 2, template removed**, same procedure from scratch:

| Suite | With RAG | Without RAG | July (with RAG) |
|---|---|---|---|
| Full (42 runs) | **21/21** | 6/21 | 21/21 |
| Phrasing (36 runs) | **18/18** | 0/18 | 18/18 |
| Hard (60 runs) | **20/30** | 3/30 | 27/30 |

The garage came back. The hard suite did not:

- **Plausible nonexistent place: 0/6** (July 6/6). "Ve a estacion_d" explores,
  then navigates to `estacion_a`. "Ve a la estación central" goes straight to
  `estacion_a`, reasoning that it "has known coordinates". Without RAG: 3/6.
  With no memory there are no coordinates to borrow, so it explores for "estación
  central" and invents (-2, 5) for `estacion_d`.
- **Spatial relation: 2/6** (July 3/6). "Nearest to base" 0/3, "farthest" 2/3.
  Every miss picks `estacion_a`.

In the same replay, the July files declined `estacion_d` but sent "estación
central" to a zone that does not exist. The regression is therefore not one
chunk that can be pointed at, and it is not resolved here.

## Decision

1. **Remove the contradicting template, and only it.** The rule it overrode
   stands.
2. **`KnowledgeBase.ingest()` syncs with the files.** On startup the chunks are
   compared with the stored ones by id and text. If they match, nothing is
   embedded. If anything differs, the collection is replaced. It holds nothing
   but chunks of committed Markdown, so no runtime state is lost. The new
   chunks are embedded *before* the old ones are deleted, so an Ollama failure
   leaves the previous knowledge base in place.
3. **An edit to `data/knowledge/` is a prompt change.** It is followed by a
   re-run of the planning suites before any number is quoted again
   (EVALUATION.md, CLAUDE.md). Superseded results are archived, not
   overwritten silently. Run 1 lives in `eval/results/before-kb-fix-2026-09-16/`.
4. **Publish run 2 as measured, and withdraw the claim it contradicts.** The
   July analysis read `negative_plausible` 6/6 vs 3/6 as "RAG *suppresses*
   hallucination". On the current system the opposite holds for this task
   type, and the README, rag-analysis §2.6 and the promo material now say so.
5. **Do not tune the knowledge base to the benchmark.** The failing goals were
   used to *diagnose*. No benchmark goal text or landmark name is written into
   `data/knowledge/`, and the final files were measured once, not re-run in
   search of a better result.

## Rationale

**Rejected — soften the template and add a negative example.** Tried in the
replay as one change. The template became "no need to explore first; this
applies only when the description actually matches". A new example was added:
"Ve al sótano", a goal no suite contains, with the plan explore, never another
place's coordinates. The result was worse on every count: the garage still
borrowed `estacion_a`'s coordinates, `estacion_d` still got an invented zone,
and the known-zone control broke, with "Ve a la zona base" becoming `explore`.
Few-shot examples do not stay in their lane in a 7B planner; each one moves
plans far from the goal pattern it was written for. Iterating on wording until
the suite passes would be tuning to the exam.

**Not done — restore July's "find a specific object: … if not found, explore"
template.** It is the one example 35dc1a1 removed that shows the not-found path,
which makes it the obvious suspect for `negative_plausible`. The replay shows
it does not account for both goals. Reinstating it because it might lift this
exact score would be tuning to the exam. It stays a candidate for a measured
experiment, not a fix.

**Rejected — wipe `data/chroma_db` whenever the knowledge changes.** That is
what the old behaviour required, and it deletes every place the robot has
remembered along with the stale chunks.

**Rejected — store a content hash in metadata.** Equivalent, but it adds state
to keep consistent. The knowledge base is 13 chunks, and comparing the text
directly is cheap and cannot drift.

## Consequences

- **The published numbers are lower, and they are true.** Hard suite 20/30 vs
  3/30. The gap it documents moves from "spatial reasoning" alone to spatial
  reasoning *plus* borrowing coordinates for places that do not exist.
- **An operational hazard, now documented.** "Ve a estacion_d" drives the robot
  to `estacion_a`. The planner's JSON is well-formed and the step is a valid
  navigate, so nothing downstream rejects it. The fix belongs in plan
  validation, where rag-analysis §4 already points: a `navigate(x, y)` whose
  coordinates belong to a retrieved memory not named by the goal is suspect.
  Alternatively, the agent loop could let the planner check its target before
  moving. Both are roadmap items and neither is claimed here.
- **`temperature=0` does not make runs identical.** Even repetitions inside one
  run differ now and then: "farthest from base" went 2 of 3 in run 2, and
  `estacion_a` without RAG planned `explore` twice and something else once.
  Between run 1 and run 2 "farthest" moved 0/3 → 2/3, and nothing separates
  the template removal from that variation. A difference of one or two runs
  per task type is noise at this sample size, and the analysis now says so.
- **The first start after this change re-embeds the knowledge base** once,
  because the stored chunks differ from the files. Every later start embeds
  nothing unless `data/knowledge/` changed.
- Tests: `tests/test_knowledge_base_sync.py` covers unchanged (no embedding),
  removed and edited chunks, an embedder failure (old store kept), and a
  missing or empty directory (store untouched).
