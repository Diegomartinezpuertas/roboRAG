"""Plan check before execution (ADR-032) — pure logic, with a fake resolver."""

import pytest

from robot_brain.plan_validation import (
    GROUNDING_TOLERANCE_M,
    Resolution,
    parse_resolution,
    places_from_context,
    places_from_zones,
    validate_plan,
)

CONTEXT = [
    '## Template: explore an area\n\nPlan: explore.',  # knowledge chunk: no coordinates
    'estacion_a at (x=-2.10, y=-1.61) in unknown area: estacion_a, a named location',
    'estacion_b at (x=-0.05, y=-1.31): estacion_b, a named location in the house',
    'area at (x=2.05, y=-0.01): predominantly brown, a cluttered space with many objects',
]
ZONES = {'base': {'x_min': -0.35, 'y_min': -1.61, 'x_max': 0.25, 'y_max': -1.01}}
CANDIDATES = places_from_context(CONTEXT) + places_from_zones(ZONES)


def resolver(places=(), missing=()):
    calls = []

    def resolve(goal, candidates):
        calls.append(goal)
        return Resolution(places=tuple(places), missing=tuple(missing))

    resolve.calls = calls
    return resolve


def nav(**params):
    return {'skill': 'navigate', 'params': params}


def check(steps, resolve=None):
    return validate_plan('goal', steps, CANDIDATES, set(ZONES), resolve or resolver())


# --- where places come from -------------------------------------------------

def test_places_come_from_memory_documents_and_zone_centres_not_knowledge():
    labels = [(p.label, p.x, p.y, p.kind) for p in CANDIDATES]
    assert labels == [
        ('estacion_a', -2.10, -1.61, 'memory'),
        ('estacion_b', -0.05, -1.31, 'memory'),
        ('area', 2.05, -0.01, 'memory'),
        ('base', -0.05, -1.31, 'zone'),
    ]


def test_parse_resolution_is_one_based_and_drops_numbers_outside_the_list():
    raw = '```json\n{"places": [1, 9, "x"], "missing": ["estacion_d", ""]}\n```'
    parsed = parse_resolution(raw, 3)
    assert parsed == Resolution(places=(0,), missing=('estacion_d',))


def test_parse_resolution_rejects_non_json():
    with pytest.raises(ValueError):
        parse_resolution('the goal asks for estacion_a', 3)


# --- exact checks: no model involved ----------------------------------------

def test_a_known_zone_passes_and_an_invented_one_becomes_explore():
    resolve = resolver()
    result = check([nav(zone='base'), {'skill': 'perceive'}], resolve)
    assert not result.changed and resolve.calls == []

    result = check([nav(zone='estacion_d'), {'skill': 'perceive'}])
    assert result.steps == [{'skill': 'explore', 'params': {}}, {'skill': 'perceive'}]
    assert 'estacion_d' in result.notes[0]


def test_a_point_that_is_no_known_place_becomes_explore_without_asking_the_model():
    # The no-RAG failure: "Ve a estacion_d" -> navigate(-2, 5), a number from nowhere.
    resolve = resolver()
    result = check([nav(x=-2, y=5)], resolve)
    assert result.steps == [{'skill': 'explore', 'params': {}}]
    assert resolve.calls == []


def test_a_point_rounded_by_the_planner_still_counts_as_the_place():
    result = check([nav(x=-2.1, y=-1.6)], resolver(places=[0]))
    assert not result.changed
    assert GROUNDING_TOLERANCE_M >= 0.1


# --- the borrowed-coordinates check -----------------------------------------

def test_coordinates_borrowed_for_a_place_not_in_memory_become_explore():
    # The measured failure: "Ve a estacion_d" -> estacion_a's coordinates.
    result = check([nav(x=-2.10, y=-1.61), {'skill': 'perceive'}],
                   resolver(places=[], missing=['estacion_d']))
    assert result.steps == [{'skill': 'explore', 'params': {}}, {'skill': 'perceive'}]
    assert '"estacion_d"' in result.notes[0] and '"estacion_a"' in result.notes[0]


def test_an_existing_explore_is_not_duplicated_by_a_replacement():
    result = check([{'skill': 'explore', 'params': {'duration_sec': 10}},
                    nav(x=-2.10, y=-1.61)], resolver(missing=['estacion_d']))
    assert result.steps == [{'skill': 'explore', 'params': {'duration_sec': 10}}]


def test_a_place_the_goal_asks_for_is_kept_even_if_something_else_is_missing():
    # "Ve a estacion_a y luego a estacion_d": the first leg stands.
    result = check([nav(x=-2.10, y=-1.61), nav(x=-2, y=5)],
                   resolver(places=[0], missing=['estacion_d']))
    assert result.steps[0] == nav(x=-2.10, y=-1.61)
    assert result.steps[1] == {'skill': 'explore', 'params': {}}


def test_a_description_the_goal_matches_is_kept():
    result = check([nav(x=2.05, y=-0.01)], resolver(places=[2]))
    assert not result.changed and not result.disagreements


def test_a_resolver_that_picks_another_known_place_does_not_override_the_planner():
    # Spatial relations and confusable names stay the planner's call.
    result = check([nav(x=-0.05, y=-1.31)], resolver(places=[0]))
    assert not result.changed
    assert len(result.disagreements) == 1


def test_a_missing_name_that_is_a_known_place_is_not_missing():
    # Design replay: "the station farthest from estacion_a" -> the resolver
    # listed estacion_a, a reference point that is in memory, as missing.
    result = check([nav(x=-0.05, y=-1.31)], resolver(places=[], missing=['Estación_A', 'BASE']))
    assert not result.changed


def test_a_failing_resolver_keeps_the_plan_and_is_called_once():
    calls = []

    def broken(goal, candidates):
        calls.append(goal)
        raise ConnectionError('Ollama is down')

    result = check([nav(x=-2.10, y=-1.61), nav(x=-0.05, y=-1.31)], broken)
    assert not result.changed and len(calls) == 1


def test_plans_without_navigation_cost_no_model_call():
    resolve = resolver()
    result = check([{'skill': 'explore', 'params': {}}, {'skill': 'scan_360'}], resolve)
    assert not result.changed and resolve.calls == []



def test_a_remembered_place_named_as_a_zone_is_replaced_not_repaired():
    # Run B of ADR-032: "Ve a la estación central" -> navigate(zone="estacion_c").
    # Repairing it to estacion_c's coordinates left only the resolver between the
    # robot and a borrowed place, and the resolver let it through.
    result = check([nav(zone='estacion_b'), {'skill': 'perceive'}])
    assert result.steps == [{'skill': 'explore', 'params': {}}, {'skill': 'perceive'}]
    assert '"estacion_b"' in result.notes[0]
