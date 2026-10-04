from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class BookCreate(BaseModel):
    title: str = Field(min_length=1)
    author: str = Field(min_length=1)
    isbn: str = Field(min_length=1)
    published_year: int | None = None
    copy_count: int = Field(default=1, ge=1, le=100)


class BookOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    author: str
    isbn: str
    published_year: int | None
    copies_total: int
    copies_available: int


class MemberCreate(BaseModel):
    name: str = Field(min_length=1)
    email: str = Field(min_length=3)


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    email: str
    joined_at: datetime


class LendRequest(BaseModel):
    copy_id: int
    member_id: int
    borrowed_at: datetime | None = None
    due_at: datetime | None = None


class LoanOut(BaseModel):
    id: int
    copy_id: int
    member_id: int
    borrowed_at: datetime
    due_at: datetime
    returned_at: datetime | None


class CurrentLoanOut(BaseModel):
    loan_id: int
    title: str
    borrowed_at: datetime
    due_at: datetime


class Message(BaseModel):
    detail: str
