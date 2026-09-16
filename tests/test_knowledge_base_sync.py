"""Knowledge base follows the Markdown on disk (ADR-031).

Runs the real ingestion path against a real ChromaDB with a fake embedder, so no
ROS and no Ollama are needed.
"""

import pytest

from robot_rag.chroma_manager import ChromaManager
from robot_rag.knowledge_base import COLLECTION_NAME, KnowledgeBase

TEMPLATES = (
    '## Template: explore an area\n\nPlan: explore.\n\n'
    '## Template: go by description\n\nPlan: navigate. Do not explore.\n'
)


class FakeEmbedder:
    def __init__(self):
        self.calls = 0

    def embed_batch(self, texts):
        self.calls += 1
        return [[1.0, 0.0] for _ in texts]


class DownEmbedder:
    def embed_batch(self, texts):
        raise ConnectionError('Ollama is not running')


@pytest.fixture
def workspace(tmp_path):
    knowledge = tmp_path / 'knowledge'
    knowledge.mkdir()
    (knowledge / 'task_templates.md').write_text(TEMPLATES, encoding='utf-8')
    chroma = ChromaManager(str(tmp_path / 'chroma'), [COLLECTION_NAME])
    return knowledge, chroma


def stored_texts(chroma):
    return {e['id']: e['document'] for e in chroma.list_documents(COLLECTION_NAME, 100)}


def test_first_start_ingests_every_chunk(workspace):
    knowledge, chroma = workspace
    assert KnowledgeBase(chroma, FakeEmbedder(), str(knowledge)).ingest() == 2
    assert set(stored_texts(chroma)) == {'task_templates-0', 'task_templates-1'}


def test_an_unchanged_knowledge_base_is_not_re_embedded(workspace):
    knowledge, chroma = workspace
    KnowledgeBase(chroma, FakeEmbedder(), str(knowledge)).ingest()
    embedder = FakeEmbedder()
    assert KnowledgeBase(chroma, embedder, str(knowledge)).ingest() == 0
    assert embedder.calls == 0


def test_a_removed_template_stops_being_retrievable_without_wiping_the_store(workspace):
    # The regression that motivated this: a template deleted from the files kept
    # being retrieved, because a populated collection was never re-ingested.
    knowledge, chroma = workspace
    KnowledgeBase(chroma, FakeEmbedder(), str(knowledge)).ingest()
    (knowledge / 'task_templates.md').write_text(
        '## Template: explore an area\n\nPlan: explore.\n', encoding='utf-8',
    )
    assert KnowledgeBase(chroma, FakeEmbedder(), str(knowledge)).ingest() == 1
    texts = stored_texts(chroma)
    assert list(texts) == ['task_templates-0']
    assert not any('Do not explore' in text for text in texts.values())


def test_an_edited_chunk_replaces_the_stored_text(workspace):
    knowledge, chroma = workspace
    KnowledgeBase(chroma, FakeEmbedder(), str(knowledge)).ingest()
    (knowledge / 'task_templates.md').write_text(
        TEMPLATES.replace('Do not explore.', 'Explore if it is not there.'), encoding='utf-8',
    )
    assert KnowledgeBase(chroma, FakeEmbedder(), str(knowledge)).ingest() == 2
    assert 'Explore if it is not there.' in stored_texts(chroma)['task_templates-1']


def test_an_embedder_failure_keeps_the_previous_knowledge_base(workspace):
    knowledge, chroma = workspace
    KnowledgeBase(chroma, FakeEmbedder(), str(knowledge)).ingest()
    before = stored_texts(chroma)
    (knowledge / 'task_templates.md').write_text('## Changed\n\ntext\n', encoding='utf-8')
    with pytest.raises(ConnectionError):
        KnowledgeBase(chroma, DownEmbedder(), str(knowledge)).ingest()
    assert stored_texts(chroma) == before


def test_a_missing_or_empty_knowledge_dir_leaves_the_store_alone(workspace, tmp_path):
    knowledge, chroma = workspace
    KnowledgeBase(chroma, FakeEmbedder(), str(knowledge)).ingest()
    before = stored_texts(chroma)
    empty = tmp_path / 'empty'
    empty.mkdir()
    assert KnowledgeBase(chroma, FakeEmbedder(), str(tmp_path / 'missing')).ingest() == 0
    assert KnowledgeBase(chroma, FakeEmbedder(), str(empty)).ingest() == 0
    assert stored_texts(chroma) == before
