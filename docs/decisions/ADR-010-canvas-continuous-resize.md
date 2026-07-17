# ADR-010: Resize the map canvas every frame

**Date:** 2026-07-14
**Status:** Accepted

## Context

Area selection on the dashboard map landed on wrong world coordinates.
`resizeCanvas()` ran only once at page load and on the `window.resize` event,
but `canvas.width`/`canvas.height` (the internal pixel-buffer size used by
all the `w2c`/`c2w` math) desynced from the on-screen box whenever the layout
shifted for other reasons: zone chips appearing, the timeline growing, the
map image loading — none of which fire `window.resize`, which only reacts to
browser-window size changes.

## Decision

`resizeCanvas()` is called on every `draw()` frame (via
`requestAnimationFrame`), comparing `canvas.width/height` against the current
`getBoundingClientRect()` and updating only when they differ (avoids
needlessly clearing the canvas).

## Consequences

- Near-zero cost fix (two number comparisons per frame).
- Any future canvas-based interactive element in the dashboard must follow
  the same pattern rather than trusting `window.resize`.
