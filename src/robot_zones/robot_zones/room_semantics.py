"""What a named zone is *for*, so it can be found by function instead of by name.

A zone the user draws on the map is stored as a name and a rectangle. That is
enough to obey "go to the kitchen", but not "go where people usually cook":
the memory document for that zone used to say only `User-defined zone "cocina"
covering x[...] y[...]`, and the embedding of a bare name plus a pile of
coordinates sits far from the embedding of a sentence about cooking — far
enough to fall under the planner's relevance threshold and never reach the
prompt at all.

This module closes that gap without any model: a small table of room types,
each carrying the words a person would actually use for what happens there, in
Spanish and in English (bge-m3 is multilingual, and this project's users ask in
both — see docs/rag-analysis.md 2.4). A zone whose name matches a room type is
indexed with those sentences attached, so a functional query and the zone land
in the same neighbourhood of the embedding space.

Zones whose names match nothing (`estacion_a`, `punto_3`) keep the plain
description. Inventing a purpose for a name the robot cannot interpret would be
worse than saying nothing.

See docs/decisions/ADR-022-room-semantics.md.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class RoomType:
    """One kind of room, with everything that makes it retrievable by function.

    Attributes:
        key: Canonical English name, used as the semantic-object label, e.g. "kitchen".
        spanish_name: Canonical Spanish name, e.g. "cocina".
        spanish_article: Definite article for spanish_name ("la"/"el"), so the
            generated sentence reads naturally.
        aliases: Name fragments that identify this room type, accent-free and
            lowercase, in both languages. Matched against the tokens of a zone
            name, so "sala_de_estar" and "salon_grande" both resolve.
        english: What happens there and what it holds, in English.
        spanish: The same sentence in Spanish — not a translation for the
            reader's benefit, but a second set of vectors for Spanish queries.
    """

    key: str
    spanish_name: str
    spanish_article: str
    aliases: tuple[str, ...]
    english: str
    spanish: str

    @property
    def english_name(self) -> str:
        """The room type as a phrase, e.g. "living room"."""
        return self.key.replace('_', ' ')


# Ordered by how often a home robot meets them. The alias lists are deliberately
# generous with Spanish synonyms and regional variants: a user naming a zone is
# not consulting this table, they are typing whatever they call the room.
ROOM_TYPES: tuple[RoomType, ...] = (
    RoomType(
        key='kitchen',
        spanish_name='cocina',
        spanish_article='la',
        aliases=('kitchen', 'cocina', 'cocinar', 'kitchenette', 'cocinilla'),
        english=(
            'where meals are cooked and food is prepared, where the dishes are washed '
            'and where food is stored; it usually holds a stove, an oven, a fridge, '
            'a sink, worktops and cupboards'
        ),
        spanish=(
            'donde se cocina y se prepara la comida, donde se suele cocinar, donde se '
            'friegan los platos y se guardan los alimentos; suele tener fogones, horno, '
            'nevera, fregadero, encimera y armarios'
        ),
    ),
    RoomType(
        key='living_room',
        spanish_name='salón',
        spanish_article='el',
        aliases=('living', 'livingroom', 'lounge', 'salon', 'sala', 'estar', 'tv'),
        english=(
            'where people sit down to relax, watch television and receive visitors; '
            'it usually holds a sofa, armchairs, a coffee table and a TV'
        ),
        spanish=(
            'donde la gente se sienta a descansar, donde se ve la televisión y se '
            'recibe a las visitas; suele tener sofá, sillones, mesa baja y televisor'
        ),
    ),
    RoomType(
        key='dining_room',
        spanish_name='comedor',
        spanish_article='el',
        aliases=('dining', 'diningroom', 'comedor'),
        english=(
            'where people sit down to eat their meals together; it usually holds a '
            'dining table and chairs'
        ),
        spanish=(
            'donde la gente se sienta a comer, donde se come y se cena; suele tener '
            'una mesa de comedor y sillas'
        ),
    ),
    RoomType(
        key='bedroom',
        spanish_name='dormitorio',
        spanish_article='el',
        aliases=(
            'bedroom', 'dormitorio', 'habitacion', 'cuarto', 'alcoba', 'recamara', 'cama',
        ),
        english=(
            'where people sleep and rest at night, where clothes are kept; it usually '
            'holds a bed, a wardrobe and a bedside table'
        ),
        spanish=(
            'donde se duerme y se descansa por la noche, donde se guarda la ropa; '
            'suele tener cama, armario y mesilla de noche'
        ),
    ),
    RoomType(
        key='bathroom',
        spanish_name='baño',
        spanish_article='el',
        aliases=('bathroom', 'bano', 'aseo', 'servicio', 'toilet', 'wc', 'ducha', 'lavabo'),
        english=(
            'where people wash themselves, shower and use the toilet; it usually holds '
            'a toilet, a washbasin, a shower or a bathtub and a mirror'
        ),
        spanish=(
            'donde la gente se lava, se ducha y usa el inodoro; suele tener retrete, '
            'lavabo, ducha o bañera y espejo'
        ),
    ),
    RoomType(
        key='office',
        spanish_name='despacho',
        spanish_article='el',
        aliases=('office', 'oficina', 'despacho', 'estudio', 'study', 'escritorio'),
        english=(
            'where people work, study and use the computer; it usually holds a desk, '
            'a chair, a computer and shelves of books'
        ),
        spanish=(
            'donde se trabaja, se estudia y se usa el ordenador; suele tener escritorio, '
            'silla, ordenador y estanterías con libros'
        ),
    ),
    RoomType(
        key='hallway',
        spanish_name='pasillo',
        spanish_article='el',
        aliases=('hallway', 'corridor', 'pasillo', 'corredor', 'hall', 'distribuidor'),
        english=(
            'a narrow passage connecting the rooms, used to walk from one room to '
            'another rather than to stay in'
        ),
        spanish=(
            'un paso estrecho que conecta las habitaciones, por donde se pasa para ir '
            'de una habitación a otra en lugar de quedarse'
        ),
    ),
    RoomType(
        key='entrance',
        spanish_name='entrada',
        spanish_article='la',
        aliases=('entrance', 'entry', 'entrada', 'recibidor', 'vestibulo', 'puerta'),
        english=(
            'where people come in and go out of the building, where coats and keys are '
            'left; it holds the front door'
        ),
        spanish=(
            'por donde se entra y se sale de la casa, donde se dejan los abrigos y las '
            'llaves; tiene la puerta de entrada'
        ),
    ),
    RoomType(
        key='garage',
        spanish_name='garaje',
        spanish_article='el',
        aliases=('garage', 'garaje', 'cochera', 'parking', 'coche'),
        english='where the car is parked and tools are kept',
        spanish='donde se aparca el coche y se guardan las herramientas',
    ),
    RoomType(
        key='laundry',
        spanish_name='lavadero',
        spanish_article='el',
        aliases=('laundry', 'lavadero', 'lavanderia', 'colada', 'tendedero'),
        english=(
            'where clothes are washed and hung out to dry; it usually holds a washing '
            'machine'
        ),
        spanish=(
            'donde se lava la ropa y se tiende la colada; suele tener lavadora'
        ),
    ),
    RoomType(
        key='storage',
        spanish_name='trastero',
        spanish_article='el',
        aliases=('storage', 'trastero', 'almacen', 'despensa', 'pantry', 'armario'),
        english='where things that are not in daily use are stored on shelves and in boxes',
        spanish='donde se guardan en cajas y estanterías las cosas que no se usan a diario',
    ),
)


def classify_room(zone_name: str) -> RoomType | None:
    """Resolves a zone name to the kind of room it refers to.

    The name is compared token by token (accents stripped, plurals tolerated),
    so "cocina", "Cocina Grande", "sala_de_estar" and "bano_1" all resolve,
    while "estacion_a" resolves to nothing.

    Args:
        zone_name: Zone name as stored, e.g. "sala_de_estar".

    Returns:
        The matching RoomType, or None when the name carries no known meaning.
    """
    tokens = _tokenize(zone_name)
    if not tokens:
        return None
    for room in ROOM_TYPES:
        if any(token in room.aliases for token in tokens):
            return room
    return None


def describe_zone(zone_name: str, area: dict) -> str:
    """Builds the semantic-memory description of a user-defined zone.

    This is the text that gets embedded, so it carries three things: the
    bounds (the planner reads coordinates out of document text — ADR-012), the
    name the robot answers to, and, when the name is a known room type, what
    that room is for in both languages.

    Args:
        zone_name: Zone name as stored, e.g. "cocina".
        area: {x_min, y_min, x_max, y_max} in map-frame meters.

    Returns:
        A description ready to travel in a SemanticObject.
    """
    bounds = (
        f'covering x[{area["x_min"]:.2f}, {area["x_max"]:.2f}] '
        f'y[{area["y_min"]:.2f}, {area["y_max"]:.2f}] in the map frame'
    )
    tail = f'The robot can navigate to it or explore inside it by name ("{zone_name}").'
    room = classify_room(zone_name)
    if room is None:
        return f'User-defined zone "{zone_name}" {bounds}. {tail}'
    return (
        f'User-defined zone "{zone_name}" — a {room.english_name} '
        f'({room.spanish_article} {room.spanish_name}) — {bounds}. '
        f'This is the {room.english_name}: {room.english}. '
        f'Es {room.spanish_article} {room.spanish_name}: {room.spanish}. '
        f'{tail}'
    )


def scene_context(zone_name: str) -> str:
    """Builds the sentence appended to a scene description observed inside a zone.

    What the robot measures while exploring is colors and clutter — nothing in
    it says "kitchen". Appending the zone's function makes that observation
    retrievable by what the place is used for, which is how people ask for it.

    Args:
        zone_name: Zone the observation was made in; '' when outside every zone.

    Returns:
        A sentence to append to the scene description, or '' when the zone is
        unknown or carries no known meaning.
    """
    room = classify_room(zone_name)
    if room is None:
        return ''
    return (
        f'Observed inside the {room.english_name} ("{zone_name}"), '
        f'{room.english}. '
        f'Observado en {room.spanish_article} {room.spanish_name} ("{zone_name}"), '
        f'{room.spanish}.'
    )


def room_label(zone_name: str) -> str:
    """Returns the semantic-object label for a zone: its room type, else "zone"."""
    room = classify_room(zone_name)
    return room.key if room is not None else 'zone'


def suggested_zone_names() -> list[str]:
    """Returns the Spanish room names the classifier recognizes, for UI suggestions.

    Naming a zone "cocina" instead of "zona_1" is what unlocks functional
    retrieval, so the dashboard offers these as autocomplete while the user
    types a zone name.
    """
    return [room.spanish_name for room in ROOM_TYPES]


def _tokenize(zone_name: str) -> list[str]:
    """Splits a zone name into accent-free, singular, lowercase tokens."""
    stripped = ''.join(
        char for char in unicodedata.normalize('NFKD', zone_name.lower())
        if not unicodedata.combining(char)
    )
    tokens = [token for token in re.split(r'[^a-z0-9]+', stripped) if token]
    # "banos" and "dormitorios" are the same room as their singulars; a bare
    # trailing "s" is stripped as a second chance, never replacing the original.
    return tokens + [token[:-1] for token in tokens if token.endswith('s') and len(token) > 3]
