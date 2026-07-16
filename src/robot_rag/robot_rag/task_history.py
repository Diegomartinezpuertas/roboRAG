"""Loads completed-task logs into the "task_history" ChromaDB collection."""

import json
from pathlib import Path

from robot_rag.chroma_manager import ChromaManager
from robot_rag.embedder import OllamaEmbedder

COLLECTION_NAME = 'task_history'


class TaskHistoryStore:
    """Ingests report_skill's JSON task logs so past tasks are RAG-queryable.

    Args:
        chroma_manager: Shared ChromaDB manager instance.
        embedder: Shared text embedder instance.
        logs_dir: Directory containing report_skill's `<task_id>.json` logs.
    """

    def __init__(
        self, chroma_manager: ChromaManager, embedder: OllamaEmbedder, logs_dir: str,
    ) -> None:
        self._chroma = chroma_manager
        self._embedder = embedder
        self._logs_dir = Path(logs_dir)

    def ingest(self) -> int:
        """Re-scans the logs directory and upserts every task log found.

        Unlike KnowledgeBase.ingest(), this always re-scans (not skipped when
        already populated): task logs accumulate over time, and IDs are
        derived from filenames, so upserting is idempotent and picks up
        tasks completed since the last rag_node start.

        Returns:
            Number of task logs ingested.
        """
        if not self._logs_dir.is_dir():
            return 0

        documents: list[str] = []
        metadatas: list[dict] = []
        ids: list[str] = []
        for file_path in sorted(self._logs_dir.glob('*.json')):
            try:
                entry = json.loads(file_path.read_text(encoding='utf-8'))
            except (OSError, json.JSONDecodeError):
                continue
            goal_text = entry.get('goal_text', '')
            response = entry.get('response', '')
            if not goal_text and not response:
                continue
            documents.append(f'Goal: {goal_text}\nOutcome: {response}')
            metadatas.append({'task_id': entry.get('task_id', file_path.stem)})
            ids.append(f'task-{file_path.stem}')

        if not documents:
            return 0

        embeddings = self._embedder.embed_batch(documents)
        self._chroma.add(COLLECTION_NAME, documents, embeddings, metadatas, ids)
        return len(documents)
