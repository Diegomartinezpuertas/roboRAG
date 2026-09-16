"""Checks a plan's navigation steps against what the robot actually knows (ADR-032).

Pure logic, no ROS imports, so it is unit-tested in layer 1 (ADR-018). The one
judgement that needs language understanding — which remembered places a goal
asks for — is injected as a callable, so tests pass a fake and the node passes
a Qwen call.

The failure this exists for was measured, not imagined (rag-analysis §2.6):
asked for "estacion_d" when only a, b and c are in memory, the planner with RAG
navigated to estacion_a's coordinates in 6 runs out of 6. The plan was valid
JSON with a valid skill, so nothing downstream could object. Three checks, in
order of how much they need to know:

1. `navigate(zone=Z)` — Z must be a known zone. Exact, no model involved. A
   remembered place named as if it were a zone is replaced like any other
   unknown zone, not repaired to its coordinates: the repair was measured and
   turned a caught borrowing into a missed one (ADR-032, run B).
2. `navigate(x, y)` — the point must be one the robot knows: a retrieved memory
   or a known zone's centre, within GROUNDING_TOLERANCE_M. Exact, no model.
3. A grounded point must not be borrowed. The resolver lists the remembered
   places the goal asks for and the places it asks for that are *not* in memory.
   The step is replaced only when the goal asks for a missing place and the
   point belongs to none of the places it asks for.

The third check deliberately does nothing when the resolver merely disagrees
with the planner about *which* remembered place is meant (a spatial relation, a
confusable name). Those choices are the planner's; overriding them would trade
one model's error for another's. A replaced step becomes `explore`, which is
what the prompt rules already demand for an unknown place.
"""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field

# A navigate point counts as a known place within this radius, in metres. The
# planner copies coordinates from the prompt and sometimes rounds them to one
# decimal (-2.099 -> -2.1), an error of at most ~0.07 m; distinct memories are
# stored at least 0.5 m apart (scene merging, ADR-025; seeded landmarks further).
GROUNDING_TOLERANCE_M = 0.25

# Replacement for a rejected navigate step. The explore skill's own default
# duration applies (no params), the same as a planner-written bare explore.
EXPLORE_STEP = {'skill': 'explore', 'params': {}}

_PLACE_IN_TEXT = re.compile(
    r'(?P<label>[^\n]*?)\s*at \(x=(?P<x>-?\d+(?:\.\d+)?), y=(?P<y>-?\d+(?:\.\d+)?)\)',
)


@dataclass(frozen=True)
class Place:
    """A location the robot knows, as the planner saw it.

    Attributes:
        label: Short name — the memory's label ("estacion_a", "area") or the zone name.
        x: Map-frame x, in metres.
        y: Map-frame y, in metres.
        text: The full text the planner was shown for it (memory document or zone line).
        kind: "memory" (retrieved from RAG) or "zone" (SQLite).
    """

    label: str
    x: float
    y: float
    text: str
    kind: str


@dataclass(frozen=True)
class Resolution:
    """What the resolver decided the goal asks for.

    Attributes:
        places: Indices into the candidate list of the remembered places the goal
            asks the robot to go to.
        missing: Places the goal asks for that are not in the candidate list,
            as the goal names them. Empty when everything asked for is known.
    """

    places: tuple[int, ...] = ()
    missing: tuple[str, ...] = ()


@dataclass
class ValidationResult:
    """The validated plan steps and what, if anything, was changed.

    Attributes:
        steps: Steps to execute — the original ones, with rejected navigate steps
            replaced by explore.
        notes: One human-readable line per rejected step, in Spanish (they reach
            the dashboard and the final report). Empty when nothing changed.
        disagreements: Kept steps whose point the resolver did not attribute to
            the goal, for logging only (see the module docstring).
    """

    steps: list[dict]
    notes: list[str] = field(default_factory=list)
    disagreements: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """True if at least one step was replaced."""
        return bool(self.notes)


Resolver = Callable[[str, list[Place]], Resolution]


def places_from_context(contexts: list[str]) -> list[Place]:
    """Extracts every "<label> at (x=..., y=...)" location from retrieved text.

    Knowledge-base chunks carry no coordinates and yield nothing; memory and
    task-history documents yield their location.

    Args:
        contexts: Retrieved context strings, as injected into the planning prompt.

    Returns:
        One Place per coordinate pair found, in context order.
    """
    places = []
    for text in contexts:
        for match in _PLACE_IN_TEXT.finditer(text):
            label = match.group('label').strip().lstrip('-').strip() or 'place'
            places.append(Place(
                label=label, x=float(match.group('x')), y=float(match.group('y')),
                text=text.strip(), kind='memory',
            ))
    return places


def places_from_zones(zones: dict[str, dict]) -> list[Place]:
    """Turns the zone store's {name: bounds} into Places at each zone's centre.

    Args:
        zones: {name: {"x_min", "y_min", "x_max", "y_max"}} as ZoneStore.load_all returns.

    Returns:
        One Place per zone, sorted by name.
    """
    places = []
    for name in sorted(zones):
        area = zones[name]
        cx = (area['x_min'] + area['x_max']) / 2.0
        cy = (area['y_min'] + area['y_max']) / 2.0
        places.append(Place(
            label=name, x=round(cx, 2), y=round(cy, 2),
            text=f'zone "{name}" centred at (x={cx:.2f}, y={cy:.2f})', kind='zone',
        ))
    return places


def parse_resolution(raw_response: str, candidate_count: int) -> Resolution:
    """Parses the resolver model's JSON answer into a Resolution.

    The prompt numbers places from 1; the Resolution holds 0-based indices.
    Numbers outside the list are dropped rather than trusted.

    Args:
        raw_response: The model's raw text, expected {"places": [...], "missing": [...]}.
        candidate_count: How many places the prompt listed.

    Returns:
        The parsed Resolution.

    Raises:
        ValueError: If the text is not JSON of that shape.
    """
    text = raw_response.strip()
    if text.startswith('```'):
        text = '\n'.join(
            line for line in text.splitlines() if not line.strip().startswith('```')
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f'Resolver answer is not JSON: {raw_response!r}') from exc
    if not isinstance(data, dict):
        raise ValueError(f'Resolver answer is not an object: {raw_response!r}')
    places = []
    for number in data.get('places') or []:
        try:
            index = int(number) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= index < candidate_count:
            places.append(index)
    missing = tuple(
        str(name).strip() for name in data.get('missing') or [] if str(name).strip()
    )
    return Resolution(places=tuple(places), missing=missing)


def _normalize(name: str) -> str:
    """Folds a place name for comparison: no accents, case, spaces or punctuation."""
    folded = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]', '', folded.lower())


def _truly_missing(
    missing: tuple[str, ...], candidates: list[Place], zone_names: set[str],
) -> list[str]:
    """Drops "missing" names that are in fact known places.

    The resolver sometimes lists a place the goal uses only as a reference point
    ("the station farthest from estacion_a") as missing although it is right
    there in the list. Found in the design replay; a name that matches a known
    label is not missing, whatever the model says.
    """
    known = {_normalize(place.label) for place in candidates}
    known |= {_normalize(zone) for zone in zone_names}
    return [name for name in missing if _normalize(name) not in known]


def _point(params: dict) -> tuple[float, float] | None:
    try:
        return float(params['x']), float(params['y'])
    except (KeyError, TypeError, ValueError):
        return None


def _nearest(point: tuple[float, float], places: list[Place]) -> int | None:
    best, best_distance = None, GROUNDING_TOLERANCE_M
    for index, place in enumerate(places):
        distance = math.dist(point, (place.x, place.y))
        if distance < best_distance or (best is None and distance <= best_distance):
            best, best_distance = index, distance
    return best


def _replace(steps: list[dict], index: int) -> None:
    """Replaces steps[index] with explore, or drops it if an explore precedes it."""
    if any(step.get('skill') == 'explore' for step in steps[:index]):
        steps[index] = None  # type: ignore[call-overload]
    else:
        steps[index] = dict(EXPLORE_STEP)


def validate_plan(
    goal: str,
    steps: list[dict],
    candidates: list[Place],
    zone_names: set[str],
    resolve: Resolver,
) -> ValidationResult:
    """Validates every navigate step of a plan, replacing the ones that cannot stand.

    The resolver is called at most once, and only when a navigate(x, y) step
    lands on a known place — plans that explore, perceive or navigate by zone
    name cost no extra model call.

    Args:
        goal: The user's goal, verbatim.
        steps: Plan steps as parsed ({"skill": str, "params": dict}).
        candidates: Places the planner was shown: retrieved memories and known zones.
        zone_names: Names of the zones that exist.
        resolve: Decides which candidates the goal asks for, and what it asks for
            that is missing. May raise; the step is then kept (fail open).

    Returns:
        The validated steps and notes on what was replaced.
    """
    result = list(steps)
    notes: list[str] = []
    disagreements: list[str] = []
    resolution: Resolution | None = None
    resolver_failed = False

    for index, step in enumerate(steps):
        if step.get('skill') != 'navigate':
            continue
        params = step.get('params') or {}

        zone = params.get('zone')
        if zone is not None and _point(params) is None:
            if zone not in zone_names:
                _replace(result, index)
                notes.append(f'No existe ninguna zona llamada "{zone}"; exploro en su lugar.')
            continue

        point = _point(params)
        if point is None:
            continue
        grounded = _nearest(point, candidates)
        if grounded is None:
            _replace(result, index)
            notes.append(
                f'Las coordenadas ({point[0]:.2f}, {point[1]:.2f}) no corresponden a '
                f'ningún lugar que recuerde; exploro en su lugar.',
            )
            continue

        if resolution is None and not resolver_failed:
            try:
                resolution = resolve(goal, candidates)
            except Exception:  # noqa: BLE001 — any resolver failure keeps the plan
                resolver_failed = True
        if resolution is None:
            continue
        asked = {
            i for i in resolution.places if 0 <= i < len(candidates)
        }
        if grounded in asked or any(
            math.dist(point, (candidates[i].x, candidates[i].y)) <= GROUNDING_TOLERANCE_M
            for i in asked
        ):
            continue
        place = candidates[grounded]
        missing_names = _truly_missing(resolution.missing, candidates, zone_names)
        if missing_names:
            _replace(result, index)
            missing = ', '.join(f'"{name}"' for name in missing_names)
            notes.append(
                f'No tengo {missing} en la memoria: el plan iba a ({place.x:.2f}, '
                f'{place.y:.2f}), que es "{place.label}". Exploro en su lugar.',
            )
        else:
            disagreements.append(
                f'navigate({point[0]:.2f}, {point[1]:.2f}) -> "{place.label}", '
                f'not among the places the resolver attributed to the goal',
            )

    return ValidationResult(
        steps=[step for step in result if step is not None],
        notes=notes,
        disagreements=disagreements,
    )
