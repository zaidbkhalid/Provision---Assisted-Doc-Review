"""Grounded question answering over the uploaded documents.

Deliberately no embeddings and no vector retrieval. Contracts are small enough
to put in the model's context whole, and doing so keeps cross-references intact
— clause 7.3 is meaningless without the cap in 7.4 and the schedule six pages
earlier, and chunk retrieval routinely returns one without the others.

The request is built as: stable instructions -> the full document text (one
input block) -> the question, last. OpenAI caches long prompt prefixes
automatically, so keeping the document text ahead of the volatile question, and
passing a cache key derived from the document set, means repeated questions
over the same documents reuse the cached prefix.
"""

from __future__ import annotations

import hashlib
import logging
from typing import List, Optional, Sequence, Tuple

import openai
from openai import OpenAI
from pydantic import BaseModel

from . import store
from .config import (
    ANSWER_MAX_TOKENS,
    MAX_CONTEXT_CHARS,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    OPENAI_REASONING_EFFORT,
)
from .models import AskResponse, Citation, Document

logger = logging.getLogger("provision.qa")


class QAError(Exception):
    """A failure with a message written for the end user."""


class MissingApiKey(QAError):
    pass


class NoDocuments(QAError):
    pass


class InvalidQuestion(QAError):
    pass


SYSTEM_PROMPT = """\
You are Provision, a contract analyst for a small B2B company. You answer \
questions about the company's own signed documents — contracts, MOUs, \
quotations and purchase orders.

Absolute rules:
1. Answer ONLY from the document text supplied in this request. You have no \
other knowledge of this company, its vendors, its figures or its dates.
2. Every substantive claim — every figure, date, percentage, deadline, party \
name and clause number — must come from the supplied text and must carry a \
citation to the document and page it came from.
3. Never invent or infer a clause number, amount, date or vendor name that is \
not written in the text. If a clause is unnumbered, cite the heading, or leave \
the clause field null. Do not guess a page number.
4. If the documents do not contain the answer, set "answered" to false and say \
plainly what is missing. Do not substitute general legal knowledge, industry \
norms or an assumption. An honest "these documents don't cover that" is a \
correct answer; a plausible guess is a failure.
5. If the documents conflict, or the answer depends on a term defined \
elsewhere in them, say so and cite both places.
6. Quote the exact wording when the precise phrasing carries the obligation.

Style: precise and businesslike, like a briefing note. No greetings, no \
hedging filler, no restating the question. Two or three short paragraphs at \
most; use a short list when the answer is genuinely a list. Give the answer \
first, then the qualifying detail (caps, conditions, notice periods) that \
changes what it means in practice."""


# ── The shape we require back from the model ─────────────────────────────────
# All fields are required (nullable where optional): structured outputs demand
# a strict schema in which every property appears in `required`.


class _ModelCitation(BaseModel):
    document: str  # exact filename as given in the document header
    page: int  # 1-based page number as labelled in the supplied text
    clause: Optional[str]  # e.g. "Clause 7.3", "Annex A" — null if unnumbered
    quote: Optional[str]  # short verbatim snippet supporting the claim


class _ModelAnswer(BaseModel):
    answered: bool  # false when the documents do not cover the question
    answer: str
    citations: List[_ModelCitation]


# ── Public entry point ───────────────────────────────────────────────────────


def answer_question(question: str, document_ids: Optional[Sequence[str]] = None) -> AskResponse:
    """Answer `question` strictly from the selected (or all) documents."""
    question = (question or "").strip()
    if not question:
        raise InvalidQuestion("Please enter a question.")
    if not OPENAI_API_KEY:
        raise MissingApiKey(
            "No OpenAI API key is configured. Add OPENAI_API_KEY to the .env "
            "file in the project root and restart the server."
        )

    documents = _select_documents(document_ids)
    if not documents:
        raise NoDocuments(
            "There are no documents to search yet — upload a contract first."
            if not document_ids
            else "Those documents are no longer in the workspace."
        )

    documents, scope_note = _fit_to_budget(documents)
    context = _build_context(documents)

    try:
        parsed = _call_model(context, question, documents)
    except openai.AuthenticationError as exc:
        raise QAError("The OpenAI API key was rejected. Check OPENAI_API_KEY in .env.") from exc
    except openai.PermissionDeniedError as exc:
        raise QAError(
            f"This API key is not allowed to use the model '{OPENAI_MODEL}'. "
            "Set OPENAI_MODEL in .env to a model the key can reach."
        ) from exc
    except openai.NotFoundError as exc:
        raise QAError(
            f"The model '{OPENAI_MODEL}' was not found. Set OPENAI_MODEL in .env "
            "to a model your account can use."
        ) from exc
    except openai.RateLimitError as exc:
        raise QAError(
            "The OpenAI API is rate limiting this key, or the account is out of "
            "quota. Try again shortly."
        ) from exc
    except openai.APIStatusError as exc:
        logger.error("OpenAI API error %s", exc.status_code)
        raise QAError(
            "The OpenAI API returned an error. Try again in a moment."
            if exc.status_code >= 500
            else "The question could not be processed by the model."
        ) from exc
    except openai.APIConnectionError as exc:
        raise QAError("Could not reach the OpenAI API. Check the network connection.") from exc

    citations, unverified = _verify_citations(parsed.citations, documents)
    if unverified and not citations:
        # The model cited something that isn't in these documents. Keep the
        # answer but never present an unverifiable citation as a source.
        scope_note = _join_notes(
            scope_note,
            "The sources cited could not be matched to a page in these documents — verify before relying on this answer.",
        )

    return AskResponse(
        answer=parsed.answer.strip(),
        answered=parsed.answered,
        citations=citations,
        documents_searched=[document.filename for document in documents],
        scope_note=scope_note,
    )


# ── Prompt construction ──────────────────────────────────────────────────────


def _build_context(documents: Sequence[Document]) -> str:
    """Render every document's full text, page by page, with clear labels.

    The page markers are what the model cites, so they are unambiguous and
    repeated on every page rather than only at document boundaries.
    """
    pages_by_document = store.get_pages_for([document.id for document in documents])

    parts: List[str] = []
    for document in documents:
        parts.append(
            f"<document filename=\"{document.filename}\" pages=\"{document.page_count}\">"
        )
        for page in pages_by_document.get(document.id, []):
            text = page.text.strip()
            parts.append(f"[PAGE {page.page_number}]")
            parts.append(text if text else "(this page contains no extractable text)")
        parts.append("</document>")
    return "\n".join(parts)


def _cache_key(documents: Sequence[Document]) -> str:
    """A stable key for this exact document set, for prompt-cache routing."""
    digest = hashlib.sha256(
        "|".join(sorted(document.id for document in documents)).encode()
    ).hexdigest()
    return f"provision-docs-{digest[:32]}"


def _call_model(
    context: str, question: str, documents: Sequence[Document]
) -> _ModelAnswer:
    client = OpenAI(api_key=OPENAI_API_KEY)

    response = client.responses.parse(
        model=OPENAI_MODEL,
        instructions=SYSTEM_PROMPT,
        input=[
            {
                "role": "user",
                "content": [
                    {
                        # Stable prefix: identical for every question about this
                        # same document set, so it is served from cache.
                        "type": "input_text",
                        "text": (
                            "These are the company's documents. Everything you "
                            "may rely on is between the tags below.\n\n" + context
                        ),
                    },
                    {
                        # Volatile part, deliberately last so it never breaks
                        # the cached prefix.
                        "type": "input_text",
                        "text": (
                            f"Question: {question}\n\n"
                            "Answer using only the documents above. Cite the "
                            "document filename and page number for every claim. "
                            "If they do not cover it, set answered to false."
                        ),
                    },
                ],
            }
        ],
        text_format=_ModelAnswer,
        max_output_tokens=ANSWER_MAX_TOKENS,
        reasoning={"effort": OPENAI_REASONING_EFFORT},
        prompt_cache_key=_cache_key(documents),
    )

    usage = response.usage
    if usage is not None:
        # Useful for confirming the prefix cache is actually being hit; the
        # document text dominates input cost, so a zero here on a repeat
        # question means something upstream of the question changed.
        logger.info(
            "tokens in=%s (cached %s) out=%s",
            usage.input_tokens,
            getattr(usage.input_tokens_details, "cached_tokens", 0),
            usage.output_tokens,
        )

    if response.status == "incomplete":
        reason = getattr(response.incomplete_details, "reason", None)
        logger.warning("Model response incomplete: %s", reason)
        raise QAError(
            "The answer was cut short before it finished. Try a narrower "
            "question, or scope it to a single document."
        )

    parsed = response.output_parsed
    if parsed is None:
        # Covers a refusal or any other non-conforming output.
        raise QAError("The model did not return a usable answer. Try rephrasing the question.")
    return parsed


# ── Document selection and budget ────────────────────────────────────────────


def _select_documents(document_ids: Optional[Sequence[str]]) -> List[Document]:
    if document_ids:
        return store.get_documents(list(document_ids))
    return store.list_documents()  # newest first


def _fit_to_budget(documents: List[Document]) -> Tuple[List[Document], Optional[str]]:
    """Trim the document set to the context budget, newest first.

    Never truncates a document's text — a half-read contract produces confident
    wrong answers. It drops whole documents and reports which ones were read.
    """
    total = sum(document.char_count for document in documents)
    if total <= MAX_CONTEXT_CHARS or len(documents) <= 1:
        return documents, None

    kept: List[Document] = []
    used = 0
    for document in documents:
        if kept and used + document.char_count > MAX_CONTEXT_CHARS:
            continue
        kept.append(document)
        used += document.char_count

    dropped = len(documents) - len(kept)
    if dropped == 0:
        return documents, None

    names = ", ".join(document.filename for document in kept)
    return kept, (
        f"These documents are too long to read together, so this answer covers "
        f"only {names} ({dropped} other document{'s' if dropped != 1 else ''} "
        "not searched). Select a single document to ask about the rest."
    )


# ── Citation verification ────────────────────────────────────────────────────


def _verify_citations(
    raw: Sequence[_ModelCitation], documents: Sequence[Document]
) -> Tuple[List[Citation], int]:
    """Keep only citations that point at a real document and a real page.

    Returns the surviving citations and how many were discarded. This is the
    backstop for rule 3 in the system prompt: a citation the user cannot open
    is worse than no citation at all.
    """
    by_name = {document.filename.lower(): document for document in documents}
    verified: List[Citation] = []
    discarded = 0
    seen: set[Tuple[str, int, Optional[str]]] = set()

    for citation in raw:
        document = by_name.get((citation.document or "").strip().lower())
        if document is None or not (1 <= citation.page <= max(document.page_count, 1)):
            discarded += 1
            logger.info(
                "Discarded unverifiable citation: %r p.%s", citation.document, citation.page
            )
            continue
        key = (document.id, citation.page, citation.clause)
        if key in seen:
            continue
        seen.add(key)
        verified.append(
            Citation(
                document_id=document.id,
                document=document.filename,
                page=citation.page,
                clause=_clean(citation.clause),
                quote=_clean(citation.quote),
            )
        )
    return verified, discarded


def _clean(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    return value or None


def _join_notes(*notes: Optional[str]) -> Optional[str]:
    present = [note for note in notes if note]
    return " ".join(present) if present else None
