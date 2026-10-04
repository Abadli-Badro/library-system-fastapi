"""SQLite access layer: schema DDL, connection factory, queries.

Enforcement note
----------------
"A copy that is on loan cannot be lent again" is enforced by the partial
unique index ``ux_loans_open_copy`` (see ``OPEN_LOAN_INDEX_SQL``), i.e. inside
the database itself.  Nothing an application does can be relied upon here: an
``if not has_open_loan()`` check in Python is a time-of-check/time-of-use race
between two concurrent lends, and any other writer (script, REPL, second
process) can skip it entirely.  SQLite applies the unique index atomically as
part of the INSERT's write transaction, so no writer can ever commit a second
open loan for the same copy.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "library.db"

SCHEMA_SQL = """
CREATE TABLE books (
    id             INTEGER PRIMARY KEY,
    title          TEXT NOT NULL,
    author         TEXT NOT NULL,
    isbn           TEXT NOT NULL UNIQUE,
    published_year INTEGER
);

CREATE TABLE copies (
    id      INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL REFERENCES books(id)
);

CREATE TABLE members (
    id        INTEGER PRIMARY KEY,
    name      TEXT NOT NULL,
    email     TEXT NOT NULL UNIQUE,
    joined_at TEXT NOT NULL
);

CREATE TABLE loans (
    id          INTEGER PRIMARY KEY,
    copy_id     INTEGER NOT NULL REFERENCES copies(id),
    member_id   INTEGER NOT NULL REFERENCES members(id),
    borrowed_at TEXT NOT NULL,
    due_at      TEXT NOT NULL,
    returned_at TEXT,
    CHECK (due_at > borrowed_at),
    CHECK (returned_at IS NULL OR returned_at >= borrowed_at)
);
"""

# The invariant: at most one open (returned_at IS NULL) loan per copy.
OPEN_LOAN_INDEX_SQL = """
CREATE UNIQUE INDEX ux_loans_open_copy ON loans(copy_id) WHERE returned_at IS NULL
"""

# Query index for GET /members/{id}/loans/current: partial (only open loans),
# matches the WHERE member_id filter and the ORDER BY borrowed_at DESC.
CURRENT_LOANS_INDEX_SQL = """
CREATE INDEX ix_loans_member_open_borrowed
    ON loans(member_id, borrowed_at DESC) WHERE returned_at IS NULL
"""

# The endpoint that matters.
CURRENT_LOANS_QUERY = """
SELECT l.id AS loan_id, b.title, l.borrowed_at, l.due_at
FROM loans l
JOIN copies c ON c.id = l.copy_id
JOIN books b ON b.id = c.book_id
WHERE l.member_id = ? AND l.returned_at IS NULL
ORDER BY l.borrowed_at DESC
"""

OPEN_LOAN_BY_COPY_QUERY = """
SELECT id, copy_id, member_id, borrowed_at, due_at, returned_at
FROM loans
WHERE copy_id = ? AND returned_at IS NULL
"""

# Full dump: ordered by id, which is the INTEGER PRIMARY KEY, i.e. the rowid
# -- SQLite's own table B-tree.  Rows come out in index order for free, so
# there is no temp sort and no second pass over the table.
LIST_LOANS_QUERY = """
SELECT id, copy_id, member_id, borrowed_at, due_at, returned_at
FROM loans
ORDER BY id
"""


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(
    conn: sqlite3.Connection,
    *,
    with_open_loan_index: bool = True,
    with_current_loans_index: bool = True,
) -> None:
    """Create tables (and, optionally, the secondary indexes).

    The two index flags exist so the benchmark script can build the exact
    \"before\" state (tables only) and the shipped \"after\" state.
    """
    conn.executescript(SCHEMA_SQL)
    if with_open_loan_index:
        conn.execute(OPEN_LOAN_INDEX_SQL)
    if with_current_loans_index:
        conn.execute(CURRENT_LOANS_INDEX_SQL)
    conn.commit()


def reset_db(
    conn: sqlite3.Connection,
    *,
    with_open_loan_index: bool = True,
    with_current_loans_index: bool = True,
) -> None:
    for table in ("loans", "copies", "members", "books"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    for index in ("ux_loans_open_copy", "ix_loans_member_open_borrowed"):
        conn.execute(f"DROP INDEX IF EXISTS {index}")
    conn.commit()
    init_db(
        conn,
        with_open_loan_index=with_open_loan_index,
        with_current_loans_index=with_current_loans_index,
    )
