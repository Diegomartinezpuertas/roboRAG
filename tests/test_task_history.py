"""Tests for ingesting completed-task JSON logs into task_history."""

import json

from robot_rag.chroma_manager import ChromaManager
from robot_rag.task_history import TaskHistoryStore


class FakeEmbedder:
    """Deterministic stand-in for the Ollama embedder (no server needed)."""

    def embed_text(self, text):
        return [0.1] * 8

    def embed_batch(self, texts):
        return [[0.1] * 8 for _ in texts]


def test_ingest_reads_valid_logs_and_skips_bad(tmp_path):
    logs = tmp_path / 'logs'
    logs.mkdir()
    (logs / 't1.json').write_text(
        json.dumps({'task_id': 't1', 'goal_text': 've a casa', 'response': 'llegue'}),
    )
    (logs / 't2.json').write_text(
        json.dumps({'task_id': 't2', 'goal_text': 'explora', 'response': 'hecho'}),
    )
    (logs / 'broken.json').write_text('{ not valid json')

    chroma = ChromaManager(str(tmp_path / 'chroma'), ['task_history'])
    store = TaskHistoryStore(chroma, FakeEmbedder(), str(logs))

    assert store.ingest() == 2               # broken.json skipped
    assert chroma.count('task_history') == 2
    assert store.ingest() == 2               # re-scan is idempotent (upsert by id)
    assert chroma.count('task_history') == 2


def test_ingest_missing_dir_returns_zero(tmp_path):
    chroma = ChromaManager(str(tmp_path / 'chroma'), ['task_history'])
    store = TaskHistoryStore(chroma, FakeEmbedder(), str(tmp_path / 'nope'))
    assert store.ingest() == 0
