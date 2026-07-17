"""Runs the RAG-vs-no-RAG PLANNING benchmark and writes per-condition results.

Measures the hypothesis at the decision level (see ADR-013): does the
RAG-equipped planner navigate DIRECTLY to a remembered landmark's coordinates,
versus falling back to blind EXPLORE without RAG? Run in the planner's `dry_run`
mode (plan, don't drive), so it is reliable and reproducible (temperature 0) and
independent of the flaky low-level navigation.

Task types (see the YAML):
  object_nav : success = navigate step within tol of the landmark coordinates
               (this is the RAG-dependent measurement).
  zone_nav   : control; success = navigate to the zone (by name, or within tol
               of its center) — should hold in both conditions.
  negative   : hallucination check; success = the plan does NOT contain a
               navigate-to-coordinates step for a place that does not exist.

Results go to eval/results/<suite>/<condition>.json, where <suite> is the YAML
stem without the "tasks_" prefix (e.g. tasks_full.yaml -> results/full/).

Usage: python3 run_benchmark.py [tasks_full.yaml]
"""

import json
import math
import subprocess
import sys
import time
from pathlib import Path

import rclpy
import yaml

from robot_zones.zone_store import ZoneStore

from bench_lib import BenchNode, spin_in_thread

HERE = Path(__file__).resolve().parent
LANDMARKS_FILE = HERE / 'landmarks.json'
ZONES_DB = '/home/diego/robot_ws/data/zones.db'
PLANNER = '/llm_planner_node'
CONDITIONS = [('rag', True), ('norag', False)]
NAV_TOLERANCE_M = 0.75


def set_param(name: str, value: str) -> None:
    subprocess.run(['ros2', 'param', 'set', PLANNER, name, value],
                   check=False, capture_output=True, text=True)


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


def classify(plan, task, landmarks, zones):
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

    return 'other', False


def load_zone_centers():
    zones = ZoneStore(ZONES_DB).load_all()
    return {n: ((a['x_min'] + a['x_max']) / 2.0, (a['y_min'] + a['y_max']) / 2.0)
            for n, a in zones.items()}


def main():
    tasks_file = HERE / (sys.argv[1] if len(sys.argv) > 1 else 'tasks_full.yaml')
    suite_name = tasks_file.stem.replace('tasks_', '')
    suite = yaml.safe_load(tasks_file.read_text(encoding='utf-8'))
    landmarks = json.loads(LANDMARKS_FILE.read_text(encoding='utf-8'))
    zones = load_zone_centers()
    reps = suite['repetitions']
    out_dir = HERE / 'results' / suite_name
    out_dir.mkdir(parents=True, exist_ok=True)

    rclpy.init()
    node = BenchNode()
    spin = spin_in_thread(node)

    set_param('dry_run', 'true')
    set_param('zones_in_prompt', 'true')

    for cond_name, rag_on in CONDITIONS:
        set_param('rag_enabled', 'true' if rag_on else 'false')
        time.sleep(1.0)
        node.get_logger().info(f'=== Condition: {cond_name} (rag_enabled={rag_on}) ===')
        runs = []
        for task in suite['tasks']:
            for rep in range(reps):
                t0 = time.monotonic()
                node.publish_goal(task['goal'])
                plan = node.wait_for_plan(timeout_sec=60.0)
                latency = time.monotonic() - t0
                decision, success = classify(plan or {}, task, landmarks, zones)
                runs.append({
                    'task_id': task['id'], 'type': task['type'], 'rep': rep,
                    'condition': cond_name, 'goal': task['goal'],
                    'decision': decision, 'success': success,
                    'latency_sec': round(latency, 2), 'plan': plan,
                })
                node.get_logger().info(
                    f'[{cond_name}] {task["id"]} rep {rep + 1}: {decision} '
                    f'(ok={success}, {latency:.1f}s)',
                )
                time.sleep(1.0)
        out = out_dir / f'{cond_name}.json'
        out.write_text(json.dumps(runs, indent=2, ensure_ascii=False), encoding='utf-8')
        node.get_logger().info(f'Wrote {out}')

    set_param('dry_run', 'false')
    spin.stop()
    rclpy.shutdown()


if __name__ == '__main__':
    main()
