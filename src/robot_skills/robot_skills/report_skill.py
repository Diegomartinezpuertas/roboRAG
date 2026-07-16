"""Report skill: formats and delivers the final response to the user."""

import json
from pathlib import Path

from std_msgs.msg import String


class ReportSkill:
    """Publishes the final natural-language response and logs the completed task.

    Args:
        response_publisher: ROS 2 publisher for the /robot/response topic.
        logs_dir: Directory where task completion logs are written.
    """

    def __init__(self, response_publisher, logs_dir: str) -> None:
        self._publisher = response_publisher
        self._logs_dir = Path(logs_dir)
        self._logs_dir.mkdir(parents=True, exist_ok=True)

    def report(self, params: dict, task_context: dict) -> dict:
        """Publishes the response and writes a task history log entry.

        Args:
            params: Skill params, expects "message" (str).
            task_context: Extra metadata to persist, e.g. {"task_id": ..., "goal_text": ...}.

        Returns:
            Dict with "published" (bool).
        """
        message = params.get('message', '')
        self._publisher.publish(String(data=message))

        log_entry = {**task_context, 'response': message}
        log_path = self._logs_dir / f'{task_context.get("task_id", "task")}.json'
        log_path.write_text(json.dumps(log_entry, indent=2), encoding='utf-8')

        return {'published': True}
