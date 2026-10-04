"""Double lending: idempotent replay at the API, hard guarantee in the DB."""

from __future__ import annotations

import sqlite3

import pytest

from tests.conftest import copy_ids, lend, make_book, make_member


def _open_loans_for_copy(conn, copy_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, member_id FROM loans "
        "WHERE copy_id = ? AND returned_at IS NULL",
        (copy_id,),
    ).fetchall()


def test_replay_same_member_is_idempotent(client, conn):
    book = make_book(client, copy_count=1)
    member = make_member(client)
    copy_id = copy_ids(conn, book["id"])[0]

    status1, loan1 = lend(client, copy_id, member["id"])
    status2, loan2 = lend(client, copy_id, member["id"])

    assert status1 == 201
    assert status2 == 200, "replaying the same lend must not be a conflict"
    assert loan1["id"] == loan2["id"], "replay returns the existing loan"
    assert len(_open_loans_for_copy(conn, copy_id)) == 1, "no second row"


def test_lend_to_different_member_is_conflict(client, conn):
    book = make_book(client, copy_count=1, isbn="ISBN-TEST-2")
    member_a = make_member(client, email="a@library.example")
    member_b = make_member(client, email="b@library.example")
    copy_id = copy_ids(conn, book["id"])[0]

    status1, _ = lend(client, copy_id, member_a["id"])
    status2, body2 = lend(client, copy_id, member_b["id"])

    assert status1 == 201
    assert status2 == 409
    assert "already on loan" in body2["detail"]
    open_rows = _open_loans_for_copy(conn, copy_id)
    assert len(open_rows) == 1
    assert open_rows[0]["member_id"] == member_a["id"]


def test_database_rejects_second_open_loan_without_app(client, conn):
    """Bypass the API entirely: even raw SQL cannot commit the violation."""
    book = make_book(client, copy_count=1, isbn="ISBN-TEST-3")
    member_a = make_member(client, email="raw-a@library.example")
    member_b = make_member(client, email="raw-b@library.example")
    copy_id = copy_ids(conn, book["id"])[0]

    status, _ = lend(client, copy_id, member_a["id"])
    assert status == 201

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO loans (copy_id, member_id, borrowed_at, due_at) "
            "VALUES (?, ?, '2026-01-01T09:00:00', '2026-01-22T09:00:00')",
            (copy_id, member_b["id"]),
        )
    conn.rollback()
    assert len(_open_loans_for_copy(conn, copy_id)) == 1


def test_return_frees_copy_for_relend(client, conn):
    book = make_book(client, copy_count=1, isbn="ISBN-TEST-4")
    member = make_member(client, email="relend@library.example")
    copy_id = copy_ids(conn, book["id"])[0]

    status1, loan1 = lend(client, copy_id, member["id"])
    assert status1 == 201

    resp = client.post(f"/loans/{loan1['id']}/return")
    assert resp.status_code == 200
    assert resp.json()["returned_at"] is not None

    status2, loan2 = lend(client, copy_id, member["id"])
    assert status2 == 201
    assert loan2["id"] != loan1["id"], "a new loan is created"
    assert len(_open_loans_for_copy(conn, copy_id)) == 1


def test_return_is_idempotent(client, conn):
    book = make_book(client, copy_count=1, isbn="ISBN-TEST-5")
    member = make_member(client, email="ret@library.example")
    copy_id = copy_ids(conn, book["id"])[0]

    _, loan = lend(client, copy_id, member["id"])
    first = client.post(f"/loans/{loan['id']}/return")
    second = client.post(f"/loans/{loan['id']}/return")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json() == second.json(), "replay returns the same payload"


def test_lending_unknown_entities_404(client):
    member = make_member(client, email="404@library.example")
    assert client.post("/loans", json={
        "copy_id": 999_999, "member_id": member["id"],
    }).status_code == 404
    assert client.post("/loans", json={
        "copy_id": 1, "member_id": 999_999,
    }).status_code == 404
