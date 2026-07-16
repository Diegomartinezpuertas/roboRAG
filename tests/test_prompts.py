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
