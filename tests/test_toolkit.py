"""Tests for the plan executor (skill dispatch loop)."""

from robot_brain.toolkit import VALID_SKILLS, execute_plan


class FakeToolkit:
    """Records skill calls and optionally fails on a given skill."""

    def __init__(self, fail_on=None):
        self.calls = []
        self.fail_on = fail_on

    def call_skill(self, name, params):
        self.calls.append((name, params))
        if name == self.fail_on:
            raise RuntimeError(f'{name} boom')
        return {'ok': True, 'skill': name}


def test_valid_skills_set():
    assert set(VALID_SKILLS) == {'navigate', 'explore', 'perceive', 'report'}


def test_executes_all_steps_in_order():
    toolkit = FakeToolkit()
    steps = [
        {'skill': 'navigate', 'params': {'x': 1}},
        {'skill': 'perceive', 'params': {'query': 'q'}},
    ]
    results = execute_plan(toolkit, steps)
    assert [r['skill'] for r in results] == ['navigate', 'perceive']
    assert all(r['error'] is None for r in results)
    assert toolkit.calls == [('navigate', {'x': 1}), ('perceive', {'query': 'q'})]


def test_stops_at_first_failure():
    toolkit = FakeToolkit(fail_on='navigate')
    steps = [{'skill': 'navigate', 'params': {}}, {'skill': 'perceive', 'params': {}}]
    results = execute_plan(toolkit, steps)
    assert len(results) == 1
    assert results[0]['error'] is not None
    assert len(toolkit.calls) == 1  # perceive never reached


def test_unknown_skill_stops_without_dispatch():
    toolkit = FakeToolkit()
    results = execute_plan(toolkit, [{'skill': 'fly', 'params': {}}])
    assert len(results) == 1
    assert 'Unknown skill' in results[0]['error']
    assert toolkit.calls == []
