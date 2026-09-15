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
        # Kept so stats() can report every collection the robot serves,
        # including the empty ones — "0 memories here" is information too.
        self._collection_names = list(collections)
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
        hits = self.query_documents(collection_name, query_embedding, top_k, where=where)
        return [hit['document'] for hit in hits], [hit['score'] for hit in hits]

    def query_documents(
        self, collection_name: str, query_embedding: list[float], top_k: int,
        where: dict | None = None,
    ) -> list[dict]:
        """Retrieves the most similar entries with their ids, metadata and scores.

        The richer sibling of `query`: the planner only ever consumes text and
        a score, but the memory inspector (`/rag/inspect`) also shows *which*
        entry a document is and what metadata it carries — the coordinates, the
        map session, the source file.

        Args:
            collection_name: Target collection name.
            query_embedding: Embedding vector of the query.
            top_k: Number of results to return.
            where: Optional ChromaDB metadata filter, e.g. {'map_id': 'abc'}.

        Returns:
            List of {'id', 'document', 'metadata', 'score'} dicts ordered by
            decreasing relevance; scores are cosine similarities in [0.0, 1.0].

        Raises:
            ValueError: If the collection does not exist.
        """
        collection = self._get_collection(collection_name)
        count = collection.count()
        if count == 0:
            return []
        # n_results is capped at the collection count, not the filtered count
        # (ChromaDB has no cheap filtered-count); it returns as many matches as
        # exist up to that cap, which is always enough.
        result = collection.query(
            query_embeddings=[query_embedding], n_results=min(top_k, count),
            where=where or None,
            include=['documents', 'metadatas', 'distances'],
        )
        return [
            {
                'id': entry_id,
                'document': document,
                'metadata': dict(metadata or {}),
                # Cosine distance in ChromaDB is 1 - cosine_similarity.
                'score': max(0.0, min(1.0, 1.0 - distance)),
            }
            for entry_id, document, metadata, distance in zip(
                result['ids'][0], result['documents'][0],
                result['metadatas'][0], result['distances'][0], strict=False,
            )
        ]

    def list_documents(
        self, collection_name: str, limit: int, where: dict | None = None,
    ) -> list[dict]:
        """Lists stored entries as they are, without embedding anything.

        Browsing is not searching: the inspector opens on a collection before
        the user has typed a query, and paying an embedding round-trip just to
        show what is in there would be wasteful (and would fail whenever Ollama
        is down, which is exactly when someone wants to look).

        Args:
            collection_name: Target collection name.
            limit: Maximum number of entries to return.
            where: Optional ChromaDB metadata filter, e.g. {'map_id': 'abc'}.

        Returns:
            List of {'id', 'document', 'metadata'} dicts, in ChromaDB's
            insertion order.

        Raises:
            ValueError: If the collection does not exist.
        """
        collection = self._get_collection(collection_name)
        result = collection.get(
            limit=max(1, limit), where=where or None,
            include=['documents', 'metadatas'],
        )
        return [
            {'id': entry_id, 'document': document, 'metadata': dict(metadata or {})}
            for entry_id, document, metadata in zip(
                result['ids'], result['documents'], result['metadatas'], strict=False,
            )
        ]

    def count(self, collection_name: str) -> int:
        """Returns the number of documents stored in a collection."""
        return self._get_collection(collection_name).count()

    def stats(self) -> dict[str, int]:
        """Returns {collection_name: document_count} for every managed collection."""
        return {name: self.count(name) for name in self._collection_names}

    def _get_collection(self, collection_name: str):
        try:
            return self._client.get_collection(collection_name)
        except Exception as exc:
            raise ValueError(f'Unknown collection: {collection_name}') from exc
