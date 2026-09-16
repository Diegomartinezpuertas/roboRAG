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


def _zone_line(name: str, area: dict | None) -> str:
    """Formats one known zone, with its centre when its bounds are known."""
    if not area:
        return name
    cx = (area['x_min'] + area['x_max']) / 2.0
    cy = (area['y_min'] + area['y_max']) / 2.0
    return f'{name} (centre x={cx:.2f}, y={cy:.2f})'


def build_user_prompt(
    goal_text: str, rag_context: list[str], zones: list[str] | dict[str, dict],
) -> str:
    """Builds the planner user-turn prompt from the goal, context, and zones.

    Zones given with their bounds are listed with their centre. Without it a
    goal like "the station nearest the base zone" cannot be answered at all:
    the planner sees the stations' coordinates and only the *name* of the base,
    so it can only guess (found 2026-09-16, ADR-032).

    Args:
        goal_text: Natural language goal from the user.
        rag_context: Text fragments retrieved from ChromaDB.
        zones: Zones the robot currently knows — {name: bounds} as
            ZoneStore.load_all() returns, or just their names.

    Returns:
        Formatted prompt string ready to send to the planner LLM.
    """
    context_block = '\n'.join(f'- {fragment}' for fragment in rag_context) or '(no context found)'
    areas = zones if isinstance(zones, dict) else dict.fromkeys(zones)
    zones_block = ', '.join(_zone_line(name, areas[name]) for name in sorted(areas))
    zones_block = zones_block or '(none defined yet)'
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


def build_report_prompt(
    goal_text: str, results: list[dict], corrections: list[str] | tuple[str, ...] = (),
) -> str:
    """Builds the reporter user-turn prompt from the goal and real step results.

    Args:
        goal_text: The user's original goal.
        results: Per-step results from execute_plan (skill, result, error).
        corrections: Notes on plan steps the validator replaced before execution
            (plan_validation.py). The user is told, rather than left to wonder
            why the robot explored instead of going where they asked.

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
    corrections_block = ''
    if corrections:
        corrections_block = (
            'PLAN CORRECTIONS (made before executing; mention them):\n'
            + '\n'.join(f'- {note}' for note in corrections) + '\n\n'
        )
    language = _detect_language(goal_text)
    return (
        f'USER GOAL: {goal_text}\n\n'
        f'{corrections_block}'
        f'EXECUTED STEPS AND RESULTS:\n{results_block}\n\n'
        f'Write the response to the user now. Reply in {language}.'
    )


PLACE_RESOLVER_SYSTEM_PROMPT = """
You decide which places a robot is being asked to go to.

You receive the user's GOAL and a numbered list of PLACES the robot knows: places it
remembers, with coordinates, and zones the user defined.

Return two lists:
- "places": the numbers of the listed places the goal asks the robot to go to. A listed
  place counts when the goal names it (ignoring accents, case, spaces and underscores),
  describes it (colours, how open or cluttered it is), refers to it by what is done
  there, or selects it by its position relative to another listed place ("the nearest
  to ...", "the farthest from ...", "the one furthest east").
- "missing": each place the goal asks the robot to go to that is NOT in the list, written
  as the goal names it. A name that only resembles a listed one is missing: "estacion_d"
  is not "estacion_a", and "the north station" is not a place unless one is called that.

A place the goal mentions only as a reference point ("nearest to the base") is not a
destination. If the goal has no destination (explore, look around), both lists are empty.

Answer with JSON only, no other text: {"places": [numbers], "missing": ["names"]}
"""


def build_resolver_prompt(goal_text: str, places: list) -> str:
    """Builds the user-turn prompt for the place resolver (plan_validation.py).

    Args:
        goal_text: The user's goal, verbatim.
        places: Candidate places (plan_validation.Place), numbered from 1 in the prompt.

    Returns:
        Formatted prompt string.
    """
    lines = '\n'.join(f'{number}. {place.text}' for number, place in enumerate(places, start=1))
    return f'GOAL: {goal_text}\n\nPLACES:\n{lines or "(none)"}\n\nAnswer with the JSON now.'



PLACES_SQL_SYSTEM_PROMPT = """
You look up a robot's memory of places by writing ONE SQLite query.

TABLE places(name TEXT, x REAL, y REAL, zone TEXT, description TEXT)
- name: the place's label. Either an identifier a user gave it (for example "punto_3"),
  the generic label "area" for a spot the robot described itself, or an English room type
  ("kitchen", "bedroom", "living_room", ...) for a room the user named.
- x, y: map-frame coordinates in metres.
- zone: the user-defined zone the place lies in, or "unknown area".
- description: English text in one of these forms:
  * a spot the robot described: "predominantly <colour> and <colour>, <clutter>", where
    <clutter> is exactly one of "an open, uncluttered space",
    "a moderately furnished space (N obstacle groups nearby)" or
    "a cluttered space with many objects (N obstacle groups nearby)";
    colours are plain English names: white, gray, black, red, orange, brown, yellow,
    green, blue, purple.
  * a named location: "<name>, a named location in the house".
  * a room the user named: what the room is for, in English and in Spanish.

Write the query that returns the rows the robot needs to decide where to go for the goal.
Rules:
- Always SELECT name, x, y, zone, description FROM places, filtered with WHERE.
- Goals are often in Spanish; the stored text is English. Match the stored vocabulary
  above, not a literal translation (for example "despejado" is "uncluttered", not "clear").
- Compare text case-insensitively with lower(column) LIKE. Beware of substrings: "red" is
  inside "uncluttered", so match a colour with its leading word: '%predominantly red%'
  or '% red%'.
- If the goal asks for no particular place (explore, look around, turn), return no rows:
  WHERE 0.
- A single read-only statement.

Answer with the SQL statement only, no explanation and no code fence.
"""


def build_places_sql_prompt(goal_text: str) -> str:
    """Builds the user-turn prompt for the SQL writer (sql_memory.py, ADR-033).

    Args:
        goal_text: The user's goal, verbatim.

    Returns:
        Formatted prompt string.
    """
    return f'GOAL: {goal_text}\n\nWrite the SQL statement now.'
