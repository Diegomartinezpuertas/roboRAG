"""Does describing a zone by its purpose make it findable by purpose? (ADR-022)

The robot's zones used to be indexed as a name plus a bounding box:

    cocina at (x=1.75, y=-0.75) in cocina: User-defined zone "cocina"
    covering x[1.00, 2.50] y[-1.50, 0.00] in the map frame.

That answers "ve a la cocina". This script measures what it does with "ve donde
se suele cocinar" — a goal that names the room's *function*, never the room —
against the same zones described with `robot_zones.room_semantics`.

For each description style it reports:
  - top-1 accuracy: the expected zone ranks first among all five zones
  - mean score of the correct zone
  - how many queries clear the planner's rag_score_threshold (0.40), below
    which a hit never reaches the prompt at all (ADR-011)

No ROS required. Requires bge-m3 pulled in Ollama:
    ollama pull bge-m3

Usage: python3 room_semantics_bench.py   -> writes results/room_semantics.json
"""

import json
import statistics
import sys
from pathlib import Path

import numpy as np
import ollama

# robot_zones is a ROS package directory, but room_semantics is pure stdlib —
# importable straight from the source tree, like the rest of this suite.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'src' / 'robot_zones'))

from robot_zones.room_semantics import describe_zone, room_label  # noqa: E402

RESULTS_FILE = Path(__file__).resolve().parent / 'results' / 'room_semantics.json'
MODEL = 'bge-m3'
# The planner drops anything below this before building the prompt (ADR-011).
SCORE_THRESHOLD = 0.40

# Five zones as a user would draw and name them in the dashboard.
ZONES = {
    'cocina': {'x_min': 0.5, 'y_min': -1.0, 'x_max': 2.0, 'y_max': 0.5},
    'salon': {'x_min': -3.0, 'y_min': 0.0, 'x_max': -1.0, 'y_max': 2.0},
    'dormitorio': {'x_min': 2.0, 'y_min': 2.0, 'x_max': 4.0, 'y_max': 4.0},
    'bano': {'x_min': -1.0, 'y_min': -3.0, 'x_max': 0.0, 'y_max': -2.0},
    'despacho': {'x_min': 4.0, 'y_min': -2.0, 'x_max': 6.0, 'y_max': 0.0},
}

# Goals that ask for a room by what happens in it, never by its name.
QUERIES = [
    ('donde se suele cocinar', 'cocina'),
    ('ve donde se prepara la comida', 'cocina'),
    ('where people usually cook', 'cocina'),
    ('donde se ve la television', 'salon'),
    ('ve donde la gente se sienta a descansar', 'salon'),
    ('donde se duerme', 'dormitorio'),
    ('ve donde la gente duerme por la noche', 'dormitorio'),
    ('donde me puedo duchar', 'bano'),
    ('ve donde esta el retrete', 'bano'),
    ('donde se trabaja con el ordenador', 'despacho'),
    ('ve al sitio donde se estudia', 'despacho'),
]


def _center(area: dict) -> tuple[float, float]:
    return (area['x_min'] + area['x_max']) / 2, (area['y_min'] + area['y_max']) / 2


def plain_document(name: str, area: dict) -> str:
    """The zone document as it was stored before ADR-022: name plus bounds."""
    x, y = _center(area)
    return (
        f'{name} at (x={x:.2f}, y={y:.2f}) in {name}: '
        f'User-defined zone "{name}" covering x[{area["x_min"]:.2f}, {area["x_max"]:.2f}] '
        f'y[{area["y_min"]:.2f}, {area["y_max"]:.2f}] in the map frame. '
        f'The robot can navigate to it or explore inside it by name.'
    )


def functional_document(name: str, area: dict) -> str:
    """The zone document as stored now: the same, plus what the room is for."""
    x, y = _center(area)
    return (
        f'{room_label(name)} at (x={x:.2f}, y={y:.2f}) in {name}: '
        f'{describe_zone(name, area)}'
    )


def cosine(a, b) -> float:
    """Cosine similarity between two embedding vectors."""
    a, b = np.array(a), np.array(b)
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def evaluate(client, builder) -> dict:
    """Scores one description style over every query.

    Args:
        client: Ollama client.
        builder: Function (name, area) -> stored document text.

    Returns:
        Dict with per-query rows and the aggregate metrics.
    """
    names = list(ZONES)
    vectors = client.embed(model=MODEL, input=[builder(n, ZONES[n]) for n in names])['embeddings']
    rows = []
    for query, expected in QUERIES:
        query_vector = client.embed(model=MODEL, input=query)['embeddings'][0]
        scores = {name: cosine(query_vector, v) for name, v in zip(names, vectors, strict=True)}
        ranked = sorted(scores, key=scores.get, reverse=True)
        rows.append({
            'query': query,
            'expected': expected,
            'top1': ranked[0] == expected,
            'retrieved': ranked[0],
            'correct_score': round(scores[expected], 4),
            'above_threshold': scores[expected] >= SCORE_THRESHOLD,
        })
    return {
        'runs': rows,
        'top1_acc': sum(r['top1'] for r in rows) / len(rows),
        'above_threshold': sum(r['above_threshold'] for r in rows),
        'mean_correct_score': round(statistics.mean(r['correct_score'] for r in rows), 4),
    }


def main() -> None:
    """Runs both description styles and writes the comparison."""
    client = ollama.Client(host='http://localhost:11434')
    results = {
        'model': MODEL,
        'score_threshold': SCORE_THRESHOLD,
        'queries': len(QUERIES),
        'zones': list(ZONES),
        'styles': {
            'name_and_bounds': evaluate(client, plain_document),
            'functional': evaluate(client, functional_document),
        },
    }

    RESULTS_FILE.parent.mkdir(exist_ok=True)
    RESULTS_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')

    print(f'{"style":18} {"top-1":>7} {">=0.40":>7} {"score":>7}')
    for style, summary in results['styles'].items():
        print(
            f'{style:18} {summary["top1_acc"] * 100:6.0f}% '
            f'{summary["above_threshold"]:4}/{len(QUERIES)} {summary["mean_correct_score"]:7.3f}',
        )
    print('\nQueries the old descriptions sent to the wrong room:')
    for plain, rich in zip(
        results['styles']['name_and_bounds']['runs'],
        results['styles']['functional']['runs'], strict=True,
    ):
        if not plain['top1']:
            fixed = 'fixed' if rich['top1'] else f'still {rich["retrieved"]}'
            print(
                f'  "{plain["query"]}" -> {plain["retrieved"]} '
                f'(expected {plain["expected"]}) [{fixed}]',
            )
    print(f'\nWrote {RESULTS_FILE}')


if __name__ == '__main__':
    main()
