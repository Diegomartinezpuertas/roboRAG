"""Embedding-model comparison for the RAG memory: nomic-embed-text vs bge-m3.

Motivation: the knowledge base and stored object descriptions are English,
while user goals are often Spanish. Retrieval quality then depends on the
embedding model's cross-lingual alignment. nomic-embed-text is
English-centric; bge-m3 is trained multilingual. This script measures the
difference on a corpus that mirrors what the robot actually stores.

For each (model, query language) it reports:
  - top-1 accuracy: the expected document ranks first
  - mean correct-document similarity
  - mean margin: correct score minus the best wrong score (negative = the
    wrong document wins)

No ROS required. Requires the models pulled in Ollama:
    ollama pull nomic-embed-text && ollama pull bge-m3

Usage: python3 embedding_bench.py    -> writes results/embeddings.json + prints a table
"""

import json
import statistics
from pathlib import Path

import numpy as np
import ollama

RESULTS_FILE = Path(__file__).resolve().parent / 'results' / 'embeddings.json'
MODELS = ['nomic-embed-text', 'bge-m3']

# Documents as the robot actually stores them (semantic_map entries embed
# coordinates in the text; knowledge_base chunks keep header + body).
DOCS = {
    'estacion_a': (
        'estacion_a at (x=-1.79, y=-0.44) in unknown area: '
        'estacion_a, a named location in the house'
    ),
    'estacion_b': (
        'estacion_b at (x=1.31, y=-0.09) in unknown area: '
        'estacion_b, a named location in the house'
    ),
    'fridge': 'refrigerator at (x=3.42, y=-1.15) in kitchen: a white fridge',
    'mailbox': 'mailbox at (x=-0.74, y=-0.86) in corridor: a black mailbox on a pole',
    'chair': 'chair at (x=2.10, y=1.40) in kitchen: a wooden chair near the table',
    'safety': (
        '## Safety rules\n\nThe robot must stop and report if continuous operation exceeds '
        '30 minutes of simulation time. The robot must never attempt to navigate outside '
        'the mapped boundaries produced by SLAM Toolbox.'
    ),
    'nav_rules': (
        '## Navigation rules\n\nThe robot must never cross into an area without a clear '
        'LIDAR-confirmed path. If Nav2 reports a blocked or out-of-bounds goal, the robot '
        'should fall back to frontier exploration instead of retrying the same goal repeatedly.'
    ),
    'catalog': (
        '## Common object-to-room associations\n\nKitchen typically contains: refrigerator, '
        'stove, sink, table, chair. Living room typically contains: sofa, television, '
        'bookshelf, lamp, plant. Bedroom typically contains: bed, lamp, bookshelf, window.'
    ),
}

# (query, expected doc key) per language.
QUERIES = {
    'es': [
        ('¿Dónde está la nevera?', 'fridge'),
        ('Ve al buzón', 'mailbox'),
        ('Busca una silla', 'chair'),
        ('¿Cuáles son las reglas de seguridad del robot?', 'safety'),
        ('¿Qué haces si Nav2 no encuentra camino?', 'nav_rules'),
        ('Llévame a estacion_b', 'estacion_b'),
        ('¿Qué objetos suele haber en la cocina?', 'catalog'),
    ],
    'en': [
        ('Where is the fridge?', 'fridge'),
        ('Go to the mailbox', 'mailbox'),
        ('Find a chair', 'chair'),
        ('What are the robot safety rules?', 'safety'),
        ('What do you do if Nav2 cannot find a path?', 'nav_rules'),
        ('Take me to estacion_b', 'estacion_b'),
        ('What objects are typically in the kitchen?', 'catalog'),
    ],
}


def cosine(a, b):
    a, b = np.asarray(a), np.asarray(b)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


def main():
    client = ollama.Client(host='http://localhost:11434')
    doc_keys = list(DOCS)
    results = {'docs': doc_keys, 'models': {}}

    for model in MODELS:
        doc_vecs = client.embed(model=model, input=[DOCS[k] for k in doc_keys])['embeddings']
        model_out = {}
        for lang, pairs in QUERIES.items():
            rows = []
            for query, expected in pairs:
                q_vec = client.embed(model=model, input=query)['embeddings'][0]
                scores = {k: cosine(q_vec, v) for k, v in zip(doc_keys, doc_vecs, strict=True)}
                ranked = sorted(scores, key=scores.get, reverse=True)
                correct = scores[expected]
                best_wrong = max(s for k, s in scores.items() if k != expected)
                rows.append({
                    'query': query,
                    'expected': expected,
                    'top1': ranked[0] == expected,
                    'correct_score': round(correct, 4),
                    'margin': round(correct - best_wrong, 4),
                    'top3': ranked[:3],
                })
            model_out[lang] = {
                'runs': rows,
                'top1_acc': sum(r['top1'] for r in rows) / len(rows),
                'mean_correct_score': round(statistics.mean(r['correct_score'] for r in rows), 4),
                'mean_margin': round(statistics.mean(r['margin'] for r in rows), 4),
            }
        results['models'][model] = model_out

    RESULTS_FILE.parent.mkdir(exist_ok=True)
    RESULTS_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding='utf-8')

    print(f'{"model":18} {"lang":4} {"top-1":>6} {"score":>7} {"margin":>7}')
    for model, langs in results['models'].items():
        for lang, s in langs.items():
            print(
                f'{model:18} {lang:4} {s["top1_acc"] * 100:5.0f}% '
                f'{s["mean_correct_score"]:7.3f} {s["mean_margin"]:+7.3f}',
            )
    print(f'\nWrote {RESULTS_FILE}')


if __name__ == '__main__':
    main()
