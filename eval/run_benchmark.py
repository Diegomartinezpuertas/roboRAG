"""Runs the RAG-vs-no-RAG PLANNING benchmark and writes per-condition results.

Measures the hypothesis at the decision level (see ADR-013): does the
RAG-equipped planner navigate DIRECTLY to a remembered landmark's coordinates,
versus falling back to blind EXPLORE without RAG? Run in the planner's `dry_run`
mode (plan, don't drive), so it is reliable and reproducible (temperature 0) and
independent of the flaky low-level navigation.

Scoring lives in `scoring.py` (pure logic, unit-tested without ROS); the task
types it recognises are documented there and in each suite's YAML header.

Suites:
  tasks_full.yaml     the headline ablation (object/attribute/zone/negative)
  tasks_phrasing.yaml robustness to paraphrase and language
  tasks_hard.yaml     disambiguation, ordered multi-step, spatial relations —
                      the suite with headroom (needs `seed_memory.py --hard`)

Results go to eval/results/<suite>/<condition>.json, where <suite> is the YAML
stem without the "tasks_" prefix (e.g. tasks_full.yaml -> results/full/).

Usage: python3 run_benchmark.py [tasks_full.yaml]
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import rclpy
import yaml

from robot_zones.zone_store import ZoneStore

from bench_lib import BenchNode, spin_in_thread
from scoring import classify

HERE = Path(__file__).resolve().parent
LANDMARKS_FILE = HERE / 'landmarks.json'
# Workspace root: ROBOT_WS when the shell was prepared with setup_env.sh,
# otherwise the parent of eval/ — which is the workspace root by layout.
WS_ROOT = Path(os.environ.get('ROBOT_WS', HERE.parent))
ZONES_DB = str(WS_ROOT / 'data' / 'zones.db')
PLANNER = '/llm_planner_node'
CONDITIONS = [('rag', True), ('norag', False)]


def set_param(name: str, value: str) -> None:
    """Sets a planner parameter, verifying it actually took effect.

    `ros2 param set` can silently no-op when node discovery has not settled yet
    (observed right after launch, and on WSL2's multi-NIC DDS). A missed
    `rag_enabled` flip would run a whole condition under the wrong ablation and
    quietly corrupt the result — so set the value, read it back, and retry until
    it sticks.
    """
    for _ in range(10):
        subprocess.run(['ros2', 'param', 'set', PLANNER, name, value],
                       check=False, capture_output=True, text=True)
        got = subprocess.run(['ros2', 'param', 'get', PLANNER, name],
                             check=False, capture_output=True, text=True)
        if value.lower() in got.stdout.lower():
            return
        time.sleep(0.5)
    print(f'WARNING: could not confirm {PLANNER} {name}={value}', file=sys.stderr)


def load_zone_centers():
    """Returns {zone name: (x, y) centre} for every zone in the zones database."""
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
                retrieved = (
                    node.query_rag(task['goal'], 'semantic_map', top_k=5)
                    if task['type'] == 'attribute_nav' else ()
                )
                decision, success = classify(plan or {}, task, landmarks, zones, retrieved)
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
