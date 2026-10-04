"""Benchmark the current-loans query on 10 000+ seeded loans.

Builds a scratch database with tables only (no secondary indexes), seeds it,
then measures the query twice: before and after creating the production
indexes.  Prints a markdown block (EXPLAIN QUERY PLAN + timing) suitable for
pasting into the README.

Run:  uv run python scripts/measure_query.py [--runs 200] [--db PATH]
"""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.seed import seed  # noqa: E402


def explain(conn, member_id: int) -> list[str]:
    rows = conn.execute(
        "EXPLAIN QUERY PLAN " + db.CURRENT_LOANS_QUERY, (member_id,)
    ).fetchall()
    by_id = {r["id"]: r for r in rows}

    def depth_of(row) -> int:
        parent = row["parent"]
        return 0 if parent == 0 else depth_of(by_id[parent]) + 1

    return ["  " * depth_of(r) + r["detail"] for r in rows]


def time_query(conn, member_id: int, runs: int) -> tuple[int, dict[str, float]]:
    query = db.CURRENT_LOANS_QUERY
    for _ in range(20):  # warm the page cache for both phases
        conn.execute(query, (member_id,)).fetchall()
    samples: list[float] = []
    rows = 0
    for _ in range(runs):
        start = time.perf_counter()
        rows = len(conn.execute(query, (member_id,)).fetchall())
        samples.append(time.perf_counter() - start)
    return rows, {
        "mean_ms": statistics.fmean(samples) * 1000,
        "median_ms": statistics.median(samples) * 1000,
        "min_ms": min(samples) * 1000,
    }


def worst_member(conn) -> tuple[int, int]:
    row = conn.execute(
        "SELECT member_id, COUNT(*) AS n FROM loans "
        "WHERE returned_at IS NULL "
        "GROUP BY member_id ORDER BY n DESC, member_id LIMIT 1"
    ).fetchone()
    return row["member_id"], row["n"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=200)
    parser.add_argument("--loans", type=int, default=10_000)
    parser.add_argument("--open", type=int, default=500)
    parser.add_argument("--db", default=None, help="reuse a DB file instead "
                        "of a scratch one (seeded fresh either way)")
    args = parser.parse_args()

    if args.db:
        db_path = Path(args.db)
        cleanup = False
    else:
        tmp = tempfile.TemporaryDirectory(prefix="library-bench-")
        db_path = Path(tmp.name) / "bench.db"
        cleanup = True

    conn = db.connect(db_path)
    try:
        # seed() drops/recreates tables with NO secondary indexes, loads, and
        # then adds them -- so strip them again to get the "before" state.
        seed(conn, n_loans=args.loans, n_open=args.open)
        conn.execute("DROP INDEX IF EXISTS ix_loans_member_open_borrowed")
        conn.execute("DROP INDEX IF EXISTS ux_loans_open_copy")
        conn.commit()

        member_id, member_open = worst_member(conn)
        total = conn.execute("SELECT COUNT(*) FROM loans").fetchone()[0]
        total_open = conn.execute(
            "SELECT COUNT(*) FROM loans WHERE returned_at IS NULL"
        ).fetchone()[0]

        print(f"loans={total} (open={total_open}) | "
              f"member={member_id} (open loans={member_open}) | "
              f"runs={args.runs}\n")

        print("### Before \u2014 no secondary indexes\n")
        print("```")
        print("\n".join(explain(conn, member_id)))
        print("```")
        _, before = time_query(conn, member_id, args.runs)

        conn.execute(db.OPEN_LOAN_INDEX_SQL)
        conn.execute(db.CURRENT_LOANS_INDEX_SQL)
        conn.commit()

        print("\n### After \u2014 production indexes\n")
        print("```")
        print("\n".join(explain(conn, member_id)))
        print("```")
        rows, after = time_query(conn, member_id, args.runs)

        print(f"\n| phase | mean | median | min | rows |")
        print(f"|---|---|---|---|---|")
        print(f"| before | {before['mean_ms']:.3f} ms | "
              f"{before['median_ms']:.3f} ms | {before['min_ms']:.3f} ms | {rows} |")
        print(f"| after | {after['mean_ms']:.3f} ms | "
              f"{after['median_ms']:.3f} ms | {after['min_ms']:.3f} ms | {rows} |")
        speedup = before["mean_ms"] / after["mean_ms"]
        print(f"\nspeedup: {speedup:.1f}x")
    finally:
        conn.close()
        if cleanup:
            tmp.cleanup()


if __name__ == "__main__":
    main()
