"""Synchronous wrappers around the robot's ROS 2 skill/RAG services, and a plan executor.

Note: an earlier version wrapped each skill in a LangChain @tool. That added a
heavy dependency purely for a name→callable registry the LLM never tool-called
(the planner emits plan JSON we parse ourselves). Skills are dispatched
directly here instead. A true LangChain agent loop is listed as future work in
the README.
"""

from __future__ import annotations

import json
import threading

# The service types are imported lazily inside the methods that build requests,
# so this module (and execute_plan in particular) stays importable — and
# unit-testable with a fake toolkit — without a ROS environment.

# Skills the planner may emit as plan steps, dispatched via /skills/execute.
VALID_SKILLS = ('navigate', 'explore', 'perceive', 'scan_360', 'report')


class RobotToolkit:
    """Synchronous callers for the robot's /skills/execute and /rag/query services.

    Args:
        node: The rclpy node hosting the service clients.
        skills_client: Client for /skills/execute.
        rag_client: Client for /rag/query.
    """

    def __init__(self, node, skills_client, rag_client) -> None:
        self._node = node
        self._skills_client = skills_client
        self._rag_client = rag_client

    def _wait_for_future(self, future, timeout_sec: float) -> None:
        # Event-based wait instead of nested spinning: the node runs on a
        # MultiThreadedExecutor with the service clients in their own callback
        # group, so another executor thread delivers the response while this
        # callback blocks. Never add/remove the node from throwaway executors —
        # that steals entity ownership from the main executor and leaves the
        # node deaf once the callback returns.
        done = threading.Event()
        future.add_done_callback(lambda _: done.set())
        if not done.wait(timeout=timeout_sec):
            future.cancel()

    def call_skill(self, skill_name: str, params: dict) -> dict:
        """Calls /skills/execute synchronously and returns the parsed result.

        Args:
            skill_name: One of "navigate", "explore", "perceive", "scan_360", "report".
            params: Skill-specific parameters.

        Returns:
            Parsed result_json as a dict.

        Raises:
            RuntimeError: If the skill execution fails or the service is unavailable.
        """
        from robot_interfaces.srv import ExecuteSkill

        if not self._skills_client.wait_for_service(timeout_sec=5.0):
            raise RuntimeError('/skills/execute service unavailable')
        request = ExecuteSkill.Request(skill_name=skill_name, params_json=json.dumps(params))
        future = self._skills_client.call_async(request)
        self._wait_for_future(future, timeout_sec=120.0)
        response = future.result()
        if response is None or not response.success:
            error = response.error_msg if response else 'service call timed out'
            raise RuntimeError(f'Skill "{skill_name}" failed: {error}')
        return json.loads(response.result_json) if response.result_json else {}

    def call_rag(self, query_text: str, collection_name: str, top_k: int = 5) -> list[str]:
        """Calls /rag/query synchronously and returns retrieved contexts.

        Args:
            query_text: Natural language query.
            collection_name: Target ChromaDB collection.
            top_k: Number of results to retrieve.

        Returns:
            List of retrieved text fragments. Empty if the service is unavailable
            or the query fails.
        """
        pairs = self.call_rag_with_scores(query_text, collection_name, top_k)
        return [context for context, _ in pairs]

    def call_rag_with_scores(
        self, query_text: str, collection_name: str, top_k: int = 5,
    ) -> list[tuple[str, float]]:
        """Calls /rag/query synchronously and returns (context, score) pairs.

        Args:
            query_text: Natural language query.
            collection_name: Target ChromaDB collection.
            top_k: Number of results to retrieve.

        Returns:
            List of (text fragment, cosine similarity score) pairs, ordered by
            decreasing relevance. Empty if the service is unavailable or the
            query fails.
        """
        from robot_interfaces.srv import QueryRAG

        if not self._rag_client.wait_for_service(timeout_sec=5.0):
            return []
        request = QueryRAG.Request(
            query_text=query_text, collection_name=collection_name, top_k=top_k,
        )
        future = self._rag_client.call_async(request)
        self._wait_for_future(future, timeout_sec=10.0)
        response = future.result()
        if response is None or not response.success:
            return []
        return list(zip(response.contexts, response.scores, strict=False))


def execute_plan(toolkit: RobotToolkit, steps: list[dict]) -> list[dict]:
    """Executes plan steps in sequence, dispatching each to its skill.

    Execution stops at the first failing step.

    Args:
        toolkit: RobotToolkit used to call /skills/execute.
        steps: Plan steps, each {"skill": str, "params": dict}.

    Returns:
        List of per-step results: {"skill": str, "result": dict | None, "error": str | None}.
    """
    results = []
    for step in steps:
        skill_name = step.get('skill')
        params = step.get('params', {})
        if skill_name not in VALID_SKILLS:
            results.append(
                {'skill': skill_name, 'result': None, 'error': f'Unknown skill: {skill_name}'},
            )
            break
        try:
            result = toolkit.call_skill(skill_name, params)
            results.append({'skill': skill_name, 'result': result, 'error': None})
        except Exception as exc:
            results.append({'skill': skill_name, 'result': None, 'error': str(exc)})
            break
    return results
