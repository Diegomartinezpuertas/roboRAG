"""Tests for the benchmark plan scorer (eval/scoring.py).

The scorer decides what the headline numbers in the README mean, so a silent
bug here would misreport the project's central claim. It is also the only part
of the hard suite that can be verified without a live simulator and LLM: these
tests pin down exactly what counts as success for each task type, including the
cases designed to be failable (visiting both a landmark and its distractor,
producing the right steps in the wrong order, hedging across candidates).
"""

import pytest

from scoring import NAV_TOLERANCE_M, classify

LANDMARKS = {
    'estacion_a': {'x': 0.0, 'y': 0.0},
    'estacion_a_norte': {'x': 4.0, 'y': 0.0},
    'estacion_b': {'x': 8.0, 'y': 0.0},
    'estacion_c': {'x': 12.0, 'y': 0.0},
    'estacion_c_sur': {'x': 12.0, 'y': 4.0},
}
# The base zone sits on estacion_b. Distances from it along x: estacion_b 0 m,
# estacion_c 4 m, estacion_a 8 m — so nearest = estacion_b, farthest = estacion_a.
# Note this is NOT the outermost landmark on the map: the relation is resolved
# against the reference zone, which is exactly the reasoning being tested.
ZONES = {'base': (8.0, 0.0)}


def nav(name=None, x=None, y=None, zone=None):
    """Builds a navigate step, by landmark name, raw coordinates, or zone."""
    if name is not None:
        x, y = LANDMARKS[name]['x'], LANDMARKS[name]['y']
    params = {'zone': zone} if zone is not None else {'x': x, 'y': y}
    return {'skill': 'navigate', 'params': params}


def plan(*steps):
    return {'reasoning': 'test', 'steps': list(steps)}


EXPLORE = {'skill': 'explore', 'params': {'duration_sec': 30}}
SCAN = {'skill': 'scan_360', 'params': {}}
PERCEIVE = {'skill': 'perceive', 'params': {}}


# --- shared behaviour ------------------------------------------------------

def test_a_missing_plan_is_never_a_success():
    task = {'type': 'object_nav', 'target': 'estacion_a'}
    assert classify({}, task, LANDMARKS, ZONES) == ('no_plan', False)


def test_coordinates_within_tolerance_count_as_the_landmark():
    task = {'type': 'object_nav', 'target': 'estacion_a'}
    near = plan(nav(x=NAV_TOLERANCE_M - 0.05, y=0.0))
    assert classify(near, task, LANDMARKS, ZONES) == ('direct_nav', True)


def test_coordinates_outside_tolerance_do_not():
    task = {'type': 'object_nav', 'target': 'estacion_a'}
    far = plan(nav(x=NAV_TOLERANCE_M + 0.5, y=0.0))
    assert classify(far, task, LANDMARKS, ZONES)[1] is False


# --- distractor_nav --------------------------------------------------------

DISTRACTOR_TASK = {
    'type': 'distractor_nav',
    'target': 'estacion_a',
    'distractor': 'estacion_a_norte',
}


def test_distractor_navigating_only_to_the_target_succeeds():
    result = classify(plan(nav('estacion_a')), DISTRACTOR_TASK, LANDMARKS, ZONES)
    assert result == ('direct_nav', True)


def test_distractor_navigating_to_the_wrong_landmark_fails():
    result = classify(plan(nav('estacion_a_norte')), DISTRACTOR_TASK, LANDMARKS, ZONES)
    assert result == ('wrong_landmark', False)


def test_distractor_visiting_both_is_hedging_not_disambiguation():
    """The failure mode the task exists to catch: covering both bets."""
    hedged = plan(nav('estacion_a'), nav('estacion_a_norte'))
    assert classify(hedged, DISTRACTOR_TASK, LANDMARKS, ZONES) == ('visited_both', False)


def test_distractor_exploring_instead_fails_and_is_labelled_as_such():
    assert classify(plan(EXPLORE), DISTRACTOR_TASK, LANDMARKS, ZONES) == ('explore', False)


# --- ordered_multi_step ----------------------------------------------------

MULTI_TASK = {
    'type': 'ordered_multi_step',
    'expect': [
        {'skill': 'navigate', 'target': 'estacion_a'},
        {'skill': ['scan_360', 'perceive']},
        {'skill': 'navigate', 'target': 'estacion_c'},
    ],
}


def test_multi_step_exact_sequence_succeeds():
    good = plan(nav('estacion_a'), SCAN, nav('estacion_c'))
    assert classify(good, MULTI_TASK, LANDMARKS, ZONES) == ('ordered_plan', True)


def test_multi_step_accepts_either_alternative_skill():
    """"Look around" is satisfied by scan_360 or perceive."""
    good = plan(nav('estacion_a'), PERCEIVE, nav('estacion_c'))
    assert classify(good, MULTI_TASK, LANDMARKS, ZONES)[1] is True


def test_multi_step_tolerates_extra_steps_in_between():
    """The planner may add work of its own; that is not an error."""
    good = plan(nav('estacion_a'), SCAN, EXPLORE, PERCEIVE, nav('estacion_c'))
    assert classify(good, MULTI_TASK, LANDMARKS, ZONES)[1] is True


def test_multi_step_wrong_order_is_reported_distinctly():
    """Right pieces, wrong order — a different weakness from omission."""
    reordered = plan(nav('estacion_c'), SCAN, nav('estacion_a'))
    assert classify(reordered, MULTI_TASK, LANDMARKS, ZONES) == ('out_of_order', False)


def test_multi_step_dropping_a_step_reports_how_many_were_found():
    incomplete = plan(nav('estacion_a'), nav('estacion_c'))
    decision, success = classify(incomplete, MULTI_TASK, LANDMARKS, ZONES)
    assert success is False
    assert decision == 'incomplete_2_of_3'


def test_multi_step_navigating_to_the_wrong_place_is_incomplete():
    wrong = plan(nav('estacion_b'), SCAN, nav('estacion_c'))
    decision, success = classify(wrong, MULTI_TASK, LANDMARKS, ZONES)
    assert success is False
    assert decision == 'incomplete_2_of_3'


# --- relational_nav --------------------------------------------------------

NEAREST_TASK = {
    'type': 'relational_nav', 'relation': 'nearest', 'reference_zone': 'base',
    'candidates': ['estacion_a', 'estacion_b', 'estacion_c'],
}
FARTHEST_TASK = {**NEAREST_TASK, 'relation': 'farthest'}


def test_relational_nearest_is_derived_from_the_seeded_coordinates():
    """Ground truth is computed, never written in the YAML — so it cannot drift."""
    assert classify(plan(nav('estacion_b')), NEAREST_TASK, LANDMARKS, ZONES) == (
        'direct_nav', True
    )


def test_relational_farthest_resolves_against_the_reference_not_the_map():
    """estacion_a is farthest FROM BASE, though estacion_c is the map's far end."""
    assert classify(plan(nav('estacion_a')), FARTHEST_TASK, LANDMARKS, ZONES) == (
        'direct_nav', True
    )
    assert classify(plan(nav('estacion_c')), FARTHEST_TASK, LANDMARKS, ZONES) == (
        'wrong_landmark', False
    )


def test_relational_picking_the_wrong_candidate_fails():
    assert classify(plan(nav('estacion_a')), NEAREST_TASK, LANDMARKS, ZONES) == (
        'wrong_landmark', False
    )


def test_relational_visiting_several_candidates_is_not_an_answer():
    hedged = plan(nav('estacion_b'), nav('estacion_c'))
    assert classify(hedged, NEAREST_TASK, LANDMARKS, ZONES) == ('visited_multiple', False)


def test_relational_is_unscorable_without_the_reference_zone():
    """Better to flag the run than to silently score against a missing zone."""
    decision, success = classify(plan(nav('estacion_b')), NEAREST_TASK, LANDMARKS, {})
    assert success is False
    assert decision == 'unscorable_missing_reference'


# --- zone_nav (the control) ------------------------------------------------

ZONE_TASK = {'type': 'zone_nav', 'zone': 'base'}
ZONES_WITH_BASE = {'base': (8.0, 0.0)}


def test_zone_nav_navigate_by_name_succeeds():
    plan_ = plan(nav(zone='base'))
    assert classify(plan_, ZONE_TASK, LANDMARKS, ZONES_WITH_BASE) == ('zone_nav', True)


def test_zone_nav_explore_by_name_also_succeeds():
    """explore(zone) reads the zone from SQLite and goes there — resolution, not failure.

    Observed without RAG: qwen2.5:7b plans explore(zone="base") rather than
    navigate(zone="base"). It still resolved the zone by name (the point of the
    control); direct-nav vs explore-within is a strategy choice.
    """
    plan_ = plan({'skill': 'explore', 'params': {'zone': 'base', 'duration_sec': 10}})
    assert classify(plan_, ZONE_TASK, LANDMARKS, ZONES_WITH_BASE) == ('zone_nav', True)


def test_zone_nav_navigate_to_zone_centre_coords_succeeds():
    plan_ = plan(nav(x=8.0, y=0.0))
    assert classify(plan_, ZONE_TASK, LANDMARKS, ZONES_WITH_BASE) == ('zone_nav', True)


def test_zone_nav_blind_explore_without_the_zone_name_fails():
    """A target-less explore is a genuine failure to resolve — the object_nav case."""
    plan_ = plan({'skill': 'explore', 'params': {'duration_sec': 30}})
    assert classify(plan_, ZONE_TASK, LANDMARKS, ZONES_WITH_BASE) == ('blind_explore', False)


def test_zone_nav_navigate_to_the_wrong_zone_name_does_not_count():
    plan_ = plan({'skill': 'explore', 'params': {'zone': 'kitchen'}})
    decision, success = classify(plan_, ZONE_TASK, LANDMARKS, ZONES_WITH_BASE)
    assert success is False


# --- negative / negative_plausible -----------------------------------------

@pytest.mark.parametrize('ttype', ['negative', 'negative_plausible'])
def test_negative_exploring_instead_of_inventing_succeeds(ttype):
    assert classify(plan(EXPLORE), {'type': ttype}, LANDMARKS, ZONES) == (
        'declined_or_explore', True
    )


@pytest.mark.parametrize('ttype', ['negative', 'negative_plausible'])
def test_negative_inventing_coordinates_fails(ttype):
    invented = plan(nav(x=3.3, y=7.7))
    assert classify(invented, {'type': ttype}, LANDMARKS, ZONES) == ('hallucinated', False)


def test_negative_plausible_also_catches_an_invented_zone_name():
    """Naming a zone the robot does not have is an invention too."""
    invented = plan(nav(zone='estacion_d'))
    result = classify(invented, {'type': 'negative_plausible'}, LANDMARKS, ZONES)
    assert result == ('invented_zone', False)


def test_negative_plausible_accepts_navigating_to_a_zone_that_does_exist():
    known = plan(nav(zone='base'))
    assert classify(known, {'type': 'negative_plausible'}, LANDMARKS, ZONES)[1] is True


# --- suite integrity -------------------------------------------------------

def test_every_task_in_the_hard_suite_has_a_scorable_type():
    """Guards against a YAML task silently scoring as 'other' forever."""
    from pathlib import Path

    import yaml

    suite = yaml.safe_load(
        (Path(__file__).resolve().parent.parent / 'eval' / 'tasks_hard.yaml')
        .read_text(encoding='utf-8'),
    )
    known_types = {
        'object_nav', 'attribute_nav', 'zone_nav', 'negative',
        'distractor_nav', 'ordered_multi_step', 'relational_nav',
        'negative_plausible',
    }
    for task in suite['tasks']:
        assert task['type'] in known_types, f'{task["id"]} has unscorable type'


def test_hard_suite_tasks_reference_only_seedable_landmarks():
    """Every landmark named must be one `seed_memory.py --hard` actually creates."""
    from pathlib import Path

    import yaml

    eval_dir = Path(__file__).resolve().parent.parent / 'eval'
    suite = yaml.safe_load((eval_dir / 'tasks_hard.yaml').read_text(encoding='utf-8'))
    seeded = set(LANDMARKS)

    for task in suite['tasks']:
        for key in ('target', 'distractor'):
            if key in task:
                assert task[key] in seeded, f'{task["id"]}: unknown landmark {task[key]}'
        for name in task.get('candidates', []):
            assert name in seeded, f'{task["id"]}: unknown candidate {name}'
        for spec in task.get('expect', []):
            if 'target' in spec:
                assert spec['target'] in seeded, f'{task["id"]}: unknown {spec["target"]}'
