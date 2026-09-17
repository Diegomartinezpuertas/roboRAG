# ADR-030: Dashboard as a live floor plan, tested in a real browser

**Date:** 2026-09-16
**Status:** Accepted

## Context

Four problems with the dashboard, all found by using it:

1. **"The robot doesn't move."** Reported during a manual mapping run. The
   command path was fine end to end (reproduced: 58 commands published, the
   robot advanced 0.31 m). What the page could not tell a person was *why* a
   robot might not move: the keyboard focus in another window (RViz, Gazebo), no
   node forwarding commands, or a slow simulation.
2. **"Sometimes the map is red and grey."** The map image was versioned by the
   grid's stamp — *simulation* time, which restarts at zero every launch. After
   a relaunch the browser asked "do I still have stamp X?", a previous run had
   had that stamp, the server answered 304, and the browser showed the old
   image: a previous run's map, and after the colour change below, its old
   red-and-grey rendering.
3. **Nothing tested the page.** The HTTP layer was covered (ADR-018); the
   JavaScript on top — where a key press becomes a request — was not.
4. **Layout.** The first screenshot of the page (the first time it was looked at
   by anything other than a person) showed the map using a fraction of its
   panel, memory cards cut off, and the controls small and scattered.

## Decision

**A live floor plan.** The SLAM map *is* the house's plan, so it is drawn as one:
chalk walls on blueprint blue (server-side `render_map_png` colours), a 1 m
grid, rooms hatched with their names, memories as cyan markers, the robot as an
amber arrow showing its heading (`/api/map` now returns `yaw`) with a trail. The
plan fills the left of the screen and carries its instruments *around* it —
rooms and legend above, drive controls and a title block below — and is fitted
into the space between them, so no panel ever covers part of the house. The
right column holds the order box, the agent's reasoning as a thread in time
(order, reasoning, steps, answer; ROS logs one tab away) and the memory. Icons
are inline SVG, text is Bahnschrift (ships with Windows), no external assets.

**The drive panel explains itself.** It shows who holds `/cmd_vel` (from the mux,
ADR-029), whether the keyboard reaches the page (`document.hasFocus()`), the
simulation's real-time factor (estimated from `/clock` by
`robot_dashboard/sim_clock.py`, pure and tested), and — after 2.5 s of keys held
with no motion — which of those is the likely reason.

**Map images are versioned per server process.** `/api/map` returns
`version = <process id>-<stamp>`; the ETag and the image URL use it. A relaunched
dashboard can no longer validate a previous run's image. Regression test
included.

**Memories can be deleted** from their card: `/rag/delete` (`DeleteMemory.srv`)
and `POST /api/memory/delete`. Only `semantic_map` accepts it — `knowledge_base`
and `task_history` are rebuilt from files, so a deletion there would either not
stick or not be undoable, and rag_node says so.

**Browser tests.** `tests/test_dashboard_ui.py` serves the real app over the
stub node and drives headless Chromium with Playwright: page loads without
script errors; holding W while driving sends `['w']` to `/api/teleop` and
letting go stops; keys do nothing until driving is on; typing an order full of
w/a/s/d never drives; the drive panel reports the driver and sim speed; the
thread renders order, reasoning, steps and answer; memory cards name their kind;
deleting a card deletes every id it stands for. They skip where Playwright's
Chromium is not installed; CI and the container install it.

## Rationale

**Why a floor plan and not a generic dark dashboard.** The first page was a set
of equal panels with uppercase labels — the look of any admin tool. The one
thing specific to this project is a robot building the plan of a house; drawing
the map as that plan makes it the obvious centre, and every other element can be
quiet.

**Why test in a browser rather than add JS unit tests.** The failure that
mattered crossed the page's event handling, focus, timers and HTTP together; a
browser test exercises that path as a person does, with no build step added.

**Why the version includes a process id rather than wall time.** A per-process
random id is unique across restarts without trusting the clock, and needs no
state.

## Consequences

*(2026-09-17, from a recording session: the thread stopped updating whenever the
node was relaunched with the page left open — it polls `/api/events?since=<id>`
and the server's ids restart at 1, so the cursor pointed past everything the
new buffer would ever hold. `EventBuffer.since()` now treats a cursor ahead of
itself as a fresh start. A **Limpiar** button was added beside the tabs, which
also empties the server's buffer through `POST /api/events/clear`, so a take
starts on an empty thread and a reload does not bring the old one back. The
plan's legend gained a **Recorrido** toggle for the same reason: it drops the
robot's trail and stops drawing it, so the floor plan on camera is the map and
nothing else. Both are covered by browser tests.)*

- The drive panel, title block and memory cards were verified against the live
  stack (headless Gazebo + Nav2): driver `teleop` while keys are held, sim factor
  ×0.53–0.68, merged memories showing their observation counts, no script errors.
- `requirements.txt` gains `playwright`; CI and the Dockerfile install Chromium
  with its system libraries. The image grows accordingly.
- `render_map_png` colours changed; its tests assert the named constants.
- `dashboard_node` subscribes to `/clock` and `/robot/cmd_vel_source`, and gains
  a `/rag/delete` client.
