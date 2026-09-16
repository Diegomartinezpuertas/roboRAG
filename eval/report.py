"""Aggregates eval/results/<suite>/*.json into a Markdown table by task type.

Usage: python3 report.py [suite] [--root results/<experiment>]   (suite defaults to 'full')
"""

import json
import sys
from pathlib import Path

RESULTS_ROOT = Path(__file__).resolve().parent / 'results'
# A column appears only when its results file exists: rag_unchecked was added
# with the plan check (ADR-032), and older result sets do not have it.
CONDITION_ORDER = [
    ('rag', 'With RAG'),
    ('norag', 'Without RAG'),
    ('rag_unchecked', 'With RAG, plan check off'),
    ('sql', 'LLM → SQL over the same places'),
]
TYPE_LABELS = {
    'object_nav': 'Object-referenced nav (RAG-dependent)',
    'attribute_nav': 'Description-referenced nav (self-built memory)',
    'zone_nav': 'Known-zone nav (control)',
    'negative': 'Impossible goal (hallucination check)',
    # tasks_hard.yaml. Without these the hard suite's table came out empty —
    # every type was filtered out as unknown (fixed 2026-09-16).
    'distractor_nav': 'Disambiguation (confusable neighbour)',
    'ordered_multi_step': 'Ordered multi-step plan',
    'relational_nav': 'Spatial relation ("nearest to base")',
    'negative_plausible': 'Plausible nonexistent place',
}


def load(suite, cond):
    """Loads one condition's run records (e.g. results/full/rag.json), [] if absent."""
    path = RESULTS_ROOT / suite / f'{cond}.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []


def rate(runs, ttype):
    """Returns (successes, runs) for one task type, or None when it has no runs."""
    # .get with object_nav default keeps old-format results (reduced suite,
    # which predates the 'type' field) readable.
    subset = [r for r in runs if r.get('type', 'object_nav') == ttype]
    if not subset:
        return None
    ok = sum(r['success'] for r in subset)
    return ok, len(subset)


def main():
    """Writes results/<suite>/report.md and prints the table."""
    global RESULTS_ROOT
    args = [a for a in sys.argv[1:] if not a.startswith('--root')]
    if '--root' in sys.argv:
        RESULTS_ROOT = Path(__file__).resolve().parent / sys.argv[sys.argv.index('--root') + 1]
        args = [a for a in args if a != sys.argv[sys.argv.index('--root') + 1]]
    suite = args[0] if args else 'full'
    data = {cond: load(suite, cond) for cond, _ in CONDITION_ORDER}
    columns = [(cond, label) for cond, label in CONDITION_ORDER if data[cond]]
    types = [
        t for t in TYPE_LABELS
        if any(r.get('type', 'object_nav') == t for runs in data.values() for r in runs)
    ]

    header = '| Task type | ' + ' | '.join(label for _, label in columns) + ' |'
    sep = '|' + '---|' * (len(columns) + 1)
    lines = [header, sep]
    for ttype in types:
        cells = []
        for cond, _ in columns:
            r = rate(data[cond], ttype)
            cells.append(f'{r[0]}/{r[1]} ({r[0] / r[1] * 100:.0f}%)' if r else '—')
        lines.append(f'| {TYPE_LABELS[ttype]} | ' + ' | '.join(cells) + ' |')

    table = '\n'.join(lines)
    print(f'Suite: {suite}\n')
    print(table)
    out = RESULTS_ROOT / suite / 'report.md'
    out.write_text(f'# Benchmark: {suite}\n\n{table}\n', encoding='utf-8')
    print(f'\nWrote {out}')


if __name__ == '__main__':
    main()
