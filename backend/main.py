"""FastAPI application: document endpoints, Q&A endpoint, static frontend."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import List, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import ingest, qa, store, summary
from .config import (
    FRONTEND_DIR,
    GROQ_MODEL,
    GROQ_SUMMARY_MODEL,
    configured_providers,
    MAX_UPLOAD_BYTES,
    SUMMARY_AUTO_RUN,
    SUPPORTED_EXTENSIONS,
    llm_configured,
)
from .models import (
    AskRequest,
    AskResponse,
    DocumentOut,
    SummaryCard,
    SummaryCardRequest,
    SummaryQuestionOut,
    SummaryRequest,
    SummaryResponse,
    UploadResponse,
    UploadResult,
)

logger = logging.getLogger("provision")


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    if not llm_configured():
        # A warning, not a failure: upload and listing work without a key.
        logger.warning(
            "GROQ_API_KEY is not set — document Q&A will return an error "
            "until it is added to .env."
        )
    yield


app = FastAPI(
    title="Provision",
    description="Contract obligation intelligence — Phase 1: documents and grounded Q&A.",
    version="0.1.0",
    lifespan=lifespan,
)


# ── Error shape ──────────────────────────────────────────────────────────────
# Every failure reaches the UI as {"error": "...message for a human..."} so the
# frontend has exactly one thing to read.


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    message = detail if isinstance(detail, str) else str(detail)
    return JSONResponse(status_code=exc.status_code, content={"error": message})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # FastAPI's default body is a list of field errors; the UI only shows a
    # sentence, so flatten it to the first readable problem.
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(part) for part in first.get("loc", ()) if part != "body")
    message = first.get("msg", "The request was not valid.")
    return JSONResponse(
        status_code=422,
        content={"error": f"{field}: {message}" if field else message},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Log the detail server-side; return something safe and actionable.
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "Something went wrong handling that request."},
    )


# ── Health ───────────────────────────────────────────────────────────────────


@app.get("/api/health")
def health() -> dict:
    providers = configured_providers()
    return {
        "status": "ok",
        # Tried in order; the first with a key answers, the rest are fallbacks.
        "providers": providers,
        "provider": providers[0] if providers else None,
        "model": GROQ_MODEL,  # chat Q&A (Groq path)
        "summary_model": GROQ_SUMMARY_MODEL,  # auto-summary cards (Groq path)
        "llm_configured": llm_configured(),
    }


# ── Documents ────────────────────────────────────────────────────────────────


@app.post("/api/documents", response_model=UploadResponse)
async def upload_documents(files: List[UploadFile] = File(...)) -> UploadResponse:
    """Upload one or more PDF/DOCX files: parse, store, index by page.

    Files are processed independently — one unreadable file does not discard
    the others. Per-file outcomes come back in `results`.
    """
    if not files:
        raise HTTPException(status_code=400, detail="No files were uploaded.")

    results: List[UploadResult] = []
    for upload in files:
        filename = upload.filename or "untitled"
        try:
            data = await upload.read()
            document = store.save_document(filename, upload.content_type, data)
        except ingest.IngestError as exc:
            # Expected, user-fixable: wrong type, empty, corrupt, too large.
            results.append(UploadResult(filename=filename, ok=False, error=str(exc)))
        except Exception:
            logger.exception("Failed to store upload %s", filename)
            results.append(
                UploadResult(
                    filename=filename,
                    ok=False,
                    error=f"{filename}: could not be stored.",
                )
            )
        else:
            results.append(
                UploadResult(
                    filename=filename, ok=True, document=DocumentOut.from_document(document)
                )
            )
        finally:
            await upload.close()

    stored = [result.document for result in results if result.document is not None]
    if not stored:
        # Nothing succeeded — surface the first reason with a 400 so the UI can
        # show it as a failure rather than an empty success.
        first_error = next(
            (result.error for result in results if result.error), "Upload failed."
        )
        raise HTTPException(status_code=400, detail=first_error)

    return UploadResponse(documents=stored, results=results)


@app.get("/api/documents", response_model=List[DocumentOut])
def list_documents(status: Optional[str] = None) -> List[DocumentOut]:
    """Uploaded documents with metadata, newest first.

    `?status=active` or `?status=finalized` filters by lifecycle stage. Omitted,
    it returns everything, which is the behaviour callers had before the
    lifecycle existed.
    """
    if status and status not in {"active", "finalized"}:
        raise HTTPException(
            status_code=400, detail="status must be 'active' or 'finalized'."
        )
    return [DocumentOut.from_document(d) for d in store.list_documents(status)]


@app.post("/api/documents/{document_id}/finalize", response_model=DocumentOut)
def finalize_document(document_id: str) -> DocumentOut:
    """Mark a document as signed and move it into the progress timeline.

    Nothing is deleted: the stored file and its page text stay exactly where
    they were, so the contract remains fully answerable afterwards.
    """
    document = store.set_document_status(document_id, "finalized")
    if document is None:
        raise HTTPException(status_code=404, detail="That document no longer exists.")
    return DocumentOut.from_document(document)


@app.post("/api/documents/{document_id}/reopen", response_model=DocumentOut)
def reopen_document(document_id: str) -> DocumentOut:
    """Send a finalized document back to the active workspace."""
    document = store.set_document_status(document_id, "active")
    if document is None:
        raise HTTPException(status_code=404, detail="That document no longer exists.")
    return DocumentOut.from_document(document)


@app.delete("/api/documents/{document_id}")
def delete_document(document_id: str) -> dict:
    """Delete a document, its page text and its stored file."""
    if not store.delete_document(document_id):
        raise HTTPException(status_code=404, detail="That document no longer exists.")
    return {"deleted": document_id}


# ── Q&A ──────────────────────────────────────────────────────────────────────


@app.post("/api/ask", response_model=AskResponse)
def ask(request: AskRequest) -> AskResponse:
    """Answer a question strictly from the uploaded documents, with citations.

    Optionally scoped to `document_ids`; otherwise every document is searched.
    """
    try:
        return qa.answer_question(request.question, request.document_ids)
    except qa.MissingApiKey as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (qa.NoDocuments, qa.InvalidQuestion) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except qa.QAError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# ── Auto-summary panel ───────────────────────────────────────────────────────
# Added in Phase 1.5, alongside /api/ask — not in place of it. Every route here
# runs the same qa.answer_question the chat uses; there is no second engine.
# These answers are LLM summaries for orientation, not verified obligations.


def _summary_http_error(exc: qa.QAError) -> HTTPException:
    """Map a Q&A failure onto the same status codes /api/ask already uses."""
    if isinstance(exc, qa.MissingApiKey):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, (qa.NoDocuments, qa.InvalidQuestion, summary.UnknownSummaryQuestion)):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=502, detail=str(exc))


@app.get("/api/summary/questions", response_model=List[SummaryQuestionOut])
def summary_questions() -> List[SummaryQuestionOut]:
    """The preset question list, so the UI can render headings before answers."""
    return [
        SummaryQuestionOut(key=item.key, heading=item.heading, question=item.question)
        for item in summary.SUMMARY_QUESTIONS
    ]


@app.post("/api/summary/card", response_model=SummaryCard)
def summary_card(request: SummaryCardRequest) -> SummaryCard:
    """Answer one preset question about one document.

    One card per request so the panel can fill in progressively instead of
    blocking on all six.
    """
    try:
        return summary.answer_card(request.document_id, request.key)
    except qa.QAError as exc:
        raise _summary_http_error(exc) from exc


@app.post("/api/summary", response_model=SummaryResponse)
def summarise(request: SummaryRequest) -> SummaryResponse:
    """Every preset question for one document, in one call.

    Convenience for scripts and testing; the UI uses /api/summary/card so it can
    show progress. Individual card failures come back on the card, not as a
    request-level error.
    """
    try:
        return summary.summarise_document(request.document_id)
    except qa.QAError as exc:
        raise _summary_http_error(exc) from exc


# ── Limits, for the UI ───────────────────────────────────────────────────────


@app.get("/api/config")
def client_config() -> dict:
    """What the frontend needs to validate a file before uploading it."""
    return {
        "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "llm_configured": llm_configured(),
        # When false the panel waits for the user to press "Generate summary"
        # instead of firing six calls the moment a document is uploaded.
        "summary_auto_run": SUMMARY_AUTO_RUN,
    }


# ── Frontend ─────────────────────────────────────────────────────────────────
# Mounted last so it never shadows an /api route.


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
