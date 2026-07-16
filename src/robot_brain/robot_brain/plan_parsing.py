"""Pure helpers for parsing LLM plan output and summarizing results.

Kept free of any ROS/rclpy imports so it is unit-testable without a ROS
environment (see tests/).
"""

import json


def parse_plan(raw_response: str) -> dict:
    """Parses the planner LLM's raw response into a plan dict.

    Tolerates a ```json ... ``` code fence around the JSON.

    Args:
        raw_response: Raw text returned by the planner model.

    Returns:
        Parsed plan dict (expected keys: "reasoning", "steps").

    Raises:
        ValueError: If the content is not valid JSON.
    """
    text = raw_response.strip()
    if text.startswith('```'):
        lines = text.splitlines()[1:]
        if lines and lines[-1].strip().startswith('```'):
            lines = lines[:-1]
        text = '\n'.join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f'Invalid JSON from planner: {text}') from exc


def summarize_results(results: list[dict]) -> str:
    """Builds a fallback summary from execution results (used if the reporter LLM fails).

    Args:
        results: Per-step results, each with an "error" key (None on success).

    Returns:
        A short Spanish status line.
    """
    failed = [r for r in results if r['error']]
    if failed:
        return f'Tarea completada con errores: {failed[0]["error"]}'
    return 'Tarea completada correctamente.'
