"""Deterministic seed data: books, copies, members and 10 000+ loans.

Run with::

    uv run python -m app.seed            # resets data/library.db, seeds defaults
    uv run python -m app.seed --db /tmp/x.db

The secondary indexes are created *after* the bulk load (the usual way to
load a warehouse), which doubles as a self-check: if the generator ever
produced two open loans for one copy, ``CREATE UNIQUE INDEX ux_loans_open_copy``
would fail and the script would die.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
from datetime import timedelta

from . import db
from .dates import LOAN_PERIOD, iso, utcnow

_ADJECTIVES = [
    "Silent", "Hidden", "Broken", "Golden", "Iron", "Quiet", "Restless",
    "Midnight", "Emerald", "Hollow", "Winter", "Secret", "Burning", "Paper",
    "Glass", "Northern", "Last", "Ancient", "Electric", "Fading",
]
_NOUNS = [
    "Harbor", "King", "Garden", "Machine", "River", "Signal", "Castle",
    "Compass", "Orchard", "Mirror", "Frontier", "Lantern", "Empire", "Tide",
    "Archive", "Summit", "Circuit", "Voyage", "Citadel", "Current",
]
_FIRST = [
    "Ada", "Ben", "Clara", "Dmitri", "Elena", "Farid", "Grace", "Hana",
    "Ines", "Jonas", "Keiko", "Liam", "Maya", "Nils", "Olga", "Priya",
    "Quinn", "Rosa", "Sam", "Tariq",
]
_LAST = [
    "Alvarez", "Baker", "Chen", "Duarte", "Eriksen", "Fontaine", "Gupta",
    "Haddad", "Ivanov", "Jensen", "Kowalski", "Lindqvist", "Moreau",
    "Novak", "Okafor", "Petrov", "Quintana", "Rossi", "Silva", "Tanaka",
]


def seed(
    conn: sqlite3.Connection,
    *,
    n_books: int = 200,
    n_members: int = 300,
    n_loans: int = 10_000,
    n_open: int = 500,
    copies_per_book: int = 3,
    seed_value: int = 42,
) -> dict[str, int]:
    rng = random.Random(seed_value)

    # Tables only: indexes go on after the load.
    db.reset_db(conn, with_open_loan_index=False, with_current_loans_index=False)

    books = [
        (i + 1,
         f"{rng.choice(_ADJECTIVES)} {rng.choice(_NOUNS)}",
         f"{rng.choice(_FIRST)} {rng.choice(_LAST)}",
         f"ISBN-{i + 1:06d}",
         rng.randint(1950, 2025))
        for i in range(n_books)
    ]
    conn.executemany(
        "INSERT INTO books (id, title, author, isbn, published_year) "
        "VALUES (?, ?, ?, ?, ?)",
        books,
    )

    copies = [
        ((book_id - 1) * copies_per_book + k + 1, book_id)
        for book_id in range(1, n_books + 1)
        for k in range(copies_per_book)
    ]
    conn.executemany("INSERT INTO copies (id, book_id) VALUES (?, ?)", copies)

    now = utcnow()
    members = [
        (i + 1, f"{rng.choice(_FIRST)} {rng.choice(_LAST)}",
         f"member{i + 1}@library.example",
         iso(now.replace(year=now.year - rng.randint(0, 8))))
        for i in range(n_members)
    ]
    conn.executemany(
        "INSERT INTO members (id, name, email, joined_at) VALUES (?, ?, ?, ?)",
        members,
    )

    # Closed history: every loan ended at least 60 days ago (borrowed between
    # 1000 and 90 days ago, duration 1-30 days).
    closed = []
    for _ in range(n_loans):
        borrowed = now - timedelta(days=rng.randint(90, 1000))
        returned = borrowed + timedelta(days=rng.randint(1, 30))
        closed.append((
            rng.randint(1, len(copies)),
            rng.randint(1, n_members),
            iso(borrowed),
            iso(borrowed + LOAN_PERIOD),
            iso(returned),
        ))
    conn.executemany(
        "INSERT INTO loans (copy_id, member_id, borrowed_at, due_at, "
        "returned_at) VALUES (?, ?, ?, ?, ?)",
        closed,
    )

    # Open loans: one per distinct copy, all started within the last 50 days,
    # i.e. after every closed loan above.  Distinct copies by construction, so
    # the invariant holds before the unique index is even built.
    open_copy_ids = rng.sample([c[0] for c in copies], n_open)
    borrowed_rows = []
    for copy_id in open_copy_ids:
        borrowed = now - timedelta(
            days=rng.randint(0, 50), seconds=rng.randint(0, 86_399)
        )
        borrowed_rows.append((
            copy_id, rng.randint(1, n_members),
            iso(borrowed), iso(borrowed + LOAN_PERIOD), None,
        ))
    conn.executemany(
        "INSERT INTO loans (copy_id, member_id, borrowed_at, due_at, "
        "returned_at) VALUES (?, ?, ?, ?, ?)",
        borrowed_rows,
    )

    # Indexes last: bulk-load first, then index.  The unique index doubles as
    # an invariant self-check on the generator above.
    conn.execute(db.OPEN_LOAN_INDEX_SQL)
    conn.execute(db.CURRENT_LOANS_INDEX_SQL)
    conn.commit()

    total = conn.execute("SELECT COUNT(*) FROM loans").fetchone()[0]
    open_count = conn.execute(
        "SELECT COUNT(*) FROM loans WHERE returned_at IS NULL"
    ).fetchone()[0]
    return {
        "books": len(books),
        "copies": len(copies),
        "members": len(members),
        "loans": total,
        "open_loans": open_count,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the library database")
    parser.add_argument("--db", default=None, help="path to the SQLite file")
    parser.add_argument("--loans", type=int, default=10_000,
                        help="number of closed loans (default 10000)")
    parser.add_argument("--open", type=int, default=500,
                        help="number of open loans (default 500)")
    parser.add_argument("--seed-value", type=int, default=42)
    args = parser.parse_args()

    conn = db.connect(args.db)
    try:
        summary = seed(
            conn,
            n_loans=args.loans,
            n_open=args.open,
            seed_value=args.seed_value,
        )
    finally:
        conn.close()
    print("Seeded:", ", ".join(f"{k}={v}" for k, v in summary.items()))


if __name__ == "__main__":
    main()
