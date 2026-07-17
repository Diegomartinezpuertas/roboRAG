"""ROS 2 node that receives natural language goals and produces execution plans."""

import json

import rclpy
from rclpy.callback_groups import MutuallyExclusiveCallbackGroup
from rclpy.node import Node
from std_msgs.msg import String

from robot_interfaces.srv import ExecuteSkill, QueryRAG
from robot_zones.zone_store import ZoneStore

from robot_brain.plan_parsing import parse_plan, summarize_results
from robot_brain.prompts import (
    REPORT_SYSTEM_PROMPT,
    ROBOT_SYSTEM_PROMPT,
    build_report_prompt,
    build_user_prompt,
)
from robot_brain.qwen_client import QwenClient
from robot_brain.toolkit import RobotToolkit, execute_plan


class LLMPlannerNode(Node):
    """ROS 2 node that receives natural language goals and produces execution plans.

    Subscribes:
        /robot/goal (std_msgs/String): Natural language task description.

    Publishes:
        /robot/status (std_msgs/String): Current execution status.

    Services (client):
        /rag/query (QueryRAG): Retrieve context from ChromaDB.
        /skills/execute (ExecuteSkill): Dispatch skills to robot_skills package.

    Parameters:
        ollama_base_url (str): Ollama server URL. Default: http://localhost:11434
        llm_model (str): Model name for task planning. Default: qwen2.5:7b
        llm_temperature (float): Sampling temperature. Default: 0.0 (deterministic).
        max_plan_steps (int): Maximum steps in a single plan. Default: 10
        rag_score_threshold (float): Minimum cosine similarity for a retrieved
            RAG context to be injected into the planning prompt. Below this,
            a "relevant" hit is usually just lexical noise (e.g. cross-lingual
            query vs English docs) that actively misleads the planner rather
            than helping it. Default: 0.45
        rag_enabled (bool): When False, no RAG context is retrieved or injected.
            This is the ablation switch for the A/B navigation benchmark. Default: True
        zones_in_prompt (bool): When False, known zone names are withheld from the
            prompt (the "blind" control condition). Default: True
        zones_db (str): SQLite file with user-defined navigation zones.
    """

    def __init__(self) -> None:
        super().__init__('llm_planner_node')

        self.declare_parameter('ollama_base_url', 'http://localhost:11434')
        self.declare_parameter('llm_model', 'qwen2.5:7b')
        self.declare_parameter('llm_temperature', 0.0)
        self.declare_parameter('max_plan_steps', 10)
        self.declare_parameter('rag_score_threshold', 0.45)
        self.declare_parameter('rag_enabled', True)
        self.declare_parameter('zones_in_prompt', True)
        self.declare_parameter('dry_run', False)
        self.declare_parameter('zones_db', '/home/diego/robot_ws/data/zones.db')

        base_url = self.get_parameter('ollama_base_url').value
        llm_model = self.get_parameter('llm_model').value
        temperature = self.get_parameter('llm_temperature').value
        self._max_plan_steps = self.get_parameter('max_plan_steps').value
        self._rag_score_threshold = self.get_parameter('rag_score_threshold').value
        # rag_enabled / zones_in_prompt / dry_run are intentionally NOT cached:
        # _on_goal re-reads them per goal so the benchmark can flip conditions
        # live with `ros2 param set`.
        self._zones = ZoneStore(self.get_parameter('zones_db').value)

        self._qwen = QwenClient(base_url, llm_model, temperature=temperature)
        self._status_pub = self.create_publisher(String, '/robot/status', 10)
        # Raw plan JSON published per goal — consumed by the planning benchmark
        # (eval/) to score the planner's decision without depending on the
        # flaky low-level navigation. See ADR-013.
        self._plan_pub = self.create_publisher(String, '/robot/plan', 10)

        # Clients live in their own callback group so a MultiThreadedExecutor
        # can deliver their responses while the goal callback (default group)
        # is blocked waiting on them.
        self._client_group = MutuallyExclusiveCallbackGroup()
        self._skills_client = self.create_client(
            ExecuteSkill, '/skills/execute', callback_group=self._client_group,
        )
        self._rag_client = self.create_client(
            QueryRAG, '/rag/query', callback_group=self._client_group,
        )
        self._toolkit = RobotToolkit(self, self._skills_client, self._rag_client)

        self._goal_sub = self.create_subscription(String, '/robot/goal', self._on_goal, 10)
        self.get_logger().info(
            f'llm_planner_node ready (rag_enabled={self.get_parameter("rag_enabled").value})',
        )

    def _publish_status(self, status: str) -> None:
        self._status_pub.publish(String(data=status))
        self.get_logger().info(status)

    def _on_goal(self, msg: String) -> None:
        goal_text = msg.data
        # Re-read the ablation flags on every goal so the benchmark can switch
        # conditions with `ros2 param set` without restarting the node (which
        # would reset the shared SLAM map and break cross-condition fairness).
        rag_enabled = self.get_parameter('rag_enabled').value
        zones_in_prompt = self.get_parameter('zones_in_prompt').value
        self._publish_status(f'Received goal: {goal_text}')

        rag_context: list[str] = []
        if rag_enabled:
            rag_context += self._retrieve_context(goal_text, 'knowledge_base', top_k=3)
            rag_context += self._retrieve_context(goal_text, 'semantic_map', top_k=3)
            rag_context += self._retrieve_context(goal_text, 'task_history', top_k=2)

        zones = self._known_zones() if zones_in_prompt else []
        user_prompt = build_user_prompt(goal_text, rag_context, zones)
        self._publish_status('Generating plan with Qwen...')
        raw_response = self._qwen.generate_plan(ROBOT_SYSTEM_PROMPT, user_prompt)

        try:
            plan = parse_plan(raw_response)
        except ValueError as exc:
            self._publish_status(f'Failed to parse plan: {exc}')
            self._safe_report(goal_text, 'No pude generar un plan valido para esa tarea.')
            return

        # Drop any report steps the planner emitted: the final user-facing
        # response is generated from the real execution results below, not
        # pre-written by the planner before anything ran.
        steps = [s for s in plan.get('steps', []) if s.get('skill') != 'report']
        steps = steps[: self._max_plan_steps]
        self._publish_status(f'Qwen razona: {plan.get("reasoning", "(sin razonamiento)")}')
        for index, step in enumerate(steps):
            params_str = json.dumps(step.get('params', {}), ensure_ascii=False)
            self._publish_status(
                f'Plan paso {index + 1}/{len(steps)}: {step.get("skill")}({params_str})',
            )
        # Publish the raw plan for the planning benchmark / dashboard.
        self._plan_pub.publish(String(data=json.dumps(plan, ensure_ascii=False)))
        self.get_logger().info(f'Qwen raw plan: {json.dumps(plan, ensure_ascii=False)}')

        if self.get_parameter('dry_run').value:
            # Benchmark/inspection mode: decide the plan but don't drive the robot.
            self._publish_status('Dry run: plan produced, execution skipped')
            return

        results = execute_plan(self._toolkit, steps)
        for index, result in enumerate(results):
            status = 'ok' if result['error'] is None else f'error: {result["error"]}'
            self._publish_status(f'Step {index + 1}/{len(results)} ({result["skill"]}): {status}')

        self._report_from_results(goal_text, results)

    def _known_zones(self) -> list[str]:
        """Returns the names of zones the robot currently knows.

        Reads the zones database on every call so zones created in the
        dashboard are visible without restarting the planner.
        """
        return list(self._zones.load_all())

    def _retrieve_context(self, query_text: str, collection_name: str, top_k: int) -> list[str]:
        """Retrieves RAG context, dropping hits below rag_score_threshold.

        An irrelevant top hit (cross-lingual mismatch, wrong topic) is worse
        than no context at all — it gets stuffed into the prompt as if it
        were ground truth. See docs/decisions/ADR-011.

        Args:
            query_text: Natural language query.
            collection_name: Target ChromaDB collection.
            top_k: Number of results to request before filtering.

        Returns:
            Context strings that met the relevance threshold.
        """
        hits = self._toolkit.call_rag_with_scores(query_text, collection_name, top_k)
        dropped = [c for c, s in hits if s < self._rag_score_threshold]
        if dropped:
            self.get_logger().debug(
                f'Dropped {len(dropped)} low-relevance {collection_name} hit(s) below '
                f'{self._rag_score_threshold}',
            )
        return [context for context, score in hits if score >= self._rag_score_threshold]

    def _report_from_results(self, goal_text: str, results: list[dict]) -> None:
        """Generates and publishes the final response from real execution results.

        A second LLM call summarizes what actually happened, rather than the
        planner pre-writing the answer before execution (see Fase 0.3 / ADR-012).

        Args:
            goal_text: The user's original goal.
            results: Per-step results from execute_plan.
        """
        if not results:
            self._safe_report(goal_text, 'No ejecuté ninguna acción para esa tarea.')
            return
        self._publish_status('Generando respuesta final...')
        try:
            message = self._qwen.chat(
                REPORT_SYSTEM_PROMPT, build_report_prompt(goal_text, results),
            ).strip()
        except Exception as exc:
            self.get_logger().error(f'Report generation failed: {exc}')
            message = summarize_results(results)
        self._safe_report(goal_text, message)

    def _safe_report(self, goal_text: str, message: str) -> None:
        try:
            self._toolkit.call_skill('report', {'message': message, 'goal_text': goal_text})
        except RuntimeError as exc:
            self.get_logger().error(f'Failed to publish final report: {exc}')


def main(args: list[str] | None = None) -> None:
    """Entry point for the llm_planner_node executable."""
    rclpy.init(args=args)
    node = LLMPlannerNode()
    # MultiThreadedExecutor so service client responses (own callback group)
    # are delivered while the goal callback blocks waiting for them.
    executor = rclpy.executors.MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
