# When does RAG actually help? An evidence-based analysis

This document answers the project's central question with measurements rather
than opinions: **when does a RAG memory improve a robot's natural-language
navigation, when is a plain SQL lookup better, and when would an LLM that
translates language into SQL queries be the right design?**

All numbers come from the reproducible harness in [`eval/`](../eval)
(see [docs/EVALUATION.md](EVALUATION.md) to re-run everything). LLM =
Qwen2.5-7B at `temperature=0`; embeddings = nomic-embed-text unless stated.

---

## 1. The design space

Four architectures can connect "go to the kitchen" to coordinates:

| Design | NL understanding | Recall mechanism | Exactness | Open vocabulary | Infra cost |
|---|---|---|---|---|---|
| **A. Pure SQL lookup** | none (exact string match) | `SELECT ... WHERE name = ?` | perfect | none | trivial |
| **B. LLM → SQL** (text-to-query) | LLM | LLM writes/fills the query | perfect *if* the LLM names the right key | only what the LLM can normalize to a key | LLM only |
| **C. LLM + RAG** | LLM | embedding similarity over stored text | approximate (top-k + threshold) | yes — fuzzy, description-based | LLM + embedder + vector store |
| **D. Hybrid (this project)** | LLM | SQL for named zones **and** RAG for remembered objects | per-store | per-store | both |

A key observation the data will keep confirming: **natural-language
understanding always comes from the LLM, never from RAG.** Designs B, C and D
all let you *talk* to the robot. What RAG uniquely adds is **fuzzy semantic
recall** — retrieving a memory by meaning ("the white fridge", "where you saw
the mailbox") instead of by exact key. If everything you need to recall has a
stable exact name, RAG is overhead.

---

## 2. The evidence

### 2.1 Ablation: RAG on vs off (30 runs)

![Benchmark results](../eval/results/benchmark.png)

| Task type | With RAG | Without RAG |
|---|---|---|
| Object-referenced nav (RAG-dependent) | **9/9 (100%)** | **0/9 (0%)** |
| Known-zone nav (control) | 3/3 (100%) | 3/3 (100%) |
| Impossible goal (hallucination check) | 3/3 (100%) | 3/3 (100%) |

Three findings, one per row:

1. **RAG is decisive exactly where the information lives only in semantic
   memory.** With RAG, the planner retrieves
   `"estacion_a at (x=-1.79, y=-0.44) ..."` and emits `navigate(x, y)`
   directly; without it, it has no source for the coordinates and falls back
   to blind exploration. The LLM's own reasoning strings state the mechanism:
   *"The target has explicit coordinates, so we can navigate directly"* vs
   *"no coordinates are provided, we need to explore"*.
2. **The known-zone control proves the ablation isolates RAG, not language
   understanding.** Zone names travel to the planner through SQLite
   (design A embedded inside D), and both conditions score 100%. This is the
   honest core of the analysis: *for a small closed vocabulary of named
   places, SQL alone is enough* — removing RAG costs nothing there.
3. **RAG neither causes nor prevents hallucination.** For a nonexistent
   place, both conditions correctly refuse to invent coordinates (the prompt
   rules carry that behavior).

### 2.2 Robustness to phrasing and language (36 runs)

![Phrasing robustness](../eval/results/phrasing.png)

Same three landmarks, three phrasings each (Spanish imperative, Spanish
paraphrase, English), two repetitions:

| Phrasing | With RAG | Without RAG |
|---|---|---|
| Spanish imperative ("Ve a estacion_a") | 6/6 | 0/6 |
| Spanish paraphrase ("Llévame hasta donde está...") | 6/6 | 0/6 |
| English ("Take me to...") | 5/6 | 0/6 |

This is the practical payoff of the embedding-based recall: **retrieval
survived paraphrase and code-switching** — wordings never seen at indexing
time still ranked the right memory first (landmark names anchor the match;
see §2.4 for how language affects *descriptive* queries).

The single failure is instructive: for *"Take me to estacion_c"* the LLM
retrieved the right context but emitted `navigate(zone="estacion_c")` —
misclassifying the remembered object as a named zone. Execution would fail
(no such zone in SQLite), and the scorer correctly counts it as a failure.
Lesson: **even with perfect retrieval, the LLM's interpretation layer is a
real error source** — plan validation against the actual stores (design D's
exactness) is what catches it.

### 2.3 What does RAG cost? Planning latency

Mean goal→plan latency is **1.4 s in both conditions** (right panel of the
benchmark figure; one 4.2 s warm-up outlier on the first call). Retrieval —
three collection queries through `/rag/query`, each an embedding call plus a
vector search — is **noise compared to the 7B model's inference time**. On
this hardware, RAG's latency cost is effectively zero. (Its real costs are
operational: an embedder, a vector store, and index-freshness to manage.)

### 2.4 Embedding choice: the multilingual gap is real and large

![Embedding comparison](../eval/results/embeddings.png)

Measured on the project's own corpus (English documents, as the robot stores
them), 7 queries per language, top-1 accuracy and the margin between the
correct document's score and the best wrong one:

| Model | Query lang | Top-1 | Mean correct score | Mean margin |
|---|---|---|---|---|
| nomic-embed-text | Spanish | **43%** | 0.505 | **−0.045** |
| nomic-embed-text | English | 100% | 0.735 | +0.141 |
| bge-m3 | Spanish | **86%** | 0.591 | **+0.080** |
| bge-m3 | English | 100% | 0.639 | +0.126 |

- With **nomic-embed-text**, Spanish queries against English memories are a
  coin flip (43%), and the *negative* mean margin means the wrong document
  outranks the right one on average. This is why the relevance threshold
  (`rag_score_threshold=0.45`, ADR-011) was needed: correct matches measured
  0.57–0.62 while wrong-topic matches reached 0.40 — the threshold cuts the
  noise, at the price of sometimes retrieving nothing for Spanish.
- **bge-m3 doubles Spanish top-1 (43%→86%) and flips the margin positive**,
  while remaining perfect in English. Its English margins are slightly
  smaller than nomic's (+0.126 vs +0.141) — the one trade-off.

**Recommendation:** for any deployment where users speak a different language
than the stored documents, switch the embedder to `bge-m3` (one parameter:
`embedding_model` in `agent_params.yaml`, plus re-ingesting the collections).
For English-only corpora and queries, nomic-embed-text remains marginally
sharper and smaller.

---

## 3. Interpretation: choosing a design

**Use pure SQL (A) when** the recall vocabulary is small, closed, and
exactly named — like this project's user-defined zones. The control row shows
zero benefit from RAG there. No embedder, no index, perfect precision.

**Use LLM → SQL (B) when** the data is *structured* and the questions are
*exact or aggregative*: "how many chairs did you log yesterday?", "which zone
did you visit last?". Text-to-query gives perfect answers over keys and
numbers — things embedding similarity is bad at. Its weakness is fuzzy
reference: the LLM must normalize "esa cosa blanca de la cocina" to a key it
has never seen, with no similarity signal to help. (Worth noting: B puts the
LLM *inside* the retrieval path, so recall quality depends on model
temperature/version; C's retrieval is deterministic given the index.)

**Use LLM + RAG (C) when** memories are open-vocabulary and described rather
than named: objects the camera saw ("a black mailbox on a pole"), places
characterized by free text. §2.2 shows the recall surviving phrasings no
lookup could match. This is also the design that scales with memory size:
at 3 landmarks a human could hand-check, at 10,000 perception memories
similarity search is the only recall that still works.

**The hybrid (D, this project) is not indecision — it is putting each store
where it wins.** Exact things (zones) resolve through SQL and are immune to
retrieval noise; fuzzy things (perceived objects) resolve through RAG; the
LLM supplies language understanding over both and the plan validator catches
the LLM's own type confusions (§2.2). The measured failure modes map cleanly:
every error we observed came either from cross-lingual embedding weakness
(→ fixed by bge-m3) or from LLM interpretation (→ caught by exact-store
validation), never from the hybrid structure itself.

### Threats to validity (read before quoting the numbers)

- Measured at the **planning level** (ADR-013), not physical execution; the
  claim is about *decisions*, which is the mechanism RAG can influence.
- Small task suite and corpus (66 benchmark runs, 28 embedding queries) on
  one LLM; `temperature=0` means results are exact for this setup but not a
  sample from a distribution.
- Landmark tasks use the landmark's *name* in the goal, which favors
  retrieval; the embedding study (§2.4) covers the harder descriptive-query
  case where the multilingual gap appears.

---

## 4. Actionable conclusions

1. **Keep the hybrid.** SQL for named zones, RAG for perceived/described
   memories, LLM as the language layer. Each is measurably doing the job the
   others can't.
2. **Switch to bge-m3 for multilingual use** — the single highest-leverage
   change the data supports (43%→86% Spanish top-1).
3. **Keep the relevance threshold** while any cross-lingual traffic exists;
   re-calibrate it after an embedder change (score distributions shift:
   bge-m3's correct-match scores center lower, ~0.59 vs 0.74).
4. **Validate plans against the exact stores** — the LLM will occasionally
   emit the wrong reference type even with perfect context.
5. For future structured queries over task history ("what did you do
   yesterday?"), add a **text-to-SQL path (B)** rather than stretching RAG
   into aggregation questions it cannot answer.
