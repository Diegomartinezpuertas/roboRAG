# ADR-033: Measure LLM → SQL against RAG on the same place memory

**Date:** 2026-09-17
**Status:** Accepted — an experiment; vector memory stays the default

## Context

The project's headline asks whether RAG helps, and the benchmark answered a
narrower question than it seemed to. "Without RAG" was the same planner with
the SQLite zone table, but with **no access at all** to what vector memory
holds: the stations, the scene descriptions the robot records, rooms by
purpose. The 15/15 vs 0/15 showed that the memory is used. It did not show that
similarity search is a better way to look that memory up than the obvious
alternative, an LLM writing SQL over a table of the same places. rag-analysis §1
names that alternative design B and §3 argued where it would win; nothing had
measured it.

## Decision

1. **Implement B as an experimental memory source**, not a replacement:
   `llm_planner_node` parameter `memory_source` (`vector` default, `sql`),
   re-read per goal so the benchmark can flip it live.
   - `eval/export_places_sql.py` copies the active session's `semantic_map` into
     SQLite `places(name, x, y, zone, description)` — one row per memory, after
     seeding, so both designs hold the same places.
   - Qwen writes one SELECT per goal (`PLACES_SQL_SYSTEM_PROMPT`). It runs through
     `robot_brain/sql_memory.py`: read-only connection, a single SELECT/WITH
     statement, a write/admin keyword filter, at most 5 rows. The rows reach the
     planner formatted exactly like memory documents, so the planner prompt and
     the plan check see the same shape either way. A query that fails yields no
     places, like a retrieval that finds nothing.
   - The query, its row count and any error are stored in the published plan
     (`memory_query`), so every miss can be traced.
2. **Change only the lookup.** The knowledge base is retrieved the same way in
   both conditions, and the zone table, planner prompt, plan check (ADR-032) and
   scoring are shared. `rag`, `sql` and `norag` run in one session on one memory.
   `run_benchmark.py` gains `--conditions` and `--out`, so the experiment writes to
   `eval/results/sql-experiment-2026-09-17/` and leaves the published results alone.
3. **Give B a fair implementation, designed off the suites.** A first prompt was
   tried on a separate set of places and goals ("Llévame a punto_7", "Ve a una
   zona verde y despejada", "Take me somewhere full of obstacles", "Ve donde se
   duerme"…). It failed in ways that belong to the prompt, not the design, and
   three things were fixed before any suite ran:
   - **The bare statement, not JSON.** SQL is full of quotes, and the model broke
     the escaping inside a JSON string.
   - **The stored vocabulary, documented.** The descriptor writes a fixed set of
     phrases ("an open, uncluttered space", ten colour names). Without them the
     model searched for "clear" and "bed" and found nothing. Documenting column
     values is ordinary practice for text-to-SQL.
   - **A warning about substrings.** `LIKE '%red%'` matches "uncluttered".
   The prompt contains no benchmark goal or landmark name (a test pins that),
   and it was not changed after the suites ran. **One leak, disclosed:** the
   translation example "despejado" → "uncluttered" came from a design goal ("zona
   verde y despejada"), but "despejada" is also in the suite's "la habitación
   blanca y despejada". It favours B on that task (6 runs). The documented
   vocabulary alone lists "uncluttered", so the model might have found it
   anyway; that was not tested.
4. **Score descriptive goals against every matching memory.** The attribute scorer
   used to accept any area in the vector top-5 whose text matches; a SQL query can
   legitimately reach a matching area outside it. For the vector conditions
   nothing changes: the planner only ever sees its top-3.
5. **Keep `vector` as the default.** See Consequences.

## Rationale

**Why not the planner's full prompt in the SQL call.** The SQL writer answers one
question — which rows does this goal need — the way the vector retrieval does.
Letting it plan as well would compare two different agents, not two lookups.

**Why a row cap of 5.** Vector retrieval hands the planner 3 memories. SQL gets a
little more, because a filter can legitimately match several places, but not
the whole table. Dumping every memory into the prompt works for a dozen places
and stops being a lookup at all at real sizes.

**Rejected — iterate on the prompt until the suites pass.** Relational goals are
where B lost, and a rule like "for 'nearest'/'farthest' return every candidate"
would probably fix them. Written after seeing those failures, it would be tuning
to the exam. It stays a follow-up for a measured, pre-registered change.

## Consequences

Measured 2026-09-17, one session, one memory — 8 places for the main and phrasing
suites, 12 for the hard suite:

| Suite | Vector memory (RAG) | LLM → SQL, same places | No memory |
|---|---|---|---|
| Full (21 per condition) | 21/21 | **21/21** | 6/21 |
| Phrasing (18) | 18/18 | **18/18** | 1/18 |
| Hard (30) | 27/30 | **24/30** | 6/30 |

| Hard suite, by type | RAG | SQL | No memory |
|---|---|---|---|
| Disambiguation | 9/9 | 9/9 | 0/9 |
| Ordered multi-step | 9/9 | 9/9 | 0/9 |
| Spatial relation | 3/6 | **0/6** | 0/6 |
| Plausible nonexistent place | 6/6 | 6/6 | 6/6 |

- **B matched RAG on every lookup.** Names, descriptions ("la habitación blanca y
  despejada" → `LIKE '%white%' AND LIKE '%uncluttered%'`, helped by the leaked
  example above), Spanish and English
  phrasings, confusable names (`LIKE '%estacion_c%' AND NOT LIKE '%sur%'`) and
  multi-step plans. All 69 queries were valid SQL and all of them filtered; none
  dumped the table.
- **B lost the spatial relations, 0/6 vs 3/6.** "La estación más cercana a la
  zona base" became a filter — `WHERE description LIKE '%base%'` — that returned
  nothing, so the planner had no stations to compare. A choice among candidates
  is not a lookup, and similarity search, which always returns its top-k,
  happens to hand the planner the candidates.
- **Nonexistent places: the SQL was right and the planner still invented.** The
  queries for "estacion_d" and "la estación central" correctly returned no rows.
  The planner then invented a point (×3) and a zone called "central" (×3), and
  the plan check (ADR-032) caught all six. In the RAG condition the check caught
  an invented zone for estacion_d (×3). Neither design makes the check
  unnecessary.
- **Latency:** median goal→plan 3.0 s with SQL, 2.4 s with RAG, 1.4 s with no
  memory. The SQL condition pays a full LLM call to write the query; the vector
  one pays an embedding call.
- **What this does and does not say.** At this memory size, and with a fixed,
  documented description vocabulary, RAG is *not* shown to be a better lookup
  than an LLM writing SQL over the same places. The one advantage measured
  (spatial relations) comes from similarity search always returning candidates,
  not from matching meaning, and those tasks are unreliable anyway (ADR-032).
  Where RAG *should* win was not tested: hundreds of self-built memories sharing
  one vocabulary, where `LIKE` returns an arbitrary five and similarity ranks
  them, and open-vocabulary descriptions no schema documentation can list.
- **Why the default stays `vector`.** B was not better anywhere. It was slower,
  lost the choice-among-candidates goals, and works only because its prompt
  spells out the descriptor's phrases and colour names. That is a coupling of the
  same kind as ADR-031's knowledge base: change the descriptor's wording and the
  SQL path silently stops matching. The parameter stays for the next experiment.
- **Session variance, again.** The RAG condition scored 27/30 on the hard suite
  here and 23/30 in ADR-032's run A, with the same code. Only the comparison
  inside this session is evidence.
- Follow-ups: B vs C at scale and with open-vocabulary descriptions; a
  pre-registered "return candidates for relative goals" rule for B.
- Tests: `tests/test_sql_memory.py` (row round-trip into the plan check's place
  format, filtering, row cap, rejection of writes, multiple statements and
  administrative commands, read-only connection, required columns, the substring
  trap, parsing the model's answer) and a prompt test that no benchmark wording
  is in the SQL writer's prompt.
