"""GET /loans: full dump of every loan, ordered by the rowid index."""

from __future__ import annotations

from app import db
from app.seed import seed
from tests.conftest import copy_ids, lend, make_book, make_member


def test_list_loans_returns_all_rows_in_id_order(client, conn):
    member = make_member(client, email="dump@library.example")
    book = make_book(client, copy_count=2, isbn="ISBN-DUMP")
    first, second = copy_ids(conn, book["id"])

    _, loan_a = lend(client, first, member["id"])
    _, loan_b = lend(client, second, member["id"])
    assert client.post(f"/loans/{loan_a['id']}/return").status_code == 200

    loans = client.get("/loans").json()

    assert [l["id"] for l in loans] == sorted(l["id"] for l in loans)
    assert {l["id"] for l in loans} >= {loan_a["id"], loan_b["id"]}
    by_id = {l["id"]: l for l in loans}
    assert set(by_id[loan_a["id"]]) == {
        "id", "copy_id", "member_id", "borrowed_at", "due_at", "returned_at",
    }
    assert by_id[loan_a["id"]]["returned_at"] is not None
    assert by_id[loan_b["id"]]["returned_at"] is None


def test_list_loans_returns_every_seeded_row(client, conn):
    summary = seed(conn)

    loans = client.get("/loans").json()

    assert len(loans) == summary["loans"]
    assert len(loans) >= 10_000
    assert [l["id"] for l in loans] == list(range(1, len(loans) + 1))


def test_list_loans_query_needs_no_temp_sort(conn):
    rows = conn.execute("EXPLAIN QUERY PLAN " + db.LIST_LOANS_QUERY).fetchall()
    details = " | ".join(r["detail"] for r in rows)
    assert "USE TEMP B-TREE" not in details, details
    assert "ix_loans" not in details, details
