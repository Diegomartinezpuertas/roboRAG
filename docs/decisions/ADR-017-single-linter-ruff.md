# ADR-017: One linter (ruff), and the boilerplate ament lint tests removed

**Date:** 2026-07-20
**Status:** Accepted

## Context

Each Python package carried the three test files that `ros2 pkg create`
generates — `test_copyright.py`, `test_flake8.py`, `test_pep257.py` — untouched
across all six packages (verified identical by checksum). Meanwhile the project
had separately adopted `ruff` as its linter, configured in `ruff.toml` and
enforced in CI.

The two disagreed, so `colcon test` failed in **all five** Python packages —
10 failures — and had been failing for the whole life of the project. The
conflicts were not incidental; they contradicted documented house style:

- **`I100` (import order):** ament's `flake8-import-order` expects a different
  grouping from the project's documented convention (stdlib → third-party →
  ROS 2 → local, `CLAUDE.md`), which `ruff` enforces.
- **`D401` (imperative mood):** the codebase consistently writes docstring
  summaries in the third person ("Returns the robot's pose…", "Appends an
  event…"), per the Google style mandated in `CLAUDE.md`. `pep257`'s D401 wants
  "Return the…". Satisfying it meant rewriting roughly sixty docstrings to
  match a convention the project had not chosen.

The README additionally claimed "ROS nodes and wiring are exercised locally
with `colcon test`". That was false twice over: the command was red, and the
tests in question were stock linters that exercise no node and no wiring.

## Decision

1. Delete the three boilerplate lint tests from all six packages, and drop the
   `ament_copyright` / `ament_flake8` / `ament_pep257` `<test_depend>` entries
   from their `package.xml`. `python3-pytest` stays.
2. `ruff` is the single linter for Python, run by CI and documented in
   `ruff.toml`.
3. `robot_interfaces` (an `ament_cmake` package with no Python) keeps
   `ament_lint_auto`: its `xmllint` and `lint_cmake` checks validate the
   package manifest and CMake, which `ruff` cannot. Doing so surfaced a real
   defect — `<member_of_group>` was placed before the `<depend>` and
   `<test_depend>` blocks, which the package format 3 schema rejects. Fixed by
   moving it last, immediately before `<export>`.
4. The README's testing claims were rewritten to state plainly what is and is
   not covered.

## Rationale

Two linters enforcing contradictory conventions cannot both be satisfied; one
of them has to lose. `ruff` wins because it is the one the project actually
configured, the one CI runs, the one the code already conforms to, and it
subsumes flake8's and pep257's rule sets while being far faster.

**Alternative rejected: fix the code to satisfy both.** Achievable, but it
means reformatting sixty-odd docstrings into a mood the project deliberately
did not adopt, and permanently constraining import order to two masters. Cost
paid forever, benefit zero — the errors were style disagreements, not defects.

**Alternative rejected: keep the tests and mark them skipped.** Dead files that
must be explained to every reader. If a check is not going to run, delete it.

**Why keep the linters on `robot_interfaces`:** they check a different artefact
class (manifest XML, CMake) that `ruff` does not cover, and they immediately
earned their keep by catching the schema violation above.

## Consequences

- `colcon test` passes across all seven packages, so a red result is now a real
  signal rather than expected noise.
- `robot_interfaces/package.xml` validates against the official format 3 schema.
- Python lint coverage is unchanged in substance — `ruff check .` covers what
  flake8/pep257 covered, minus the two contested rules.
- No copyright-header check runs. Acceptable: the repository is MIT-licensed at
  the root and per-file headers were never adopted.
- The honest consequence, now stated in the README: **the ROS nodes have no
  automated test coverage.** The 52-test suite covers pure logic only. Node
  behaviour is verified by hand against a running stack. A `launch_testing`
  suite is the obvious next step and is on the roadmap.
