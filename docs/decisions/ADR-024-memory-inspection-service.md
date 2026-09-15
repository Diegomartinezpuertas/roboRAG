# ADR-024: A read-only service for looking at the memory, not a second reader

**Date:** 2026-09-15
**Status:** Accepted

## Context

The RAG memory was the least visible part of the system and the one most worth
seeing. `docs/rag-pipeline.md §5` explained how to inspect it — a `ros2 service
call` to `/rag/query`, or a Python snippet against ChromaDB — which answers the
question for someone who already understands the design, and not at all for
someone watching a demo.

`/rag/query` cannot serve a viewer anyway. By design it returns document text
and scores only (ADR-012 works around exactly that), it always applies the
active map-session filter (ADR-019), and it requires a query — there is no way
to ask "what is in there?". The interesting facts about a memory are precisely
the ones it drops: which entry this is, what coordinates it carries, which map
session it belongs to, which file a knowledge chunk came from.

## Decision

A second, read-only service on `rag_node`: **`/rag/inspect`
(`InspectMemory.srv`)**. It browses a collection as stored, or searches it, and
returns entries with **ids, metadata and scores**, plus a document count for
every collection and the active map-session id.

- **Browsing embeds nothing.** With an empty `query_text` it goes through
  `ChromaManager.list_documents` — no Ollama round-trip. The panel opens
  instantly and keeps working when the embedding backend is down, which is
  exactly when someone wants to look inside.
- **Scoping is the caller's explicit choice.** `active_map_only` decides whether
  coordinate collections are filtered to the live map session. True shows what
  the planner would retrieve; false shows everything there is, and the UI greys
  out and labels the entries from dead maps. Seeing that a stale memory exists
  is half of understanding ADR-019.
- **The dashboard renders it structurally**, not as a wall of text: cards with
  title, similarity bar, coordinates, zone, provenance, and a marker drawn on
  the SLAM map at the pose each memory was learned at, with "go there" wired to
  the goal topic.
- Bounded by construction: at most 200 entries per request, and the HTTP layer
  clamps whatever the browser asks for.

## Rationale

**Why not let the dashboard open ChromaDB directly.** It is the same file on
disk and the code would be shorter. ChromaDB's `PersistentClient` is not built
for two processes writing the same directory, `rag_node` is the writer, and the
dashboard would need the embedder to search — dragging Ollama configuration,
the model name and the map-session logic into a second place. One owner of the
store, reached through a service, is the same reason `/rag/query` exists.

**Why a new service instead of extending `QueryRAG`.** `QueryRAG` is on the
planner's hot path and its contract is quoted throughout the docs and the
benchmark. Adding metadata, browse mode and a scope flag to it would widen the
interface the LLM path depends on in order to serve a UI. Two services, two
audiences, neither compromised.

**Why JSON inside a string field.** A `SemanticEntry[]` message would be more
idiomatic ROS. It would also fix a schema across three collections whose
metadata genuinely differs (a scene has a pose, a knowledge chunk has a source
file, a task has an id), and force a rebuild of `robot_interfaces` every time
one of them gains a field. `ExecuteSkill` already sets the precedent with
`params_json`/`result_json`: JSON where the shape is open, typed fields where it
is not.

**Why the viewer never fails the request.** `rag_node` legitimately is not up
when the dashboard starts, and it can be restarted underneath it (`respawn=True`
in the launch file). `/api/memory` answers 200 with `ok: false` and the reason,
and the panel shows that sentence. A 503 would be more correct HTTP and would
make a normal startup look like a broken dashboard.

**Alternatives rejected:**

- *A CLI script (`tools/dump_memory.py`).* Cheap, and invisible during a demo,
  which is the moment that matters.
- *Streaming the whole collection to the browser and filtering client-side.*
  `task_history` is already 59 entries and grows without bound; the filter
  belongs where the index is.

## Consequences

- The memory stops being a black box: what the robot remembers, where it learned
  it, how strongly it matches a question, and which map it belongs to are all
  visible in the same window as the map and the plan.
- Searching from the panel costs one embedding call (bge-m3, ~100 ms). Browsing
  costs none, so the panel's slow auto-refresh is free; it pauses while a search
  is typed so it cannot clobber the result on screen.
- `rag_node` serves two services from a single-threaded spin. An inspection
  request can now sit briefly in front of a planner query. At one browser panel
  with a 20 s refresh this is not contention worth an executor change; if the
  viewer ever gets heavier, the split is a callback group away.
- New dashboard endpoint `GET /api/memory`, new parameterless dependency of the
  dashboard on `rag_node` — degrading, by design, to a message in the panel.
