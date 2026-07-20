# ADR-015: Workspace-relative data paths via `ROBOT_WS`

**Date:** 2026-07-20
**Status:** Accepted

## Context

Every filesystem path in the project was an absolute string pinned to one
developer's home directory — `/home/diego/robot_ws/data/...` — in three places
at once:

- the `declare_parameter` defaults of four nodes (`rag_node`,
  `skills_executor_node`, `llm_planner_node`, `dashboard_node`),
- `robot_bringup/config/agent_params.yaml`, which re-stated the same paths,
- `eval/run_benchmark.py` and `eval/seed_memory.py`, plus `setup_env.sh`.

Fifteen occurrences in total. This directly violated the project's own rule
("No hardcoded paths — ROS 2 parameters or env vars", `CLAUDE.md`) and made the
repository unusable by anyone who cloned it: a fresh clone would silently write
its ChromaDB and zone store to a path that does not exist on the new machine,
or fail outright. For a portfolio repository whose entire purpose is being read
and run by other people, this was the single largest blocker to publication.

The duplication was also a correctness hazard in its own right: the node
defaults and the YAML could drift, and which one won depended on whether the
node was started by `ros2 launch` (YAML) or `ros2 run` (node default).

## Decision

A single environment variable, `ROBOT_WS`, is the one source of truth for the
workspace root.

1. `setup_env.sh` resolves **its own directory** (`BASH_SOURCE`) and exports it
   as `ROBOT_WS`. Sourcing the script from any clone location is enough; there
   is nothing to edit.
2. Each node computes its path defaults from that variable at import time,
   with `~/robot_ws` as the fallback when it is unset:
   ```python
   WS_ROOT = Path(os.environ.get('ROBOT_WS', Path.home() / 'robot_ws'))
   ...
   self.declare_parameter('zones_db', str(WS_ROOT / 'data' / 'zones.db'))
   ```
3. Path keys were **removed entirely** from `agent_params.yaml`, which now
   carries tuning parameters only. The file's header says so explicitly.
4. The `eval/` scripts use `ROBOT_WS` with `Path(__file__).parent.parent` as the
   fallback — the workspace root by repository layout.

Paths remain ordinary ROS 2 parameters, so any single one is still overridable:

```bash
ros2 run robot_dashboard dashboard_node --ros-args -p zones_db:=/tmp/zones.db
```

## Rationale

**Why an environment variable rather than the YAML?** ROS 2's parameter YAML
loader does no variable substitution, so a YAML-only solution cannot express
"relative to wherever this is checked out" — it can only hold another absolute
string. Keeping paths in the YAML would have meant every user editing a config
file before first run.

**Why not derive the root from `ament_index` / the install prefix?** The data
directory lives in the *source* workspace (`$ROBOT_WS/data`), not in the
install space, and is deliberately not an installed package resource: ChromaDB
and the zone store are mutable runtime state that must survive a
`rm -rf build install`. The install prefix is the wrong anchor for it.

**Why remove the keys from the YAML instead of updating them?** Two defaults
for the same value is a drift hazard, and the launch-vs-run divergence above
was already latent. One definition site, in code, next to the parameter
declaration.

**Alternative rejected: a `robot_paths` shared package.** It would centralise
the three-line helper, but every node would gain a build dependency on it —
including `robot_rag`, which today has no reason to know about the other
packages. The duplication is one commented constant per node; the coupling
would have been permanent. Cheaper to repeat than to couple.

## Consequences

- The repository is clone-location independent; `git clone && source
  setup_env.sh && colcon build` works for any user in any directory.
- `ROBOT_WS` is now load-bearing rather than informational. A shell that
  runs nodes without sourcing `setup_env.sh` gets the `~/robot_ws` fallback,
  which is correct for the original layout but silent if wrong — the paths are
  logged by ChromaDB and the zone store on startup, so a misconfiguration is
  visible in `/rosout`.
- `agent_params.yaml` no longer has a `skills_executor_node` stanza at all: both
  of its parameters were paths, and an empty `ros__parameters:` block is
  rejected by the parameter loader.
- Verified by running `dashboard_node` with `ROBOT_WS` pointed at a scratch
  directory and confirming it created its `zones.db` there.
