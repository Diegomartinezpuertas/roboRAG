"""Tests for plan JSON parsing and result summarization."""

import pytest

from robot_brain.plan_parsing import parse_plan, summarize_results


def test_parse_plan_clean_json():
    plan = parse_plan(
        '{"reasoning":"r","steps":[{"skill":"navigate","params":{"x":1.0,"y":2.0}}]}',
    )
    assert plan['reasoning'] == 'r'
    assert plan['steps'][0]['skill'] == 'navigate'
    assert plan['steps'][0]['params'] == {'x': 1.0, 'y': 2.0}


def test_parse_plan_strips_json_code_fence():
    assert parse_plan('```json\n{"steps":[]}\n```') == {'steps': []}


def test_parse_plan_strips_bare_code_fence():
    assert parse_plan('```\n{"steps":[]}\n```') == {'steps': []}


def test_parse_plan_invalid_raises_valueerror():
    with pytest.raises(ValueError):
        parse_plan('this is not json')


def test_summarize_results_all_ok():
    assert 'correctamente' in summarize_results([{'skill': 'navigate', 'error': None}])


def test_summarize_results_reports_first_error():
    msg = summarize_results([
        {'skill': 'navigate', 'error': None},
        {'skill': 'perceive', 'error': 'boom'},
    ])
    assert 'boom' in msg
