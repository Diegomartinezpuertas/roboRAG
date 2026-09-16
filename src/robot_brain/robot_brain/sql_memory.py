"""Place memory as a SQL table the LLM queries itself — the "LLM → SQL" design (ADR-033).

Pure logic, no ROS imports, unit-tested in layer 1. It exists to measure one
thing: whether the robot's place memory is better looked up by vector
similarity (RAG) or by a query the LLM writes over a table holding the *same*
places. rag-analysis §1 calls these designs C and B; until this module, B was
only argued.

The table mirrors what vector memory holds. Every semantic_map document,
"<label> at (x=…, y=…) in <zone>: <description>", becomes one row, and rows
go back to the planner in that same format. The planner prompt and the plan
check therefore see the same shape whichever way the places were found.

Safety is not optional when a model writes SQL: the database is opened
read-only, only one SELECT (or WITH … SELECT) statement is accepted, and the
row count is capped.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

PLACES_TABLE = 'places'
PLACES_COLUMNS = ('name', 'x', 'y', 'zone', 'description')

# Rows handed to the planner at most. Vector retrieval gives it three; SQL gets a
# little more room because a filter can legitimately match a handful of places,
# but not the whole table — at real memory sizes that is not an option.
MAX_ROWS = 5

_DOCUMENT = re.compile(
    r'^\s*(?P<name>.+?) at \(x=(?P<x>-?\d+(?:\.\d+)?), y=(?P<y>-?\d+(?:\.\d+)?)\)'
    r'(?: in (?P<zone>[^:]+))?:\s*(?P<description>.*)$',
    re.S,
)
_FORBIDDEN = re.compile(
    r'\b(insert|update|delete|drop|create|alter|replace|attach|detach|pragma|vacuum|'
    r'reindex|analyze|begin|commit|rollback|savepoint|release)\b',
    re.I,
)


def place_row(document: str) -> tuple[str, float, float, str, str] | None:
    """Parses one semantic_map document into a places row.

    Args:
        document: Text as stored, e.g. "area at (x=1.00, y=2.00) in kitchen: predominantly white".

    Returns:
        (name, x, y, zone, description), or None when the text carries no location.
    """
    match = _DOCUMENT.match(document)
    if match is None:
        return None
    return (
        match.group('name').strip(),
        float(match.group('x')),
        float(match.group('y')),
        (match.group('zone') or '').strip(),
        match.group('description').strip(),
    )


def write_places_db(db_path: str | Path, documents: list[str]) -> int:
    """Replaces the places table with one row per document that has a location.

    Args:
        db_path: SQLite file to (re)create.
        documents: semantic_map documents, as /rag/inspect returns them.

    Returns:
        Number of rows written.
    """
    rows = [row for row in (place_row(doc) for doc in documents) if row is not None]
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(f'DROP TABLE IF EXISTS {PLACES_TABLE}')
        connection.execute(
            f'CREATE TABLE {PLACES_TABLE} '
            '(name TEXT, x REAL, y REAL, zone TEXT, description TEXT)',
        )
        connection.executemany(f'INSERT INTO {PLACES_TABLE} VALUES (?, ?, ?, ?, ?)', rows)
    return len(rows)


def parse_sql(raw_response: str) -> str:
    """Extracts the query from the model's answer.

    The model is asked for the bare statement: SQL is full of quotes, and
    wrapping it in JSON made the model break the escaping (seen while designing
    the prompt). A code fence, a leading "sql" tag or a JSON {"sql": ...} object
    are tolerated anyway.

    Args:
        raw_response: The model's raw text.

    Returns:
        The SQL text, stripped.

    Raises:
        ValueError: If nothing that looks like a statement is left.
    """
    text = raw_response.strip()
    if text.startswith('{'):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and isinstance(data.get('sql'), str):
            text = data['sql']
    lines = [line for line in text.splitlines() if not line.strip().startswith('```')]
    text = '\n'.join(lines).strip()
    if text.lower().startswith('sql\n'):
        text = text[4:].strip()
    if not text:
        raise ValueError(f'no SQL in the answer: {raw_response!r}')
    return text


def check_select(sql: str) -> str:
    """Accepts exactly one read-only SELECT statement and returns it without a trailing ';'.

    Args:
        sql: Query text written by the model.

    Returns:
        The statement, ready to be wrapped.

    Raises:
        ValueError: If it is not a single SELECT/WITH statement or names a
            write or administrative keyword.
    """
    statement = sql.strip().rstrip(';').strip()
    if not statement:
        raise ValueError('empty query')
    if ';' in statement:
        raise ValueError('more than one statement')
    if not re.match(r'^(select|with)\b', statement, re.I):
        raise ValueError('not a SELECT')
    if _FORBIDDEN.search(statement):
        raise ValueError('write or administrative keyword')
    return statement


def query_places(db_path: str | Path, sql: str, max_rows: int = MAX_ROWS) -> list[dict]:
    """Runs a model-written SELECT over the places table, read-only and capped.

    Args:
        db_path: SQLite file written by write_places_db.
        sql: The model's query; it must return the five place columns.
        max_rows: Rows returned at most.

    Returns:
        Rows as dicts with name, x, y, zone and description.

    Raises:
        ValueError: If the query is rejected by check_select, fails to run, or
            does not return the place columns.
    """
    statement = check_select(sql)
    uri = f'file:{Path(db_path).resolve()}?mode=ro'
    try:
        with sqlite3.connect(uri, uri=True, timeout=2.0) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f'SELECT * FROM ({statement}) LIMIT ?', (max_rows,),
            ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError(f'query failed: {exc}') from exc
    if rows and not set(PLACES_COLUMNS) <= set(rows[0].keys()):
        raise ValueError(f'query must return {", ".join(PLACES_COLUMNS)}')
    return [{column: row[column] for column in PLACES_COLUMNS} for row in rows]


def rows_as_documents(rows: list[dict]) -> list[str]:
    """Formats place rows exactly like semantic_map documents.

    Args:
        rows: Rows from query_places.

    Returns:
        One "<name> at (x=…, y=…) in <zone>: <description>" string per row.
    """
    documents = []
    for row in rows:
        zone = f' in {row["zone"]}' if row['zone'] else ''
        documents.append(
            f'{row["name"]} at (x={float(row["x"]):.2f}, y={float(row["y"]):.2f})'
            f'{zone}: {row["description"]}',
        )
    return documents
