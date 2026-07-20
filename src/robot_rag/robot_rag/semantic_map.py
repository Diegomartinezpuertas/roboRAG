"""Semantic map of the environment, backed by the "semantic_map" ChromaDB collection."""

from robot_interfaces.msg import SemanticObject

from robot_rag.chroma_manager import ChromaManager
from robot_rag.embedder import OllamaEmbedder

COLLECTION_NAME = 'semantic_map'


class SemanticMap:
    """Stores and retrieves detected objects indexed by description and pose.

    Args:
        chroma_manager: Shared ChromaDB manager instance.
        embedder: Shared text embedder instance.
    """

    def __init__(self, chroma_manager: ChromaManager, embedder: OllamaEmbedder) -> None:
        self._chroma = chroma_manager
        self._embedder = embedder

    def upsert_object(self, obj: SemanticObject, map_id: str = '') -> None:
        """Inserts or updates a detected object in the semantic map.

        The stored document embeds the map-frame coordinates in the text
        itself, because QueryRAG returns only document text (not metadata) —
        so this is the only channel through which the planner can learn where
        a remembered object is and navigate to it. See ADR-012 / Fase 0.1.

        Args:
            obj: Semantic object observation to store.
            map_id: Active map-session id, tagged into metadata so retrieval
                can scope this coordinate memory to the map it belongs to
                (ADR-019). Empty means "untagged" — retrievable only by an
                untagged/legacy query.
        """
        zone = obj.room_zone or 'unknown area'
        location = f'at (x={obj.pose.position.x:.2f}, y={obj.pose.position.y:.2f}) in {zone}'
        document = (
            f'{obj.label} {location}: {obj.description}'
            if obj.description
            else f'{obj.label} {location}'
        )
        embedding = self._embedder.embed_text(document)
        metadata = {
            'object_id': obj.object_id,
            'label': obj.label,
            'confidence': float(obj.confidence),
            'room_zone': obj.room_zone,
            'pose_x': obj.pose.position.x,
            'pose_y': obj.pose.position.y,
            'map_id': map_id,
        }
        self._chroma.add(
            COLLECTION_NAME,
            documents=[document],
            embeddings=[embedding],
            metadatas=[metadata],
            ids=[obj.object_id],
        )
