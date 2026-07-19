"""Aggregates eval/results/<suite>/*.json into a Markdown table by task type.

Usage: python3 report.py [suite]   (suite defaults to 'full')
"""

import json
import sys
from pathlib import Path

RESULTS_ROOT = Path(__file__).resolve().parent / 'results'
CONDITION_ORDER = [('rag', 'With RAG'), ('norag', 'Without RAG')]
TYPE_LABELS = {
    'object_nav': 'Object-referenced nav (RAG-dependent)',
    'attribute_nav': 'Description-referenced nav (self-built memory)',
    'zone_nav': 'Known-zone nav (control)',
    'negative': 'Impossible goal (hallucination check)',
}


def load(suite, cond):
    path = RESULTS_ROOT / suite / f'{cond}.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []


def rate(runs, ttype):
    # .get with object_nav default keeps old-format results (reduced suite,
    # which predates the 'type' field) readable.
    subset = [r for r in runs if r.get('type', 'object_nav') == ttype]
    if not subset:
        return None
    ok = sum(r['success'] for r in subset)
    return ok, len(subset)


def main():
    suite = sys.argv[1] if len(sys.argv) > 1 else 'full'
    data = {cond: load(suite, cond) for cond, _ in CONDITION_ORDER}
    types = [
        t for t in TYPE_LABELS
        if any(r.get('type', 'object_nav') == t for runs in data.values() for r in runs)
    ]

    header = '| Task type | ' + ' | '.join(label for _, label in CONDITION_ORDER) + ' |'
    sep = '|' + '---|' * (len(CONDITION_ORDER) + 1)
    lines = [header, sep]
    for ttype in types:
        cells = []
        for cond, _ in CONDITION_ORDER:
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
