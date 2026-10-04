"""Catalog basics: books with copies, availability tracking."""

from __future__ import annotations

from tests.conftest import copy_ids, lend, make_book, make_member


def test_book_created_with_copies_and_availability(client, conn):
    book = make_book(client, copy_count=2, isbn="ISBN-AVAIL")

    assert book["copies_total"] == 2
    assert book["copies_available"] == 2

    member = make_member(client, email="avail@library.example")
    status, _ = lend(client, copy_ids(conn, book["id"])[0], member["id"])
    assert status == 201

    listed = {b["id"]: b for b in client.get("/books").json()}
    assert listed[book["id"]]["copies_total"] == 2
    assert listed[book["id"]]["copies_available"] == 1


def test_duplicate_isbn_conflicts(client):
    make_book(client, isbn="ISBN-DUP")
    resp = client.post("/books", json={
        "title": "Other", "author": "X", "isbn": "ISBN-DUP",
        "copy_count": 1,
    })
    assert resp.status_code == 409


def test_duplicate_email_conflicts(client):
    client.post("/members", json={"name": "A", "email": "dup@library.example"})
    resp = client.post("/members",
                       json={"name": "B", "email": "dup@library.example"})
    assert resp.status_code == 409


def test_unknown_member_404(client):
    assert client.get("/members/999999").status_code == 404
