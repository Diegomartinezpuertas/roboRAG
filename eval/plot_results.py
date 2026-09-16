"""Renders the benchmark result charts (PNG) for the README and analysis doc.

Reads:
  results/full/{rag,norag}.json       — main ablation suite (with latency)
  results/phrasing/{rag,norag}.json   — phrasing/language robustness suite
  results/embeddings.json             — nomic-embed-text vs bge-m3 comparison
  results/room_semantics.json         — zone described by name vs by purpose

Writes results/benchmark.png, results/phrasing.png, results/embeddings.png,
results/room_semantics.png (plus hard{,_decisions}.png when the hard suite
has been run).

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
    'attribute_nav': 'Description-referenced\nnav (self-built memory)',
    'zone_nav': 'Known-zone nav\n(control)',
    'negative': 'Impossible goal\n(hallucination check)',
}
PHRASING_LABELS = {
    'es_imperative': 'Spanish\nimperative',
    'es_paraphrase': 'Spanish\nparaphrase',
    'en': 'English',
}


def load(suite, cond):
    """Loads one condition's run records (e.g. results/full/rag.json), [] if absent."""
    path = RESULTS / suite / f'{cond}.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else []


def style_axes(ax):
    """Applies the shared chart style: surface fill, recessive spines and grid."""
    ax.set_facecolor(SURFACE)
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    for side in ('left', 'bottom'):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=INK_2, labelcolor=INK_2, length=0)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def grouped_bars(ax, categories, series, ylabel, ymax=1.12, as_pct=True, counts=None):
    """series = [(label, color, values)] — thin grouped bars, direct labels.

    counts: optional per-category sample size, appended to the tick label. A
    rate of 100% carries very different weight at n=3 than at n=9, so the
    reader should never have to leave the chart to find out which one it is.
    It also exposes empty cells (n=0), which would otherwise render as an
    indistinguishable 0%.
    """
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
    labels = list(categories)
    if counts is not None:
        labels = [f'{c}\nn={k} per condition' for c, k in zip(labels, counts, strict=True)]
    ax.set_xticks(range(n))
    ax.set_xticklabels(labels, fontsize=9, color=INK)
    ax.set_ylim(0, ymax)
    if as_pct:
        ax.set_yticks([0, 0.25, 0.5, 0.75, 1.0])
        ax.set_yticklabels(['0%', '25%', '50%', '75%', '100%'], fontsize=9)
    ax.set_ylabel(ylabel, fontsize=10, color=INK_2)
    style_axes(ax)


def success_rate(runs, ttype):
    """Share of successful runs of one task type (0.0 when it has none)."""
    subset = [r for r in runs if r.get('type', 'object_nav') == ttype]
    return sum(r['success'] for r in subset) / len(subset) if subset else 0.0


def sample_size(runs, ttype):
    """Number of runs of a given task type — annotated on the chart (see grouped_bars)."""
    return len([r for r in runs if r.get('type', 'object_nav') == ttype])


def fig_benchmark():
    """Renders results/benchmark.png: success by task type and planning latency."""
    rag, norag = load('full', 'rag'), load('full', 'norag')
    types = ['object_nav', 'attribute_nav', 'zone_nav', 'negative']
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2), facecolor=SURFACE,
                                   gridspec_kw={'width_ratios': [1.6, 1]})
    grouped_bars(
        ax1, [TYPE_LABELS[t] for t in types],
        [('With RAG', BLUE, [success_rate(rag, t) for t in types]),
         ('Without RAG', GREEN, [success_rate(norag, t) for t in types])],
        ylabel='Success rate',
        counts=[sample_size(rag, t) for t in types],
    )
    total_runs = len(rag) + len(norag)
    ax1.set_title(f'Planning success by task type ({total_runs} runs, temperature 0)',
                  fontsize=11, color=INK, pad=26)
    ax1.legend(frameon=False, fontsize=9, ncol=2, loc='lower left',
               bbox_to_anchor=(0.0, 1.0), labelcolor=INK)

    # Latency: mean bar + individual run dots per condition. Runs that never
    # produced a plan (Ollama stall -> 60 s timeout) are excluded: they
    # measure the timeout constant, not planning latency.
    for i, (_label, color, runs) in enumerate(
            [('With RAG', BLUE, rag), ('Without RAG', GREEN, norag)]):
        lat = [
            r['latency_sec'] for r in runs
            if 'latency_sec' in r and r.get('decision') != 'no_plan'
        ]
        if not lat:
            continue
        mean = sum(lat) / len(lat)
        ax2.bar([i], [mean], width=0.42, color=color, edgecolor=SURFACE,
                linewidth=2, zorder=3)
        # Dots sit just off the bar's right edge (half-width 0.21) so they read
        # as that bar's runs without the fill swallowing them.
        ax2.plot([i + 0.28] * len(lat), lat, 'o', color=color, markersize=4,
                 alpha=0.55, zorder=4)
        ax2.annotate(f'{mean:.1f}s', (i, mean), textcoords='offset points',
                     xytext=(0, 4), ha='center', fontsize=9, color=INK)
    # Explicit limits: with autoscaling the rightmost dot column lands on the
    # axis edge and reads as clipped.
    ax2.set_xlim(-0.55, 1.55)
    ax2.set_xticks([0, 1])
    ax2.set_xticklabels(
        [f'With RAG\nn={len(rag)}', f'Without RAG\nn={len(norag)}'], fontsize=9, color=INK,
    )
    ax2.set_ylabel('Planning latency (s)', fontsize=10, color=INK_2)
    ax2.set_title('Goal → plan latency\n(bar = mean, dots = runs)',
                  fontsize=11, color=INK, pad=8)
    style_axes(ax2)

    fig.tight_layout()
    fig.savefig(RESULTS / 'benchmark.png', dpi=200, facecolor=SURFACE,
                bbox_inches='tight')
    print('Wrote', RESULTS / 'benchmark.png')


def phrasing_group(task_id):
    """Maps a phrasing task id to its phrasing group, e.g. a_es_imperative -> es_imperative."""
    return task_id.split('_', 1)[1]  # a_es_imperative -> es_imperative


def fig_phrasing():
    """Renders results/phrasing.png: direct-nav success per phrasing and language."""
    rag, norag = load('phrasing', 'rag'), load('phrasing', 'norag')
    if not rag:
        print('No phrasing results yet, skipping phrasing.png')
        return
    groups = ['es_imperative', 'es_paraphrase', 'en']

    def subset_of(runs, group):
        return [r for r in runs if phrasing_group(r['task_id']) == group]

    def rate(runs, group):
        subset = subset_of(runs, group)
        return sum(r['success'] for r in subset) / len(subset) if subset else 0.0

    fig, ax = plt.subplots(figsize=(7.6, 4.4), facecolor=SURFACE)
    grouped_bars(
        ax, [PHRASING_LABELS[g] for g in groups],
        [('With RAG', BLUE, [rate(rag, g) for g in groups]),
         ('Without RAG', GREEN, [rate(norag, g) for g in groups])],
        ylabel='Direct-nav success rate',
        counts=[len(subset_of(rag, g)) for g in groups],
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
    """Renders results/embeddings.png: top-1 accuracy and margin per embedding model."""
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
        counts=[len(data['models'][series_defs[0][0]][lang]['runs']) for lang in langs],
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
    # Zero line above the bars (zorder 5): drawn underneath it shows only in the
    # gaps between bars and reads as a row of stray dashes rather than a datum.
    ax2.axhline(0, color=INK_2, linewidth=0.8, zorder=5)
    ax2.set_xticks(range(len(langs)))
    ax2.set_xticklabels(
        [f'{label}\nn={len(data["models"][series_defs[0][0]][lang]["runs"])}'
         for label, lang in zip(lang_labels, langs, strict=True)],
        fontsize=9, color=INK,
    )
    ax2.set_ylabel('Mean margin (correct − best wrong)', fontsize=10, color=INK_2)
    ax2.set_title('Retrieval margin by query language\n(higher = more separable)',
                  fontsize=11, color=INK, pad=8)
    style_axes(ax2)

    fig.tight_layout()
    fig.savefig(RESULTS / 'embeddings.png', dpi=200, facecolor=SURFACE,
                bbox_inches='tight')
    print('Wrote', RESULTS / 'embeddings.png')



def fig_room_semantics():
    """Renders the zone-description comparison, if the bench has been run.

    Two things have to be visible at once: that describing a zone by what the
    room is *for* retrieves the right room more often, and that several of the
    old descriptions did not merely rank lower — they fell under the planner's
    relevance threshold and never entered the prompt at all. Hence the
    threshold as a drawn line rather than a number in the caption, and the
    wrong room named next to each query the old text got wrong.
    """
    path = RESULTS / 'room_semantics.json'
    if not path.exists():
        print('No room_semantics.json yet, skipping room_semantics.png '
              '(run: room_semantics_bench.py)')
        return
    data = json.loads(path.read_text(encoding='utf-8'))
    plain, rich = data['styles']['name_and_bounds'], data['styles']['functional']
    threshold = data['score_threshold']
    n = data['queries']

    fig, (ax1, ax2) = plt.subplots(
        1, 2, figsize=(12, 5.0), facecolor=SURFACE, gridspec_kw={'width_ratios': [1, 1.5]},
    )
    grouped_bars(
        ax1, ['Right zone\nranked first', f'Right zone above\nthe {threshold:.2f} threshold'],
        [('Name + what the room is for', BLUE,
          [rich['top1_acc'], rich['above_threshold'] / n]),
         ('Name + bounds (before)', GREEN,
          [plain['top1_acc'], plain['above_threshold'] / n])],
        ylabel='Share of functional queries',
        counts=[n, n],
    )
    # A two-row legend needs more clearance than the one-row ones elsewhere in
    # this file, or the title lands on top of it.
    ax1.set_title('Asking for a room by what happens in it',
                  fontsize=11, color=INK, pad=48)
    ax1.legend(frameon=False, fontsize=9, ncol=1, loc='lower left',
               bbox_to_anchor=(0.0, 1.0), labelcolor=INK)

    # Per-query score of the *correct* zone, so a bar that falls left of the
    # threshold line is a query the planner would have answered with no context.
    height = 0.36
    positions = range(n)
    # Colours carry over from ax1's legend, as in fig_embeddings — one legend
    # per figure, not per axes.
    for i, (style, color) in enumerate([(rich, BLUE), (plain, GREEN)]):
        ys = [y + (0.5 - i) * height for y in positions]
        ax2.barh(ys, [r['correct_score'] for r in style['runs']], height=height,
                 color=color, edgecolor=SURFACE, linewidth=1.5, zorder=3)
    # Name the room the old description actually retrieved — the failure is
    # more instructive than the average. Anchored on the "before" bar (the
    # upper one of each pair), past its end, where nothing else is drawn.
    for y, run in zip(positions, plain['runs'], strict=True):
        if not run['top1']:
            ax2.annotate(f'→ {run["retrieved"]}', (run['correct_score'], y - 0.5 * height),
                         textcoords='offset points', xytext=(5, -3),
                         fontsize=8, color=INK_2)
    ax2.axvline(threshold, color=INK_2, linewidth=0.9, linestyle='--', zorder=5)
    ax2.annotate(f'planner threshold ({threshold:.2f})', (threshold, -0.85),
                 textcoords='offset points', xytext=(4, 0), fontsize=8, color=INK_2)
    ax2.set_yticks(list(positions))
    ax2.set_yticklabels([r['query'] for r in plain['runs']], fontsize=8, color=INK)
    ax2.invert_yaxis()
    ax2.set_xlim(0, 0.78)
    ax2.set_xlabel('Similarity to the correct zone', fontsize=10, color=INK_2)
    ax2.set_title('Per query — upper bar: name + bounds, lower: with purpose\n'
                  '(arrow = the room the old description actually retrieved)',
                  fontsize=11, color=INK, pad=14)
    style_axes(ax2)
    ax2.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax2.yaxis.grid(False)

    fig.tight_layout()
    fig.savefig(RESULTS / 'room_semantics.png', dpi=200, facecolor=SURFACE,
                bbox_inches='tight')
    print('Wrote', RESULTS / 'room_semantics.png')


HARD_LABELS = {
    'distractor_nav': 'Disambiguation\n(confusable neighbour)',
    'ordered_multi_step': 'Ordered\nmulti-step plan',
    'relational_nav': 'Spatial relation\n("nearest to base")',
    'negative_plausible': 'Plausible\nnonexistent place',
}


def fig_hard():
    """Renders the hard suite, if it has been run.

    Unlike the main suite this one is expected to show partial scores — that is
    the whole point of it (see tasks_hard.yaml). Skipped silently until results
    exist, so no chart is ever produced from absent data.
    """
    rag, norag = load('hard', 'rag'), load('hard', 'norag')
    if not rag:
        print('No hard-suite results yet, skipping hard.png '
              '(run: seed_memory.py --hard, then run_benchmark.py tasks_hard.yaml)')
        return
    types = [t for t in HARD_LABELS if sample_size(rag, t)]

    fig, ax = plt.subplots(figsize=(8.6, 4.6), facecolor=SURFACE)
    grouped_bars(
        ax, [HARD_LABELS[t] for t in types],
        [('With RAG', BLUE, [success_rate(rag, t) for t in types]),
         ('Without RAG', GREEN, [success_rate(norag, t) for t in types])],
        ylabel='Success rate',
        counts=[sample_size(rag, t) for t in types],
    )
    total = len(rag) + len(norag)
    ax.set_title(f'Hard suite — tasks with headroom ({total} runs, temperature 0)',
                 fontsize=11, color=INK, pad=26)
    ax.legend(frameon=False, fontsize=9, ncol=2, loc='lower left',
              bbox_to_anchor=(0.0, 1.0), labelcolor=INK)
    fig.tight_layout()
    fig.savefig(RESULTS / 'hard.png', dpi=200, facecolor=SURFACE, bbox_inches='tight')
    print('Wrote', RESULTS / 'hard.png')


# Decision labels that mean the task was solved (everything else is a failure
# mode). Kept in sync with scoring.classify's success returns.
_HARD_SUCCESS_DECISIONS = {'direct_nav', 'ordered_plan', 'declined_or_explore', 'zone_nav'}


def fig_hard_decisions():
    """Breaks the hard suite's RAG condition down by decision label.

    A success rate alone says the planner missed; the decision labels say how —
    the wrong landmark, hedging across both candidates, right steps in the wrong
    order. Successes (blue) and failures (green) are coloured distinctly so the
    chart is a breakdown of outcomes, not just of failures. That "how" is what
    makes the suite useful for guiding the next iteration.
    """
    rag = load('hard', 'rag')
    if not rag:
        return
    counts = defaultdict(int)
    for run in rag:
        counts[run['decision']] += 1
    if not counts:
        return
    # Failures first (they are the point), then successes; each block by size.
    ordered = sorted(
        counts.items(),
        key=lambda kv: (kv[0] in _HARD_SUCCESS_DECISIONS, -kv[1]),
    )
    labels = [k.replace('_', ' ') for k, _ in ordered]
    values = [v for _, v in ordered]
    colors = [BLUE if k in _HARD_SUCCESS_DECISIONS else GREEN for k, _ in ordered]

    fig, ax = plt.subplots(figsize=(8.0, 0.42 * len(labels) + 2.1), facecolor=SURFACE)
    positions = range(len(labels))
    ax.barh(list(positions), values, height=0.55, color=colors,
            edgecolor=SURFACE, linewidth=2, zorder=3)
    for y, v in zip(positions, values, strict=True):
        ax.annotate(f'{v}', (v, y), textcoords='offset points', xytext=(5, 0),
                    va='center', fontsize=9, color=INK)
    ax.set_yticks(list(positions))
    ax.set_yticklabels(labels, fontsize=9, color=INK)
    ax.invert_yaxis()
    ax.set_xlabel('Runs (with RAG, 30 total)', fontsize=10, color=INK_2)
    ax.set_xlim(0, max(values) * 1.15)
    ax.set_title('Hard suite — outcome breakdown with RAG\n'
                 '(green = failure modes, blue = solved)',
                 fontsize=11, color=INK, pad=10)
    style_axes(ax)
    ax.xaxis.grid(True, color=GRID, linewidth=0.8)
    ax.yaxis.grid(False)
    fig.tight_layout()
    fig.savefig(RESULTS / 'hard_decisions.png', dpi=200, facecolor=SURFACE,
                bbox_inches='tight')
    print('Wrote', RESULTS / 'hard_decisions.png')


def main():
    fig_benchmark()
    fig_phrasing()
    fig_embeddings()
    fig_room_semantics()
    fig_hard()
    fig_hard_decisions()


if __name__ == '__main__':
    main()
