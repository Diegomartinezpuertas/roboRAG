"""Renders the benchmark result charts (PNG) for the README and analysis doc.

Reads:
  results/full/{rag,norag}.json       — main ablation suite (with latency)
  results/phrasing/{rag,norag}.json   — phrasing/language robustness suite
  results/embeddings.json             — nomic-embed-text vs bge-m3 comparison

Writes results/benchmark.png, results/phrasing.png, results/embeddings.png.

Chart conventions (see the project's data-viz method): fixed categorical
color assignment (never re-mapped between charts), thin bars with a surface
gap, recessive grid/axes, direct value labels plus a legend, text in ink
colors (never the series color).

Usage: python3 plot_results.py
"""

import json
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
RESULTS = HERE / 'results'

# Validated categorical palette (light mode), fixed assignment:
BLUE = '#2a78d6'    # slot 1 — "With RAG" / bge-m3
GREEN = '#008300'   # slot 2 — "Without RAG" / nomic-embed-text
SURFACE = '#fcfcfb'
INK = '#0b0b0b'
INK_2 = '#52514e'
GRID = '#e6e5e0'

TYPE_LABELS = {
    'object_nav': 'Object-referenced\nnav (RAG-dependent)',
    'zone_nav': 'Known-zone nav\n(control)',
    'negative': 'Impossible goal\n(hallucination check)',
}
PHRASING_LABELS = {
    'es_imperative': 'Spanish\nimperative',
    'es_paraphrase': 'Spanish\nparaphrase',
    'en': 'English',
}


def load(suite, cond):
    path = RESULTS / suite / f'{cond}.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []


def style_axes(ax):
    ax.set_facecolor(SURFACE)
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    for side in ('left', 'bottom'):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelcolor=INK_2, length=0)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def grouped_bars(ax, categories, series, ylabel, ymax=1.12, as_pct=True):
    """series = [(label, color, values)] — thin grouped bars, direct labels."""
    width = 0.32
    n = len(categories)
    for i, (label, color, values) in enumerate(series):
        xs = [x + (i - (len(series) - 1) / 2) * (width + 0.04) for x in range(n)]
        ax.bar(xs, values, width=width, color=color, label=label,
               edgecolor=SURFACE, linewidth=2, zorder=3)
        for x, v in zip(xs, values, strict=True):
            text = f'{v * 100:.0f}%' if as_pct else f'{v:.1f}'
            ax.annotate(text, (x, v), textcoords='offset points', xytext=(0, 4),
                        ha='center', fontsize=9, color=INK)
    ax.set_xticks(range(n))
    ax.set_xticklabels(categories, fontsize=9, color=INK)
    ax.set_ylim(0, ymax)
    if as_pct:
        ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
        ax.set_yticklabels(['0%', '25%', '50%', '75%', '100%'], fontsize=9)
    ax.set_ylabel(ylabel, fontsize=10, color=INK_2)
    style_axes(ax)


def success_rate(runs, ttype):
    subset = [r for r in runs if r.get('type', 'object_nav') == ttype]
    return sum(r['success'] for r in subset) / len(subset) if subset else 0.0


def fig_benchmark():
    rag, norag = load('full', 'rag'), load('full', 'norag')
    types = ['object_nav', 'zone_nav', 'negative']
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2), facecolor=SURFACE,
                                   gridspec_kw={'width_ratios': [1.6, 1]})
    grouped_bars(
        ax1, [TYPE_LABELS[t] for t in types],
        [('With RAG', BLUE, [success_rate(rag, t) for t in types]),
         ('Without RAG', GREEN, [success_rate(norag, t) for t in types])],
        ylabel='Success rate',
    )
    ax1.set_title('Planning success by task type (30 runs, temperature 0)',
                  fontsize=11, color=INK, pad=26)
    ax1.legend(frameon=False, fontsize=9, ncol=2, loc='lower left',
               bbox_to_anchor=(0.0, 1.0), labelcolor=INK)

    # Latency: mean bar + individual run dots per condition.
    for i, (_label, color, runs) in enumerate(
            [('With RAG', BLUE, rag), ('Without RAG', GREEN, norag)]):
        lat = [r['latency_sec'] for r in runs if 'latency_sec' in r]
        if not lat:
            continue
        mean = sum(lat) / len(lat)
        ax2.bar([i], [mean], width=0.42, color=color, edgecolor=SURFACE,
                linewidth=2, zorder=3)
        ax2.plot([i + 0.32] * len(lat), lat, 'o', color=color, markersize=4,
                 alpha=0.55, zorder=4)
        ax2.annotate(f'{mean:.1f}s', (i, mean), textcoords='offset points',
                     xytext=(0, 4), ha='center', fontsize=9, color=INK)
    ax2.set_xticks([0, 1])
    ax2.set_xticklabels(['With RAG', 'Without RAG'], fontsize=9, color=INK)
    ax2.set_ylabel('Planning latency (s)', fontsize=10, color=INK_2)
    ax2.set_title('Goal → plan latency\n(bar = mean, dots = runs)',
                  fontsize=11, color=INK, pad=8)
    style_axes(ax2)

    fig.tight_layout()
    fig.savefig(RESULTS / 'benchmark.png', dpi=200, facecolor=SURFACE,
                bbox_inches='tight')
    print('Wrote', RESULTS / 'benchmark.png')


def phrasing_group(task_id):
    return task_id.split('_', 1)[1]  # a_es_imperative -> es_imperative


def fig_phrasing():
    rag, norag = load('phrasing', 'rag'), load('phrasing', 'norag')
    if not rag:
        print('No phrasing results yet, skipping phrasing.png')
        return
    groups = ['es_imperative', 'es_paraphrase', 'en']

    def rate(runs, group):
        subset = [r for r in runs if phrasing_group(r['task_id']) == group]
        return sum(r['success'] for r in subset) / len(subset) if subset else 0.0

    fig, ax = plt.subplots(figsize=(7.2, 4.2), facecolor=SURFACE)
    grouped_bars(
        ax, [PHRASING_LABELS[g] for g in groups],
        [('With RAG', BLUE, [rate(rag, g) for g in groups]),
         ('Without RAG', GREEN, [rate(norag, g) for g in groups])],
        ylabel='Direct-nav success rate',
    )
    ax.set_title('Robustness to phrasing and language\n'
                 '(same 3 landmarks, 3 phrasings each, 2 reps)',
                 fontsize=11, color=INK, pad=26)
    ax.legend(frameon=False, fontsize=9, ncol=2, loc='lower left',
              bbox_to_anchor=(0.0, 1.0), labelcolor=INK)
    fig.tight_layout()
    fig.savefig(RESULTS / 'phrasing.png', dpi=200, facecolor=SURFACE,
                bbox_inches='tight')
    print('Wrote', RESULTS / 'phrasing.png')


def fig_embeddings():
    path = RESULTS / 'embeddings.json'
    if not path.exists():
        print('No embeddings.json yet, skipping embeddings.png')
        return
    data = json.loads(path.read_text(encoding='utf-8'))
    langs = ['es', 'en']
    lang_labels = ['Spanish queries', 'English queries']
    series_defs = [('bge-m3', BLUE), ('nomic-embed-text', GREEN)]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2), facecolor=SURFACE)
    grouped_bars(
        ax1, lang_labels,
        [(m, c, [data['models'][m][lang]['top1_acc'] for lang in langs])
         for m, c in series_defs],
        ylabel='Top-1 retrieval accuracy',
    )
    ax1.set_title('Retrieval accuracy by query language\n(English document corpus)',
                  fontsize=11, color=INK, pad=26)
    ax1.legend(frameon=False, fontsize=9, ncol=2, loc='lower left',
               bbox_to_anchor=(0.0, 1.02), labelcolor=INK)

    # Margin: correct-doc score minus best wrong score (higher = safer).
    width = 0.32
    margins = defaultdict(list)
    for m, _ in series_defs:
        for lang in langs:
            margins[m].append(data['models'][m][lang]['mean_margin'])
    for i, (m, color) in enumerate(series_defs):
        xs = [x + (i - 0.5) * (width + 0.04) for x in range(len(langs))]
        ax2.bar(xs, margins[m], width=width, color=color, edgecolor=SURFACE,
                linewidth=2, zorder=3, label=m)
        for x, v in zip(xs, margins[m], strict=True):
            ax2.annotate(f'{v:+.3f}', (x, v), textcoords='offset points',
                         xytext=(0, 4 if v >= 0 else -14), ha='center',
                         fontsize=9, color=INK)
    ax2.axhline(0, color=INK_2, linewidth=0.8)
    ax2.set_xticks(range(len(langs)))
    ax2.set_xticklabels(lang_labels, fontsize=9, color=INK)
    ax2.set_ylabel('Mean margin (correct − best wrong)', fontsize=10, color=INK_2)
    ax2.set_title('Retrieval margin by query language\n(higher = more separable)',
                  fontsize=11, color=INK, pad=8)
    style_axes(ax2)

    fig.tight_layout()
    fig.savefig(RESULTS / 'embeddings.png', dpi=200, facecolor=SURFACE,
                bbox_inches='tight')
    print('Wrote', RESULTS / 'embeddings.png')


def main():
    fig_benchmark()
    fig_phrasing()
    fig_embeddings()


if __name__ == '__main__':
    main()
