"""Tests for the simulation-speed estimate shown next to the drive controls."""

import pytest

from robot_dashboard.sim_clock import RtfEstimator


def feed(estimator, rate, seconds, start_wall=100.0, start_sim=0.0, hz=10):
    for i in range(int(seconds * hz) + 1):
        wall = start_wall + i / hz
        estimator.add(start_sim + (wall - start_wall) * rate, wall)
    return start_wall + seconds


@pytest.mark.parametrize('rate', [1.0, 0.6, 0.15])
def test_a_steady_simulation_reports_its_real_time_factor(rate):
    estimator = RtfEstimator()
    now = feed(estimator, rate, seconds=4)
    assert estimator.rtf(now) == pytest.approx(rate, abs=0.01)


def test_the_estimate_follows_a_slowdown_within_the_window():
    estimator = RtfEstimator(window_sec=5.0)
    now = feed(estimator, 1.0, seconds=10)
    now = feed(estimator, 0.2, seconds=10, start_wall=now, start_sim=10.0)
    assert estimator.rtf(now) == pytest.approx(0.2, abs=0.02)


def test_no_estimate_without_enough_data_or_when_clock_goes_silent():
    estimator = RtfEstimator()
    assert estimator.rtf(0.0) is None
    now = feed(estimator, 1.0, seconds=4)
    assert estimator.rtf(now + 10.0) is None      # simulator paused or stopped


def test_a_restarted_simulator_does_not_produce_a_negative_factor():
    estimator = RtfEstimator()
    now = feed(estimator, 1.0, seconds=4, start_sim=500.0)
    now = feed(estimator, 1.0, seconds=2, start_wall=now + 0.1, start_sim=0.0)
    assert estimator.rtf(now) == pytest.approx(1.0, abs=0.05)
