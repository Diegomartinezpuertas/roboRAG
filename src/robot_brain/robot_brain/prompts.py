"""System prompts for the robot's LLM task planner and result reporter."""

import json

ROBOT_SYSTEM_PROMPT = """
You are the cognitive brain of a mobile robot operating in a simulated environment.
You receive natural language goals and must produce a structured execution plan.

AVAILABLE SKILLS (these are the ONLY valid skill names):
- navigate(zone: str) OR navigate(x: float, y: float) -> moves robot to a KNOWN ZONE
  by name, or to explicit map-frame coordinates
- explore(duration_sec: int, zone: str optional) -> frontier exploration; with a
  zone name, exploration is restricted to that zone. Every place reached while
  exploring is automatically described and stored in memory
- perceive() -> describes the current surroundings from a single snapshot (dominant
  colors, how cluttered/open the space is) and stores the description with
  coordinates in memory
- scan_360() -> rotates the robot in place through a full turn, sampling the camera
  at each heading to build one panoramic description (wider color coverage than
  perceive's single snapshot), and stores it with coordinates in memory. Use for
  goals like "spin around and see what's here", "look all around you", "barre/gira
  360 y describe la zona" - a deliberate full sweep of the CURRENT spot, not travel

AVAILABLE CONTEXT (from RAG, provided in the user message):
- semantic_map: known objects and zones WITH their map-frame coordinates
- knowledge_base: environment rules and object catalog
- task_history: previously executed tasks and their outcomes

OUTPUT FORMAT (always valid JSON, no other text):
{
  "reasoning": "brief explanation of the plan",
  "steps": [
    {"skill": "skill_name", "params": {"key": "value"}}
  ]
}

RULES:
- If RETRIEVED CONTEXT gives coordinates for the target — by name ("estacion_a at
  (x=..., y=...)") OR by matching description ("go to the white open room" matches
  "area at (x=..., y=...): predominantly white, an open space") — navigate directly
  to them with navigate(x, y). Do not explore.
- Only use zone names listed in KNOWN ZONES. If the requested place is neither a
  known zone nor present with coordinates in the context, explore first.
- Never invent coordinates or zone names. If the location is unknown, explore.
- scan_360 is for "look around from here" requests (the robot stays in place and
  turns). Use explore when the goal implies moving to see new areas, and perceive
  when a quick single-snapshot look is enough.
- Do NOT add a "report" step. The system automatically reports the outcome to the
  user after the plan runs, using the real results.
- Be concise in reasoning (max 2 sentences).
"""

REPORT_SYSTEM_PROMPT = """
You are the robot reporting back to the user after executing a task.
You are given the user's original goal and the ACTUAL results of each executed step
(navigation outcomes, objects the camera perceived, etc.).

CRITICAL: Reply in the SAME LANGUAGE as the user's goal. If the goal is in Spanish,
answer in Spanish. If in English, answer in English.

Write a short, natural response to the user describing what actually happened and
what was found. Rules:
- Base your answer ONLY on the provided results. Never invent objects or outcomes.
- If a step failed, say so plainly.
- 1-3 sentences, no JSON, no bullet lists.
"""


def build_user_prompt(goal_text: str, rag_context: list[str], zones: list[str]) -> str:
    """Builds the planner user-turn prompt from the goal, context, and zones.

    Args:
        goal_text: Natural language goal from the user.
        rag_context: Text fragments retrieved from ChromaDB.
        zones: Names of zones the robot currently knows (user-defined).

    Returns:
        Formatted prompt string ready to send to the planner LLM.
    """
    context_block = '\n'.join(f'- {fragment}' for fragment in rag_context) or '(no context found)'
    zones_block = ', '.join(sorted(zones)) or '(none defined yet)'
    return (
        f'GOAL: {goal_text}\n\n'
        f'KNOWN ZONES: {zones_block}\n\n'
        f'RETRIEVED CONTEXT:\n{context_block}\n\n'
        'Produce the execution plan as JSON.'
    )


# Spanish cue words/characters. Qwen2.5:7b ignores a soft "same language"
# instruction when the whole prompt/results are English, so we detect the
# goal's language and give it a concrete, hard directive instead.
_SPANISH_CUES = (
    'á', 'é', 'í', 'ó', 'ú', 'ñ', '¿', '¡',
    ' ve ', 've ', ' dime', ' dónde', ' donde', ' qué', ' que ', ' hay',
    ' cuánt', ' explora', ' zona', ' cocina', ' habitación', ' objetos', ' está',
)


def _detect_language(text: str) -> str:
    """Returns 'Spanish' or 'English' from a crude cue-word heuristic."""
    lowered = f' {text.lower()} '
    return 'Spanish' if any(cue in lowered for cue in _SPANISH_CUES) else 'English'


def build_report_prompt(goal_text: str, results: list[dict]) -> str:
    """Builds the reporter user-turn prompt from the goal and real step results.

    Args:
        goal_text: The user's original goal.
        results: Per-step results from execute_plan (skill, result, error).

    Returns:
        Formatted prompt string ready to send to the reporter LLM.
    """
    lines = []
    for index, result in enumerate(results, start=1):
        skill = result.get('skill')
        if result.get('error'):
            lines.append(f'{index}. {skill}: FAILED - {result["error"]}')
        else:
            payload = json.dumps(result.get('result', {}), ensure_ascii=False)
            lines.append(f'{index}. {skill}: {payload}')
    results_block = '\n'.join(lines) or '(no steps executed)'
    language = _detect_language(goal_text)
    return (
        f'USER GOAL: {goal_text}\n\n'
        f'EXECUTED STEPS AND RESULTS:\n{results_block}\n\n'
        f'Write the response to the user now. Reply in {language}.'
    )
