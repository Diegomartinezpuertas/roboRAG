"""ROS 2 node exposing the RAG query and semantic map update services."""

import rclpy
from rclpy.node import Node

from robot_interfaces.srv import QueryRAG, UpdateMap

from robot_rag.chroma_manager import ChromaManager
from robot_rag.embedder import OllamaEmbedder
from robot_rag.knowledge_base import KnowledgeBase
from robot_rag.semantic_map import SemanticMap
from robot_rag.task_history import TaskHistoryStore


class RagNode(Node):
    """ROS 2 node that exposes ChromaDB-backed semantic memory over services.

    Services (server):
        /rag/query (QueryRAG): Retrieve relevant context from a collection.
        /rag/update_map (UpdateMap): Insert or update a semantic object.

    Parameters:
        ollama_base_url (str): Ollama server URL. Default: http://localhost:11434
        embedding_model (str): Embedding model name. Default: nomic-embed-text
        chroma_db_path (str): Filesystem path for ChromaDB persistence.
        knowledge_dir (str): Directory with static knowledge Markdown docs.
        logs_dir (str): Directory with report_skill's task JSON logs.
        collections (list[str]): Collections to create on startup.
        top_k_default (int): Default number of results for queries. Default: 5
    """

    def __init__(self) -> None:
        super().__init__('rag_node')

        self.declare_parameter('ollama_base_url', 'http://localhost:11434')
        self.declare_parameter('embedding_model', 'nomic-embed-text')
        self.declare_parameter('chroma_db_path', '/home/diego/robot_ws/data/chroma_db')
        self.declare_parameter('knowledge_dir', '/home/diego/robot_ws/data/knowledge')
        self.declare_parameter('logs_dir', '/home/diego/robot_ws/data/logs')
        self.declare_parameter(
            'collections', ['semantic_map', 'knowledge_base', 'task_history'],
        )
        self.declare_parameter('top_k_default', 5)

        base_url = self.get_parameter('ollama_base_url').value
        embedding_model = self.get_parameter('embedding_model').value
        chroma_db_path = self.get_parameter('chroma_db_path').value
        knowledge_dir = self.get_parameter('knowledge_dir').value
        logs_dir = self.get_parameter('logs_dir').value
        collections = self.get_parameter('collections').value
        self._top_k_default = self.get_parameter('top_k_default').value

        self._embedder = OllamaEmbedder(base_url, embedding_model)
        self._chroma = ChromaManager(chroma_db_path, collections)
        self._semantic_map = SemanticMap(self._chroma, self._embedder)
        self._knowledge_base = KnowledgeBase(self._chroma, self._embedder, knowledge_dir)
        self._task_history = TaskHistoryStore(self._chroma, self._embedder, logs_dir)

        ingested = self._knowledge_base.ingest()
        if ingested:
            self.get_logger().info(f'Ingested {ingested} knowledge_base chunks')
        ingested_tasks = self._task_history.ingest()
        if ingested_tasks:
            self.get_logger().info(f'Ingested {ingested_tasks} task_history entries')

        self._query_srv = self.create_service(QueryRAG, '/rag/query', self._handle_query)
        self._update_map_srv = self.create_service(
            UpdateMap, '/rag/update_map', self._handle_update_map,
        )

        self.get_logger().info('rag_node ready')

    def _handle_query(self, request: QueryRAG.Request, response: QueryRAG.Response):
        top_k = request.top_k if request.top_k > 0 else self._top_k_default
        try:
            query_embedding = self._embedder.embed_text(request.query_text)
            contexts, scores = self._chroma.query(
                request.collection_name, query_embedding, top_k,
            )
        except ValueError as exc:
            response.contexts = []
            response.scores = []
            response.success = False
            response.error_msg = str(exc)
            return response

        response.contexts = contexts
        response.scores = scores
        response.success = True
        response.error_msg = ''
        return response

    def _handle_update_map(self, request: UpdateMap.Request, response: UpdateMap.Response):
        try:
            self._semantic_map.upsert_object(request.object_data)
        except Exception as exc:
            response.success = False
            response.error_msg = str(exc)
            return response

        response.success = True
        response.error_msg = ''
        return response


def main(args: list[str] | None = None) -> None:
    """Entry point for the rag_node executable."""
    rclpy.init(args=args)
    node = RagNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
