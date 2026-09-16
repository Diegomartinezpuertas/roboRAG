"""Semantic map of the environment, backed by the "semantic_map" ChromaDB collection."""

from __future__ import annotations

from typing import TYPE_CHECKING

from robot_rag.chroma_manager import ChromaManager
from robot_rag.embedder import OllamaEmbedder
from robot_rag.scene_merge import DEFAULT_MERGE_RADIUS_M, find_merge_target, merged_observations

if TYPE_CHECKING:
    # Only a type hint; guarding it keeps SemanticMap importable — and its
    # merge-on-write testable against a real ChromaDB — without ROS (ADR-018).
    from robot_interfaces.msg import SemanticObject

COLLECTION_NAME = 'semantic_map'

# Candidates scanned when looking for a memory to merge into. One map session
# holds tens of memories per look, not hundreds; the cap only bounds the scan.
MERGE_CANDIDATE_LIMIT = 500


class SemanticMap:
    """Stores and retrieves remembered places, indexed by description and pose.

    What lands here: scene descriptions the robot records while exploring
    (ADR-014), user-defined zones (ADR-022), and the benchmark's seeded
    landmarks. No object detector feeds it.

    Args:
        chroma_manager: Shared ChromaDB manager instance.
        embedder: Shared text embedder instance.
        merge_radius: Radius in meters within which a re-observation of the
            same look (same group key, map session and zone) is folded into the
            existing memory instead of stored again. <= 0 disables merging.
    """

    def __init__(
        self, chroma_manager: ChromaManager, embedder: OllamaEmbedder,
        merge_radius: float = DEFAULT_MERGE_RADIUS_M,
    ) -> None:
        self._chroma = chroma_manager
        self._embedder = embedder
        self._merge_radius = merge_radius

    def upsert_object(self, obj: SemanticObject, map_id: str = '') -> tuple[str, bool]:
        """Inserts or updates one place observation in the semantic map.

        The stored document embeds the map-frame coordinates in the text
        itself, because QueryRAG returns only document text (not metadata) —
        so this is the only channel through which the planner can learn where
        a remembered place is and navigate to it (ADR-012).

        Args:
            obj: Semantic object observation to store.
            map_id: Active map-session id, tagged into metadata so retrieval
                can scope this coordinate memory to the map it belongs to
                (ADR-019). Empty means "untagged" — retrievable only by an
                untagged/legacy query.

        Returns:
            Tuple of (object_id the memory is stored under, merged). When the
            object carries a group_key and an existing memory of the same key,
            map session and zone lies within the merge radius, the observation
            is folded into it: that memory's id and anchor pose are kept, its
            description and timestamp refreshed, its observation count raised
            (ADR-025).
        """
        object_id = obj.object_id
        pose_x, pose_y = obj.pose.position.x, obj.pose.position.y
        observations = 1
        merged = False
        if obj.group_key:
            candidates = self._chroma.list_documents(
                COLLECTION_NAME, MERGE_CANDIDATE_LIMIT,
                where={'$and': [
                    {'group_key': obj.group_key},
                    {'map_id': map_id},
                    {'room_zone': obj.room_zone},
                ]},
            )
            target = find_merge_target(candidates, pose_x, pose_y, self._merge_radius)
            if target is not None:
                object_id = target['id']
                pose_x = target['metadata']['pose_x']
                pose_y = target['metadata']['pose_y']
                observations = merged_observations(target['metadata'])
                merged = True

        zone = obj.room_zone or 'unknown area'
        location = f'at (x={pose_x:.2f}, y={pose_y:.2f}) in {zone}'
        document = (
            f'{obj.label} {location}: {obj.description}'
            if obj.description
            else f'{obj.label} {location}'
        )
        embedding = self._embedder.embed_text(document)
        metadata = {
            'object_id': object_id,
            'label': obj.label,
            'confidence': float(obj.confidence),
            'room_zone': obj.room_zone,
            'pose_x': pose_x,
            'pose_y': pose_y,
            'map_id': map_id,
            'group_key': obj.group_key,
            'observations': observations,
        }
        self._chroma.add(
            COLLECTION_NAME,
            documents=[document],
            embeddings=[embedding],
            metadatas=[metadata],
            ids=[object_id],
        )
        return object_id, merged
