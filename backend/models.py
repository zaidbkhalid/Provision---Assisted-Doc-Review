"""Database tables and API schemas.

The SQLModel tables are the storage shape; the plain Pydantic models below are
the API shape. They are kept separate on purpose so that adding a storage
column (or, later, an obligations table) doesn't silently change the contract
the frontend depends on.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Field, SQLModel


def _new_id() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Tables ───────────────────────────────────────────────────────────────────


class Document(SQLModel, table=True):
    """One uploaded file, after successful parsing."""

    __tablename__ = "documents"

    id: str = Field(default_factory=_new_id, primary_key=True)
    filename: str = Field(index=True)  # original name, as shown in the UI
    stored_filename: str  # name on disk under data/uploads/
    extension: str  # ".pdf" / ".docx", lowercased
    content_type: Optional[str] = None
    size_bytes: int = 0
    page_count: int = 0
    char_count: int = 0
    uploaded_at: datetime = Field(default_factory=_utcnow)
    # Lifecycle, added in Phase 1.7. "active" = still being reviewed in the
    # workspace; "finalized" = signed off and moved to the progress timeline.
    # Finalizing never deletes anything: the file and its pages stay in place so
    # the contract can still be questioned later.
    status: str = Field(default="active", index=True)
    finalized_at: Optional[datetime] = None


class Page(SQLModel, table=True):
    """Text of a single page, kept separate so answers can cite page numbers."""

    __tablename__ = "pages"

    id: Optional[int] = Field(default=None, primary_key=True)
    document_id: str = Field(foreign_key="documents.id", index=True)
    page_number: int  # 1-based, as a reader would count it
    text: str = ""


# ── API schemas ──────────────────────────────────────────────────────────────


class DocumentOut(BaseModel):
    """Document metadata as returned to the UI."""

    id: str
    filename: str
    extension: str
    size_bytes: int
    page_count: int
    char_count: int
    uploaded_at: datetime
    status: str = "active"
    finalized_at: Optional[datetime] = None

    @classmethod
    def from_document(cls, doc: Document) -> "DocumentOut":
        return cls(
            id=doc.id,
            filename=doc.filename,
            extension=doc.extension,
            size_bytes=doc.size_bytes,
            page_count=doc.page_count,
            char_count=doc.char_count,
            uploaded_at=doc.uploaded_at,
            status=doc.status or "active",
            finalized_at=doc.finalized_at,
        )


class UploadResult(BaseModel):
    """Outcome for one file in a multi-file upload."""

    filename: str
    ok: bool
    document: Optional[DocumentOut] = None
    error: Optional[str] = None


class UploadResponse(BaseModel):
    documents: List[DocumentOut] = PydanticField(default_factory=list)
    results: List[UploadResult] = PydanticField(default_factory=list)


class Citation(BaseModel):
    """Where a claim in an answer came from.

    `clause` is optional because not every source passage carries a numbered
    clause or heading — but document and page are always required, so every
    citation is checkable.
    """

    document_id: Optional[str] = None
    document: str  # filename, as displayed
    page: int
    clause: Optional[str] = None
    quote: Optional[str] = None  # short verbatim snippet supporting the claim


class AskRequest(BaseModel):
    question: str
    document_ids: Optional[List[str]] = None  # None / empty = all documents


class AskResponse(BaseModel):
    answer: str
    # False when the documents genuinely don't cover the question. The UI uses
    # this to render the honest "not covered" state rather than a normal answer.
    answered: bool = True
    citations: List[Citation] = PydanticField(default_factory=list)
    documents_searched: List[str] = PydanticField(default_factory=list)
    # Set when the context budget forced us to narrow the question's scope.
    scope_note: Optional[str] = None


class ErrorResponse(BaseModel):
    error: str
    detail: Optional[str] = None


# ── Auto-summary panel ───────────────────────────────────────────────────────
# Added in Phase 1.5. These describe LLM-generated summary cards, NOT verified
# structured obligations — see backend/summary.py for that distinction. The
# existing Ask schemas above are untouched and remain the chat contract.


class SummaryQuestionOut(BaseModel):
    """One preset question, for a UI that wants to render headings first."""

    key: str
    heading: str
    question: str


class SummaryCardRequest(BaseModel):
    document_id: str
    key: str  # which preset question, from SUMMARY_QUESTIONS


class SummaryRequest(BaseModel):
    document_id: str


class SummaryCard(BaseModel):
    """One answered preset question. Same citation type as a chat answer."""

    key: str
    heading: str
    question: str
    answer: Optional[str] = None
    answered: bool = True  # False when the document doesn't cover this topic
    citations: List[Citation] = PydanticField(default_factory=list)
    scope_note: Optional[str] = None
    error: Optional[str] = None  # set when this one card failed


class SummaryResponse(BaseModel):
    document_id: str
    document: str
    cards: List[SummaryCard] = PydanticField(default_factory=list)
    disclaimer: str
