"""Tests for the planner/reporter prompt builders and language detection."""

from robot_brain.prompts import (
    _detect_language,
    build_report_prompt,
    build_user_prompt,
)


def test_detect_language_spanish():
    assert _detect_language('Ve a la cocina y dime qué hay') == 'Spanish'
    assert _detect_language('Explora la zona') == 'Spanish'
    assert _detect_language('Dime dónde estás') == 'Spanish'


def test_detect_language_english():
    assert _detect_language('Go to the kitchen') == 'English'
    assert _detect_language('Explore for 30 seconds') == 'English'


def test_build_user_prompt_includes_goal_zones_context():
    prompt = build_user_prompt('go home', ['ctx1', 'ctx2'], ['kitchen', 'bedroom'])
    assert 'go home' in prompt
    assert 'ctx1' in prompt and 'ctx2' in prompt
    assert 'bedroom, kitchen' in prompt  # zones sorted


def test_build_user_prompt_empty_placeholders():
    prompt = build_user_prompt('g', [], [])
    assert '(no context found)' in prompt
    assert '(none defined yet)' in prompt


def test_build_report_prompt_marks_success_and_failure():
    results = [
        {'skill': 'navigate', 'result': {'reached': True}, 'error': None},
        {'skill': 'perceive', 'result': None, 'error': 'boom'},
    ]
    prompt = build_report_prompt('Ve a casa', results)
    assert 'navigate' in prompt and 'perceive' in prompt
    assert 'FAILED' in prompt and 'boom' in prompt
    assert 'Reply in Spanish' in prompt


def test_build_report_prompt_english_directive():
    prompt = build_report_prompt('Go home', [{'skill': 'navigate', 'result': {}, 'error': None}])
    assert 'Reply in English' in prompt


def test_build_report_prompt_no_steps():
    prompt = build_report_prompt('Go home', [])
    assert '(no steps executed)' in prompt


def test_build_user_prompt_gives_zone_centres_when_bounds_are_known():
    # Without the centre, "the station nearest the base" has no reference point.
    zones = {'base': {'x_min': 1.0, 'y_min': -1.0, 'x_max': 2.0, 'y_max': 0.0}}
    prompt = build_user_prompt('go', [], zones)
    assert 'base (centre x=1.50, y=-0.50)' in prompt


def test_build_report_prompt_includes_plan_corrections():
    prompt = build_report_prompt(
        'Ve a estacion_d', [{'skill': 'explore', 'result': {}, 'error': None}],
        ['No tengo "estacion_d" en la memoria'],
    )
    assert 'PLAN CORRECTIONS' in prompt and 'estacion_d' in prompt


def test_build_report_prompt_without_corrections_has_no_block():
    assert 'PLAN CORRECTIONS' not in build_report_prompt('Go home', [])


def test_build_resolver_prompt_numbers_places_from_one():
    from robot_brain.plan_validation import places_from_context
    from robot_brain.prompts import build_resolver_prompt

    places = places_from_context(['estacion_a at (x=1.00, y=2.00): a named location'])
    prompt = build_resolver_prompt('Ve a estacion_d', places)
    assert 'GOAL: Ve a estacion_d' in prompt
    assert '1. estacion_a at (x=1.00, y=2.00)' in prompt


def test_places_sql_prompt_names_no_benchmark_landmark_or_attribute():
    # The SQL writer was designed without looking at the suites (ADR-033).
    from robot_brain.prompts import PLACES_SQL_SYSTEM_PROMPT, build_places_sql_prompt

    # "white" is allowed only as one of the descriptor's colour names. One word a
    # suite goal also uses, "despejado", is in the prompt as a translation
    # example; ADR-033 discloses it rather than pretending the prompt is clean.
    for word in ('estacion', 'blanca', 'zona base', 'garaje', 'muchos objetos'):
        assert word not in PLACES_SQL_SYSTEM_PROMPT.lower()
    assert 'GOAL: Ve a punto_3' in build_places_sql_prompt('Ve a punto_3')
