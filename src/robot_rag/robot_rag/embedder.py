"""Wrapper around Ollama's embedding API for the RAG pipeline."""

import ollama


class OllamaEmbedder:
    """Generates text embeddings using an Ollama-hosted embedding model.

    Args:
        base_url: Base URL of the Ollama server.
        model: Name of the embedding model, e.g. "nomic-embed-text".
    """

    def __init__(self, base_url: str, model: str) -> None:
        self._client = ollama.Client(host=base_url)
        self._model = model

    def embed_text(self, text: str) -> list[float]:
        """Embeds a single piece of text.

        Args:
            text: Text to embed.

        Returns:
            Embedding vector as a list of floats.
        """
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embeds a batch of texts in a single request.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embedding vectors, one per input text.
        """
        response = self._client.embed(model=self._model, input=texts)
        return list(response['embeddings'])
