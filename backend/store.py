"""Persistence: SQLite (via SQLModel) for text and metadata, disk for the files.

Uploaded bytes are written under data/uploads/ named by document id, so a
hostile or duplicated filename can never escape the directory or collide.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Sequence

from sqlalchemy import delete as sa_delete
from sqlmodel import Session, SQLModel, create_engine, select

from . import ingest
from .config import DATABASE_URL, MAX_UPLOAD_BYTES, UPLOAD_DIR, ensure_directories
from .models import Document, Page

# check_same_thread=False: FastAPI serves requests from a threadpool, and each
# request opens its own short-lived Session.
engine = create_engine(DATABASE_URL, echo=False, connect_args={"check_same_thread": False})


def init_db() -> None:
    """Create the data directories and tables. Safe to call on every startup."""
    ensure_directories()
    SQLModel.metadata.create_all(engine)


def get_session() -> Session:
    return Session(engine)


# ── Writes ───────────────────────────────────────────────────────────────────


class UploadTooLarge(ingest.IngestError):
    pass


def save_document(filename: str, content_type: Optional[str], data: bytes) -> Document:
    """Parse, store and index one uploaded file.

    Parsing happens before anything is written, so a document that cannot be
    read leaves no file on disk and no row in the database.
    """
    display_name = _safe_display_name(filename)
    if len(data) > MAX_UPLOAD_BYTES:
        raise UploadTooLarge(
            f"{display_name}: file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB."
        )

    pages = ingest.parse_document(display_name, data)
    extension = Path(display_name).suffix.lower()

    document = Document(
        filename=display_name,
        stored_filename="",  # set below, once the generated id is known
        extension=extension,
        content_type=content_type,
        size_bytes=len(data),
        page_count=len(pages),
        char_count=sum(len(page.text) for page in pages),
    )
    document.stored_filename = f"{document.id}{extension}"

    ensure_directories()
    destination = UPLOAD_DIR / document.stored_filename
    destination.write_bytes(data)

    try:
        with get_session() as session:
            session.add(document)
            for page in pages:
                session.add(
                    Page(
                        document_id=document.id,
                        page_number=page.page_number,
                        text=page.text,
                    )
                )
            session.commit()
            session.refresh(document)
    except Exception:
        # Don't leave an orphaned file behind if the transaction failed.
        destination.unlink(missing_ok=True)
        raise

    return document


def delete_document(document_id: str) -> bool:
    """Remove a document, its pages and its file. False if it wasn't there."""
    with get_session() as session:
        document = session.get(Document, document_id)
        if document is None:
            return False
        stored_filename = document.stored_filename
        session.exec(sa_delete(Page).where(Page.document_id == document_id))
        session.delete(document)
        session.commit()

    if stored_filename:
        # A missing file is not an error — the record is what we promised to remove.
        (UPLOAD_DIR / stored_filename).unlink(missing_ok=True)
    return True


# ── Reads ────────────────────────────────────────────────────────────────────


def list_documents() -> List[Document]:
    """All documents, newest first."""
    with get_session() as session:
        return list(session.exec(select(Document).order_by(Document.uploaded_at.desc())))


def get_document(document_id: str) -> Optional[Document]:
    with get_session() as session:
        return session.get(Document, document_id)


def get_documents(document_ids: Sequence[str]) -> List[Document]:
    """Fetch the given documents, preserving the caller's order."""
    if not document_ids:
        return []
    with get_session() as session:
        found = {
            document.id: document
            for document in session.exec(
                select(Document).where(Document.id.in_(list(document_ids)))
            )
        }
    return [found[doc_id] for doc_id in document_ids if doc_id in found]


def get_pages(document_id: str) -> List[Page]:
    """Pages of one document, in reading order."""
    with get_session() as session:
        return list(
            session.exec(
                select(Page)
                .where(Page.document_id == document_id)
                .order_by(Page.page_number)
            )
        )


def get_pages_for(document_ids: Sequence[str]) -> Dict[str, List[Page]]:
    """Pages for several documents at once, keyed by document id."""
    if not document_ids:
        return {}
    with get_session() as session:
        rows = session.exec(
            select(Page)
            .where(Page.document_id.in_(list(document_ids)))
            .order_by(Page.document_id, Page.page_number)
        )
        grouped: Dict[str, List[Page]] = {doc_id: [] for doc_id in document_ids}
        for page in rows:
            grouped.setdefault(page.document_id, []).append(page)
    return grouped


# ── Helpers ──────────────────────────────────────────────────────────────────


def _safe_display_name(filename: str) -> str:
    """Strip any path components a client may have sent with the filename."""
    name = Path(filename or "").name.strip()
    return name or "untitled"
