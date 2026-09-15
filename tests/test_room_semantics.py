"""Tests for the room-meaning table behind functional zone retrieval (ADR-022).

What matters here is not the prose: it is that a zone the user names after a
room ends up carrying, in its indexed text, the words someone would use to ask
for that room by what they do in it — and that a name the robot cannot
interpret stays plain instead of being given an invented purpose.
"""

import pytest

from robot_zones.room_semantics import (
    ROOM_TYPES,
    classify_room,
    describe_zone,
    room_label,
    scene_context,
    suggested_zone_names,
)

AREA = {'x_min': 0.5, 'y_min': -1.0, 'x_max': 2.0, 'y_max': 0.5}


# --- classification --------------------------------------------------------

@pytest.mark.parametrize(('name', 'expected'), [
    ('cocina', 'kitchen'),
    ('kitchen', 'kitchen'),
    ('salon', 'living_room'),
    ('sala_de_estar', 'living_room'),
    ('dormitorio', 'bedroom'),
    ('habitacion', 'bedroom'),
    ('bano', 'bathroom'),
    ('wc', 'bathroom'),
    ('despacho', 'office'),
    ('pasillo', 'hallway'),
    ('garaje', 'garage'),
])
def test_names_in_either_language_resolve_to_their_room(name, expected):
    assert classify_room(name).key == expected


@pytest.mark.parametrize('name', [
    'Cocina',           # the user typed capitals
    'cocina_grande',    # a qualifier was appended
    'segunda cocina',   # ...or prepended
    'COCINAS',          # plural, shouting
])
def test_a_room_is_recognized_however_the_name_was_written(name):
    assert classify_room(name).key == 'kitchen'


def test_accents_do_not_hide_the_room():
    """Zone names keep their accents; the classifier must not care."""
    assert classify_room('habitación').key == 'bedroom'
    assert classify_room('baño_pequeño').key == 'bathroom'


@pytest.mark.parametrize('name', ['', 'estacion_a', 'zona_1', 'punto_de_carga', '   '])
def test_a_name_with_no_known_meaning_classifies_as_nothing(name):
    assert classify_room(name) is None


def test_every_room_answers_to_its_own_canonical_names():
    """The names the UI suggests must be exactly the names that resolve."""
    for room in ROOM_TYPES:
        assert classify_room(room.spanish_name) is room
        assert classify_room(room.key) is room


def test_no_alias_is_claimed_by_two_rooms():
    """An ambiguous alias would silently resolve by table order, not by meaning."""
    seen: dict[str, str] = {}
    for room in ROOM_TYPES:
        for alias in room.aliases:
            assert alias not in seen, f'"{alias}" is claimed by {seen.get(alias)} and {room.key}'
            seen[alias] = room.key


def test_suggested_names_are_the_spanish_ones_and_all_resolve():
    names = suggested_zone_names()
    assert 'cocina' in names
    assert all(classify_room(name) is not None for name in names)


# --- the indexed description ----------------------------------------------

def test_a_known_room_is_described_by_what_happens_there_in_both_languages():
    text = describe_zone('cocina', AREA)
    assert 'cocina' in text and 'kitchen' in text
    assert 'cooked' in text            # English query surface
    assert 'se cocina' in text         # Spanish query surface
    assert 'x[0.50, 2.00]' in text     # coordinates still travel in the text (ADR-012)


def test_an_unknown_name_keeps_the_plain_description():
    text = describe_zone('estacion_a', AREA)
    assert 'estacion_a' in text
    assert 'x[0.50, 2.00] y[-1.00, 0.50]' in text
    # Nothing was invented about what the place is for.
    assert 'This is the' not in text


def test_the_description_always_says_the_zone_can_be_reached_by_name():
    for name in ('cocina', 'estacion_a'):
        assert f'by name ("{name}")' in describe_zone(name, AREA)


def test_bounds_are_formatted_to_centimeters():
    text = describe_zone('cocina', {'x_min': 1 / 3, 'y_min': 0.0, 'x_max': 2.0, 'y_max': 1.0})
    assert 'x[0.33, 2.00]' in text


# --- what an explored scene inherits from its zone -------------------------

def test_a_scene_inside_a_known_room_inherits_its_purpose():
    context = scene_context('cocina')
    assert 'kitchen' in context and 'cocina' in context
    assert 'cooked' in context and 'se cocina' in context


@pytest.mark.parametrize('zone', ['', 'estacion_a'])
def test_a_scene_outside_any_known_room_gets_no_context(zone):
    assert scene_context(zone) == ''


# --- labels ----------------------------------------------------------------

def test_the_label_is_the_room_type_when_the_name_has_one():
    assert room_label('cocina') == 'kitchen'
    assert room_label('sala_de_estar') == 'living_room'


def test_the_label_falls_back_to_a_neutral_word():
    assert room_label('estacion_a') == 'zone'
