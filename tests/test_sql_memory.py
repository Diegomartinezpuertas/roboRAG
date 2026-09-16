"""Place memory as a SQL table the LLM queries (ADR-033) — pure logic, real SQLite."""

import pytest

from robot_brain.plan_validation import places_from_context
from robot_brain.sql_memory import (
    MAX_ROWS,
    check_select,
    parse_sql,
    place_row,
    query_places,
    rows_as_documents,
    write_places_db,
)

DOCUMENTS = [
    'punto_3 at (x=-2.10, y=-1.61) in unknown area: punto_3, a named location in the house',
    'area at (x=2.05, y=-0.01) in kitchen: predominantly red, a cluttered space '
    '(9 obstacle groups nearby)',
    'area at (x=0.50, y=1.00): predominantly green and gray, an open, uncluttered space',
    '## Template: explore an area\n\nPlan: explore.',  # no location: not a place
]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / 'places.db'
    assert write_places_db(path, DOCUMENTS) == 3
    return path


def test_documents_become_rows_and_rows_become_the_same_documents(db):
    rows = query_places(db, 'SELECT name, x, y, zone, description FROM places')
    assert rows_as_documents(rows)[:2] == DOCUMENTS[:2]
    # A place row without a zone round-trips without the " in <zone>" part.
    assert rows_as_documents(rows)[2] == DOCUMENTS[2]


def test_place_row_reads_label_coordinates_zone_and_description():
    assert place_row(DOCUMENTS[1]) == (
        'area', 2.05, -0.01, 'kitchen',
        'predominantly red, a cluttered space (9 obstacle groups nearby)',
    )
    assert place_row(DOCUMENTS[3]) is None


def test_a_filtered_query_returns_only_matching_places(db):
    rows = query_places(
        db, "SELECT name, x, y, zone, description FROM places "
            "WHERE lower(description) LIKE '%green%'",
    )
    assert [(row['name'], row['zone']) for row in rows] == [('area', '')]


def test_substring_matching_is_a_trap_the_sql_writer_can_fall_into(db):
    # "red" is inside "unclutte-red": a LIKE the model finds natural matches the
    # wrong place too. Pinned here because it is a property of the design being
    # measured, not something this module should paper over.
    rows = query_places(
        db, "SELECT name, x, y, zone, description FROM places "
            "WHERE lower(description) LIKE '%red%'",
    )
    assert len(rows) == 2


def test_rows_are_capped(tmp_path):
    path = tmp_path / 'many.db'
    write_places_db(path, [f'p{i} at (x={i}.00, y=0.00): place {i}' for i in range(20)])
    assert len(query_places(path, 'SELECT name, x, y, zone, description FROM places')) == MAX_ROWS


def test_documents_from_sql_are_places_the_plan_check_recognises(db):
    rows = query_places(
        db, "SELECT name, x, y, zone, description FROM places WHERE name = 'punto_3'",
    )
    places = places_from_context(rows_as_documents(rows))
    assert [(p.label, p.x, p.y) for p in places] == [('punto_3', -2.10, -1.61)]


@pytest.mark.parametrize('sql', [
    'DELETE FROM places',
    'SELECT * FROM places; DROP TABLE places',
    "ATTACH DATABASE 'x.db' AS x",
    'PRAGMA table_info(places)',
    'UPDATE places SET x = 0',
    '',
])
def test_anything_but_one_select_is_rejected(sql):
    with pytest.raises(ValueError):
        check_select(sql)


def test_the_database_is_opened_read_only_even_for_a_clever_select(db):
    # A write smuggled past the keyword check would still fail: mode=ro.
    with pytest.raises(ValueError):
        query_places(db, "WITH t AS (SELECT 1) SELECT name, x, y, zone, description FROM nope")
    assert len(query_places(db, 'SELECT name, x, y, zone, description FROM places')) == 3


def test_a_query_without_the_place_columns_is_rejected(db):
    with pytest.raises(ValueError):
        query_places(db, 'SELECT name FROM places')


def test_parse_sql_takes_the_bare_statement_and_tolerates_fences_and_json():
    assert parse_sql('SELECT 1') == 'SELECT 1'
    assert parse_sql('```sql\nSELECT 1;\n```') == 'SELECT 1;'
    assert parse_sql('{"sql": "SELECT 1"}') == 'SELECT 1'
    with pytest.raises(ValueError):
        parse_sql('```\n```')


def test_prose_is_not_a_select():
    with pytest.raises(ValueError):
        check_select(parse_sql('I would query the places table for red areas.'))
