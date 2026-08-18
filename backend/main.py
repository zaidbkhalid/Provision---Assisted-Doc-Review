"""FastAPI application: document endpoints, Q&A endpoint, static frontend."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import List

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import ingest, qa, store
from .config import (
    FRONTEND_DIR,
    MAX_UPLOAD_BYTES,
    OPENAI_MODEL,
    SUPPORTED_EXTENSIONS,
    llm_configured,
)
from .models import AskRequest, AskResponse, DocumentOut, UploadResponse, UploadResult

logger = logging.getLogger("provision")


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.init_db()
    if not llm_configured():
        # A warning, not a failure: upload and listing work without a key.
        logger.warning(
            "OPENAI_API_KEY is not set — document Q&A will return an error "
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
    return {
        "status": "ok",
        "model": OPENAI_MODEL,
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
def list_documents() -> List[DocumentOut]:
    """All uploaded documents with their metadata, newest first."""
    return [DocumentOut.from_document(document) for document in store.list_documents()]


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


# ── Limits, for the UI ───────────────────────────────────────────────────────


@app.get("/api/config")
def client_config() -> dict:
    """What the frontend needs to validate a file before uploading it."""
    return {
        "supported_extensions": sorted(SUPPORTED_EXTENSIONS),
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "llm_configured": llm_configured(),
    }


# ── Frontend ─────────────────────────────────────────────────────────────────
# Mounted last so it never shadows an /api route.


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


if FRONTEND_DIR.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
