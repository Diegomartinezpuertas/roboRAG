"""Plan scoring for the benchmark suites — pure logic, no ROS imports.

Split out of `run_benchmark.py` so the scoring rules can be unit-tested in the
no-ROS suite (`tests/test_benchmark_scoring.py`). That matters more for the
hard suite than the original one: `tasks_hard.yaml` introduces ordered
multi-step matching and derived relational ground truth, which are easy to get
subtly wrong and would otherwise only be exercised by a full benchmark run
against a live simulator and LLM.

`classify(plan, task, landmarks, zones, retrieved_docs) -> (decision, success)`
is the entry point; `decision` is a short label recorded per run so a failure
says *how* the planner was wrong, not merely that it was.
"""

import math
import re

# A navigate step counts as reaching a landmark within this radius, in metres.
NAV_TOLERANCE_M = 0.75


def _navigate_steps(plan):
    return [s for s in plan.get('steps', []) if s.get('skill') == 'navigate']


def _navigates_to_coord(plan, target_xy):
    for step in _navigate_steps(plan):
        p = step.get('params', {})
        if 'x' in p and 'y' in p:
            try:
                if math.dist((float(p['x']), float(p['y'])), target_xy) <= NAV_TOLERANCE_M:
                    return True
            except (TypeError, ValueError):
                continue
    return False


_COORD_IN_DOC = re.compile(r'at \(x=(-?\d+(?:\.\d+)?), y=(-?\d+(?:\.\d+)?)\)')


def attribute_candidates(task, landmarks, retrieved_docs):
    """Valid target coordinates for an attribute task.

    With a self-building memory, several remembered areas can legitimately
    match a description ("where there were many objects"), so the seeded
    landmark is not the only right answer: any retrieved area whose text
    contains one of the task's match terms counts (the planner chose among
    exactly these retrieved documents).
    """
    target = landmarks[task['target']]
    candidates = [(target['x'], target['y'])]
    terms = [t.lower() for t in task.get('match_any', [])]
    for doc in retrieved_docs:
        low = doc.lower()
        if terms and not any(t in low for t in terms):
            continue
        m = _COORD_IN_DOC.search(doc)
        if m:
            candidates.append((float(m.group(1)), float(m.group(2))))
    return candidates


def _skill_matches(step, expected):
    """True if a plan step uses one of the expected skill names.

    Args:
        step: A plan step dict.
        expected: A skill name, or a list of acceptable alternatives (e.g.
            scan_360 or perceive both satisfy "look around").
    """
    names = [expected] if isinstance(expected, str) else list(expected)
    return step.get('skill') in names


def _step_matches(step, spec, landmarks):
    """True if a plan step satisfies one expected-step spec of a multi-step task."""
    if not _skill_matches(step, spec['skill']):
        return False
    if 'target' not in spec:
        return True
    target = landmarks[spec['target']]
    params = step.get('params', {})
    if 'x' not in params or 'y' not in params:
        return False
    try:
        return math.dist(
            (float(params['x']), float(params['y'])), (target['x'], target['y']),
        ) <= NAV_TOLERANCE_M
    except (TypeError, ValueError):
        return False


def _contains_ordered(steps, specs, landmarks):
    """True if every spec is matched, in order, by the plan's steps.

    Extra steps between the expected ones are tolerated — the planner may add
    a perceive of its own. Dropping an expected step, reordering them, or
    collapsing two into one is not.
    """
    remaining = list(specs)
    for step in steps:
        if remaining and _step_matches(step, remaining[0], landmarks):
            remaining.pop(0)
    return not remaining


def _relational_target(task, landmarks, zones):
    """Resolves a spatial relation to the landmark name it actually designates.

    Ground truth is computed here from the seeded coordinates rather than
    written into the YAML, so the expected answer cannot drift out of sync with
    the map the landmarks were seeded on.

    Returns:
        The winning landmark name, or None if the reference zone is unknown.
    """
    reference = zones.get(task['reference_zone'])
    if reference is None:
        return None
    candidates = [c for c in task['candidates'] if c in landmarks]
    if not candidates:
        return None
    choose = max if task['relation'] == 'farthest' else min
    return choose(
        candidates,
        key=lambda name: math.dist(
            (landmarks[name]['x'], landmarks[name]['y']), reference,
        ),
    )


def classify(plan, task, landmarks, zones, retrieved_docs=()):
    """Returns (decision, success) for a plan given the task type."""
    if not plan:
        return 'no_plan', False
    ttype = task['type']
    steps = plan.get('steps', [])

    if ttype == 'object_nav':
        target = landmarks[task['target']]
        if _navigates_to_coord(plan, (target['x'], target['y'])):
            return 'direct_nav', True
        if any(s.get('skill') == 'explore' for s in steps):
            return 'explore', False
        return 'other', False

    if ttype == 'attribute_nav':
        for candidate in attribute_candidates(task, landmarks, retrieved_docs):
            if _navigates_to_coord(plan, candidate):
                return 'direct_nav', True
        if any(s.get('skill') == 'explore' for s in steps):
            return 'explore', False
        return 'other', False

    if ttype == 'zone_nav':
        zone = task['zone']
        for step in _navigate_steps(plan):
            if step.get('params', {}).get('zone') == zone:
                return 'zone_nav', True
        if zone in zones and _navigates_to_coord(plan, zones[zone]):
            return 'zone_nav', True
        if any(s.get('skill') == 'explore' for s in steps):
            return 'explore', False
        return 'other', False

    if ttype == 'negative':
        # Success = did not invent coordinates for a nonexistent place.
        invented = any('x' in s.get('params', {}) for s in _navigate_steps(plan))
        return ('hallucinated', False) if invented else ('declined_or_explore', True)

    # --- hard suite (tasks_hard.yaml) ------------------------------------

    if ttype == 'distractor_nav':
        target = landmarks[task['target']]
        distractor = landmarks[task['distractor']]
        hit_target = _navigates_to_coord(plan, (target['x'], target['y']))
        hit_distractor = _navigates_to_coord(plan, (distractor['x'], distractor['y']))
        if hit_target and hit_distractor:
            # Visiting both is not disambiguation, it is hedging.
            return 'visited_both', False
        if hit_target:
            return 'direct_nav', True
        if hit_distractor:
            return 'wrong_landmark', False
        if any(s.get('skill') == 'explore' for s in steps):
            return 'explore', False
        return 'other', False

    if ttype == 'ordered_multi_step':
        if _contains_ordered(steps, task['expect'], landmarks):
            return 'ordered_plan', True
        # Distinguish "had the right pieces, wrong order" from "missing pieces":
        # the two point at different weaknesses in the planner.
        matched = sum(
            any(_step_matches(step, spec, landmarks) for step in steps)
            for spec in task['expect']
        )
        if matched == len(task['expect']):
            return 'out_of_order', False
        return f'incomplete_{matched}_of_{len(task["expect"])}', False

    if ttype == 'relational_nav':
        expected = _relational_target(task, landmarks, zones)
        if expected is None:
            return 'unscorable_missing_reference', False
        winner = landmarks[expected]
        if not _navigates_to_coord(plan, (winner['x'], winner['y'])):
            return 'wrong_landmark', False
        # Navigating to the right one AND a wrong one is not a correct answer.
        others = [
            name for name in task['candidates']
            if name != expected and name in landmarks
        ]
        if any(
            _navigates_to_coord(plan, (landmarks[n]['x'], landmarks[n]['y']))
            for n in others
        ):
            return 'visited_multiple', False
        return 'direct_nav', True

    if ttype == 'negative_plausible':
        invented = any('x' in s.get('params', {}) for s in _navigate_steps(plan))
        if invented:
            return 'hallucinated', False
        # A named zone the robot does not have is equally an invention.
        named = [
            s.get('params', {}).get('zone') for s in _navigate_steps(plan)
            if 'zone' in s.get('params', {})
        ]
        if any(zone not in zones for zone in named):
            return 'invented_zone', False
        return 'declined_or_explore', True

    return 'other', False
