"""Naive-UTC datetime helpers.

All timestamps are stored as naive UTC ISO-8601 strings (e.g.
``2026-10-04T09:30:00``) so lexicographic ordering in SQLite matches
chronological ordering and the CHECK constraints compare like with like.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

LOAN_PERIOD = timedelta(days=21)


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")
