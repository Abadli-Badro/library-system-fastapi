from __future__ import annotations

import sqlite3
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from . import db
from .dates import LOAN_PERIOD, iso, utcnow
from .schemas import (
    BookCreate,
    BookOut,
    CurrentLoanOut,
    LendRequest,
    LoanOut,
    MemberCreate,
    MemberOut,
)


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(title="library-system-fastapi", version="0.1.0")
    app.state.db_path = db_path

    def get_db():
        conn = db.connect(app.state.db_path)
        try:
            yield conn
        finally:
            conn.close()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # ------------------------------------------------------------------ books
    @app.post("/books", status_code=201, response_model=BookOut)
    def create_book(payload: BookCreate, conn: sqlite3.Connection = Depends(get_db)) -> dict:
        try:
            cur = conn.execute(
                "INSERT INTO books (title, author, isbn, published_year) "
                "VALUES (?, ?, ?, ?)",
                (payload.title, payload.author, payload.isbn,
                 payload.published_year),
            )
            book_id = cur.lastrowid
            conn.executemany(
                "INSERT INTO copies (book_id) VALUES (?)",
                [(book_id,)] * payload.copy_count,
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise HTTPException(status_code=409, detail=f"conflict: {exc}") from exc
        return _book_out(conn, book_id)

    @app.get("/books", response_model=list[BookOut])
    def list_books(conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
        rows = conn.execute(
            """
            SELECT b.id, b.title, b.author, b.isbn, b.published_year,
                   COUNT(DISTINCT c.id) AS copies_total,
                   COUNT(DISTINCT c.id)
                     - COUNT(DISTINCT CASE WHEN l.id IS NOT NULL
                                           THEN l.copy_id END)
                     AS copies_available
            FROM books b
            LEFT JOIN copies c ON c.book_id = b.id
            LEFT JOIN loans l ON l.copy_id = c.id AND l.returned_at IS NULL
            GROUP BY b.id
            ORDER BY b.id
            """
        ).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- members
    @app.post("/members", status_code=201, response_model=MemberOut)
    def create_member(payload: MemberCreate,
                      conn: sqlite3.Connection = Depends(get_db)) -> dict:
        try:
            cur = conn.execute(
                "INSERT INTO members (name, email, joined_at) VALUES (?, ?, ?)",
                (payload.name, payload.email, iso(utcnow())),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise HTTPException(status_code=409, detail=f"conflict: {exc}") from exc
        return dict(conn.execute(
            "SELECT id, name, email, joined_at FROM members WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone())

    @app.get("/members/{member_id}", response_model=MemberOut)
    def get_member(member_id: int, conn: sqlite3.Connection = Depends(get_db)) -> dict:
        row = conn.execute(
            "SELECT id, name, email, joined_at FROM members WHERE id = ?",
            (member_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="member not found")
        return dict(row)

    # ------------------------------------------------------------------ loans
    @app.post("/loans")
    def lend_copy(payload: LendRequest,
                  conn: sqlite3.Connection = Depends(get_db)) -> JSONResponse:
        """Lend a copy to a member.

        Idempotent: if this copy is already out to *the same* member, nothing
        is written and the existing open loan is returned with 200.  If it is
        out to somebody else the request is a 409.

        The check below is only for idempotency/UX -- the hard guarantee is
        the partial unique index ``ux_loans_open_copy``.  If a concurrent
        lend wins the race, the INSERT raises IntegrityError and we fall
        through to the same 200-or-409 decision based on what is committed.
        """
        member = conn.execute(
            "SELECT id FROM members WHERE id = ?", (payload.member_id,)
        ).fetchone()
        if member is None:
            raise HTTPException(status_code=404, detail="member not found")
        copy = conn.execute(
            "SELECT id FROM copies WHERE id = ?", (payload.copy_id,)
        ).fetchone()
        if copy is None:
            raise HTTPException(status_code=404, detail="copy not found")

        existing = conn.execute(
            db.OPEN_LOAN_BY_COPY_QUERY, (payload.copy_id,)
        ).fetchone()
        if existing is not None:
            return _lend_decision(conn, existing, payload.member_id)

        borrowed = payload.borrowed_at or utcnow()
        due = payload.due_at or (borrowed + LOAN_PERIOD)
        try:
            conn.execute(
                "INSERT INTO loans (copy_id, member_id, borrowed_at, due_at) "
                "VALUES (?, ?, ?, ?)",
                (payload.copy_id, payload.member_id, iso(borrowed), iso(due)),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            # Either the unique open-loan index (a concurrent lend) or a FK.
            conn.rollback()
            existing = conn.execute(
                db.OPEN_LOAN_BY_COPY_QUERY, (payload.copy_id,)
            ).fetchone()
            if existing is None:
                raise HTTPException(
                    status_code=409, detail="copy cannot be lent"
                )
            return _lend_decision(conn, existing, payload.member_id)

        row = conn.execute(
            db.OPEN_LOAN_BY_COPY_QUERY, (payload.copy_id,)
        ).fetchone()
        return JSONResponse(status_code=201, content=_loan_content(row))

    @app.post("/loans/{loan_id}/return", response_model=LoanOut)
    def return_loan(loan_id: int,
                    conn: sqlite3.Connection = Depends(get_db)) -> JSONResponse:
        row = conn.execute(
            "SELECT * FROM loans WHERE id = ?", (loan_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="loan not found")
        if row["returned_at"] is not None:
            # Already returned: idempotent replay, no write.
            return JSONResponse(status_code=200, content=_loan_content(row))
        conn.execute(
            "UPDATE loans SET returned_at = ? WHERE id = ? AND returned_at IS NULL",
            (iso(utcnow()), loan_id),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM loans WHERE id = ?", (loan_id,)
        ).fetchone()
        return JSONResponse(status_code=200, content=_loan_content(row))

    @app.get("/loans", response_model=list[LoanOut])
    def list_loans(conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
        """Every loan in the house, in id (insertion) order.

        ``ORDER BY id`` rides SQLite's INTEGER PRIMARY KEY (the rowid table
        B-tree itself), so the response is produced straight from the table
        index: no temp B-tree, no re-sort, single pass.
        """
        rows = conn.execute(db.LIST_LOANS_QUERY).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------ the endpoint that matters
    @app.get("/members/{member_id}/loans/current",
             response_model=list[CurrentLoanOut])
    def current_loans(member_id: int,
                      conn: sqlite3.Connection = Depends(get_db)) -> list[dict]:
        member = conn.execute(
            "SELECT id FROM members WHERE id = ?", (member_id,)
        ).fetchone()
        if member is None:
            raise HTTPException(status_code=404, detail="member not found")
        rows = conn.execute(db.CURRENT_LOANS_QUERY, (member_id,)).fetchall()
        return [dict(r) for r in rows]

    return app


def _lend_decision(conn: sqlite3.Connection, existing: sqlite3.Row,
                   member_id: int) -> JSONResponse:
    if existing["member_id"] == member_id:
        loan = conn.execute(
            "SELECT * FROM loans WHERE id = ?", (existing["id"],)
        ).fetchone()
        return JSONResponse(status_code=200, content=_loan_content(loan))
    raise HTTPException(
        status_code=409,
        detail=(f"copy is already on loan to member "
                f"{existing['member_id']}"),
    )


def _loan_content(row: sqlite3.Row) -> dict:
    return LoanOut.model_validate(dict(row)).model_dump(mode="json")


def _book_out(conn: sqlite3.Connection, book_id: int) -> dict:
    row = conn.execute(
        """
        SELECT b.id, b.title, b.author, b.isbn, b.published_year,
               COUNT(DISTINCT c.id) AS copies_total,
               COUNT(DISTINCT c.id)
                 - COUNT(DISTINCT CASE WHEN l.id IS NOT NULL
                                       THEN l.copy_id END)
                 AS copies_available
        FROM books b
        LEFT JOIN copies c ON c.book_id = b.id
        LEFT JOIN loans l ON l.copy_id = c.id AND l.returned_at IS NULL
        WHERE b.id = ?
        GROUP BY b.id
        """,
        (book_id,),
    ).fetchone()
    return dict(row)


app = create_app()
