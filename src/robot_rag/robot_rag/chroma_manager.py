"""CRUD operations over ChromaDB collections used by the robot's RAG memory."""

import uuid

import chromadb


class ChromaManager:
    """Manages persistent ChromaDB collections for the robot's semantic memory.

    Args:
        db_path: Filesystem path where ChromaDB persists its data.
        collections: Names of the collections to create on startup.
    """

    def __init__(self, db_path: str, collections: list[str]) -> None:
        self._client = chromadb.PersistentClient(path=db_path)
        for name in collections:
            self._client.get_or_create_collection(
                name, metadata={'hnsw:space': 'cosine'},
            )

    def add(
        self,
        collection_name: str,
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict],
        ids: list[str] | None = None,
    ) -> None:
        """Inserts or updates documents in a collection.

        Args:
            collection_name: Target collection name.
            documents: Raw text fragments to store.
            embeddings: Precomputed embedding vector for each document.
            metadatas: Metadata dict for each document.
            ids: Optional stable IDs. Random UUIDs are generated if omitted.

        Raises:
            ValueError: If the collection does not exist.
        """
        collection = self._get_collection(collection_name)
        if ids is None:
            ids = [str(uuid.uuid4()) for _ in documents]
        collection.upsert(
            ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas,
        )

    def query(
        self, collection_name: str, query_embedding: list[float], top_k: int,
        where: dict | None = None,
    ) -> tuple[list[str], list[float]]:
        """Retrieves the most similar documents to a query embedding.

        Args:
            collection_name: Target collection name.
            query_embedding: Embedding vector of the query.
            top_k: Number of results to return.
            where: Optional ChromaDB metadata filter, e.g. {'map_id': 'abc'}.
                Only documents whose metadata matches are considered — used to
                scope coordinate memories to the active map session (ADR-019).

        Returns:
            Tuple of (contexts, scores) where scores are cosine similarities
            in [0.0, 1.0], ordered by decreasing relevance.

        Raises:
            ValueError: If the collection does not exist.
        """
        collection = self._get_collection(collection_name)
        count = collection.count()
        if count == 0:
            return [], []
        # n_results is capped at the collection count, not the filtered count
        # (ChromaDB has no cheap filtered-count); it returns as many matches as
        # exist up to that cap, which is always enough.
        result = collection.query(
            query_embeddings=[query_embedding], n_results=min(top_k, count),
            where=where or None,
        )
        documents = result['documents'][0]
        distances = result['distances'][0]
        # Cosine distance in ChromaDB is 1 - cosine_similarity, so similarity = 1 - distance.
        scores = [max(0.0, min(1.0, 1.0 - distance)) for distance in distances]
        return documents, scores

    def count(self, collection_name: str) -> int:
        """Returns the number of documents stored in a collection."""
        return self._get_collection(collection_name).count()

    def _get_collection(self, collection_name: str):
        try:
            return self._client.get_collection(collection_name)
        except Exception as exc:
            raise ValueError(f'Unknown collection: {collection_name}') from exc
