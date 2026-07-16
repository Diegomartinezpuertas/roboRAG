"""Loads static Markdown documents into the "knowledge_base" ChromaDB collection."""

from pathlib import Path

from robot_rag.chroma_manager import ChromaManager
from robot_rag.embedder import OllamaEmbedder

COLLECTION_NAME = 'knowledge_base'


def chunk_markdown(text: str) -> list[str]:
    """Splits Markdown into section chunks, each header kept with its own body.

    A naive blank-line split treats "## Safety rules" and the paragraph below
    it as two separate chunks, so querying for "safety rules" could retrieve
    just the bare heading with no actual rule text. This groups each header
    with everything up to the next header instead.

    Args:
        text: Raw Markdown document content.

    Returns:
        List of non-empty section chunks, in document order.
    """
    lines = text.splitlines()
    sections: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith('#') and current:
            sections.append(current)
            current = []
        current.append(line)
    if current:
        sections.append(current)

    chunks = ['\n'.join(section).strip() for section in sections]
    return [chunk for chunk in chunks if chunk]


class KnowledgeBase:
    """Ingests static knowledge documents (rules, catalogs, templates) for RAG lookup.

    Args:
        chroma_manager: Shared ChromaDB manager instance.
        embedder: Shared text embedder instance.
        knowledge_dir: Directory containing Markdown documents to ingest.
    """

    def __init__(
        self, chroma_manager: ChromaManager, embedder: OllamaEmbedder, knowledge_dir: str,
    ) -> None:
        self._chroma = chroma_manager
        self._embedder = embedder
        self._knowledge_dir = Path(knowledge_dir)

    def ingest(self) -> int:
        """Chunks and embeds every Markdown file in the knowledge directory.

        Ingestion is skipped if the collection is already populated.

        Returns:
            Number of chunks ingested.
        """
        if self._chroma.count(COLLECTION_NAME) > 0:
            return 0
        if not self._knowledge_dir.is_dir():
            return 0

        documents: list[str] = []
        metadatas: list[dict] = []
        ids: list[str] = []
        for file_path in sorted(self._knowledge_dir.glob('*.md')):
            text = file_path.read_text(encoding='utf-8')
            for index, chunk in enumerate(chunk_markdown(text)):
                documents.append(chunk)
                metadatas.append({'source': file_path.name, 'chunk_index': index})
                ids.append(f'{file_path.stem}-{index}')

        if not documents:
            return 0

        embeddings = self._embedder.embed_batch(documents)
        self._chroma.add(COLLECTION_NAME, documents, embeddings, metadatas, ids)
        return len(documents)
