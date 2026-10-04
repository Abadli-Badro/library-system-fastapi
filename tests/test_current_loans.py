"""GET /members/{id}/loans/current: ordering, payload, and the seeded data."""

from __future__ import annotations

from app import db
from app.seed import seed
from tests.conftest import copy_ids, lend, make_book, make_member


def _setup_member_with_loans(client, conn):
    member = make_member(client, email="reader@library.example")
    other = make_member(client, email="other@library.example")

    alpha = make_book(client, copy_count=1, title="Alpha",
                      isbn="ISBN-A")
    beta = make_book(client, copy_count=2, title="Beta", isbn="ISBN-B")
    alpha_copy, = copy_ids(conn, alpha["id"])
    beta_copy_1, beta_copy_2 = copy_ids(conn, beta["id"])

    # earlier loan, kept open
    status, loan_early = lend(client, alpha_copy, member["id"],
                              borrowed_at="2026-01-01T09:00:00")
    assert status == 201
    # most recent loan, kept open
    status, loan_recent = lend(client, beta_copy_1, member["id"],
                               borrowed_at="2026-03-01T09:00:00")
    assert status == 201
    # middle loan, but returned -> must not appear
    status, loan_returned = lend(client, beta_copy_2, member["id"],
                                 borrowed_at="2026-02-01T09:00:00")
    assert status == 201
    assert client.post(
        f"/loans/{loan_returned['id']}/return"
    ).status_code == 200
    # another member's open loan -> must not appear either
    gamma = make_book(client, copy_count=1, title="Gamma", isbn="ISBN-C")
    status, _ = lend(client, copy_ids(conn, gamma["id"])[0], other["id"],
                     borrowed_at="2026-04-01T09:00:00")
    assert status == 201

    return member, loan_early, loan_recent


def test_current_loans_newest_first_with_title_and_due_date(client, conn):
    member, loan_early, loan_recent = _setup_member_with_loans(client, conn)

    resp = client.get(f"/members/{member['id']}/loans/current")
    assert resp.status_code == 200
    loans = resp.json()

    assert [l["loan_id"] for l in loans] == [
        loan_recent["id"], loan_early["id"],
    ], "most recently borrowed first, returned loans excluded"

    assert set(loans[0]) == {"loan_id", "title", "borrowed_at", "due_at"}
    assert loans[0]["title"] == "Beta"
    assert loans[1]["title"] == "Alpha"

    # default loan period is 21 days
    assert loans[1]["borrowed_at"] == "2026-01-01T09:00:00"
    assert loans[1]["due_at"] == "2026-01-22T09:00:00"


def test_current_loans_unknown_member_404(client):
    assert client.get("/members/424242/loans/current").status_code == 404


def test_seed_creates_10k_loans_and_invariant_holds(conn):
    summary = seed(conn)

    assert summary["loans"] >= 10_000
    assert summary["open_loans"] >= 500

    worst = conn.execute(
        "SELECT COALESCE(MAX(n), 0) FROM ("
        "  SELECT COUNT(*) AS n FROM loans WHERE returned_at IS NULL"
        "  GROUP BY copy_id)"
    ).fetchone()[0]
    assert worst == 1, "no copy may have two open loans after seeding"


def test_query_plan_uses_the_partial_index(conn):
    rows = conn.execute(
        "EXPLAIN QUERY PLAN " + db.CURRENT_LOANS_QUERY, (1,)
    ).fetchall()
    details = " | ".join(r["detail"] for r in rows)
    assert "ix_loans_member_open_borrowed" in details, details
    assert "SCAN loans" not in details, details
