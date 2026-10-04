from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import create_app


@pytest.fixture
def db_file(tmp_path):
    path = tmp_path / "test.db"
    conn = db.connect(path)
    db.init_db(conn)
    conn.close()
    return path


@pytest.fixture
def conn(db_file):
    connection = db.connect(db_file)
    yield connection
    connection.close()


@pytest.fixture
def client(db_file):
    return TestClient(create_app(str(db_file)))


def make_book(client: TestClient, *, copy_count: int = 1,
              title: str = "Silent Harbor", author: str = "Ada Lovelace",
              isbn: str = "ISBN-TEST-1") -> dict:
    resp = client.post("/books", json={
        "title": title, "author": author, "isbn": isbn,
        "published_year": 1999, "copy_count": copy_count,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def make_member(client: TestClient, *, name: str = "Ada Lovelace",
                email: str = "ada@library.example") -> dict:
    resp = client.post("/members", json={"name": name, "email": email})
    assert resp.status_code == 201, resp.text
    return resp.json()


def lend(client: TestClient, copy_id: int, member_id: int, *,
         borrowed_at: str | None = None) -> tuple[int, dict]:
    payload: dict = {"copy_id": copy_id, "member_id": member_id}
    if borrowed_at is not None:
        payload["borrowed_at"] = borrowed_at
    resp = client.post("/loans", json=payload)
    return resp.status_code, resp.json()


def copy_ids(conn, book_id: int) -> list[int]:
    return [r["id"] for r in conn.execute(
        "SELECT id FROM copies WHERE book_id = ? ORDER BY id", (book_id,)
    )]
