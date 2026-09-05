"""Auto-summary panel: preset questions run automatically over one document.

WHAT THIS IS: a convenience layer on top of the existing Q&A path. Every card
below is an LLM-generated answer to a fixed question, produced by exactly the
same `qa.answer_question` call the chat box uses — same grounding rules, same
citation verification, same honest "not covered" behaviour.

WHAT THIS IS NOT: structured obligation extraction. These cards are prose
summaries for orientation, not audited records with typed fields, confidence
scores or a review workflow. That extraction is separate, later work and belongs
in `obligations.py`, not here. Nothing downstream should treat a card as a
verified obligation.

The point of the panel is that a blank chat box only helps someone who already
knows what to ask; these questions surface what they didn't know to look for.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

from . import qa, store
from .config import GROQ_SUMMARY_MODEL
from .models import SummaryCard, SummaryResponse

logger = logging.getLogger("provision.summary")

# Shown on the panel and repeated in the API response, so the caveat travels
# with the data rather than living only in the markup.
SUMMARY_DISCLAIMER = (
    "AI summary — generated from this document's text. Verify against the "
    "cited clause before relying on it."
)


@dataclass(frozen=True)
class SummaryQuestion:
    key: str  # stable id used by the API and the UI
    heading: str  # short card title
    question: str  # what actually gets asked of the model


# ── THE PRESET LIST ──────────────────────────────────────────────────────────
# Single source of truth. Edit, reorder, add or remove entries here and both the
# API and the panel follow; nothing else hardcodes these questions.

SUMMARY_QUESTIONS: Tuple[SummaryQuestion, ...] = (
    SummaryQuestion(
        key="dates",
        heading="Key dates & deadlines",
        question=(
            "What are the key dates and deadlines in this document, including "
            "delivery dates, renewal dates, notice deadlines and expiry?"
        ),
    ),
    SummaryQuestion(
        key="penalties",
        heading="Penalties & compensation",
        question=(
            "What penalties, liquidated damages, or compensation clauses apply, "
            "and what triggers each?"
        ),
    ),
    SummaryQuestion(
        key="discounts",
        heading="Discounts & pricing benefits",
        question=(
            "What discounts, rebates, or volume-based pricing benefits are "
            "available, and at what thresholds?"
        ),
    ),
    SummaryQuestion(
        key="renewal",
        heading="Renewal & termination",
        question=(
            "What are the renewal and termination terms, including any required "
            "notice period?"
        ),
    ),
    SummaryQuestion(
        key="obligations",
        heading="Ongoing obligations",
        question=(
            "What are the main ongoing obligations for each party, for example "
            "insurance, reporting, compliance, or minimum commitments?"
        ),
    ),
    SummaryQuestion(
        key="unusual",
        heading="Unusual or easily-missed terms",
        question=(
            "Are there any unusual, one-sided, or easily-missed terms worth "
            "attention?"
        ),
    ),
)

QUESTIONS_BY_KEY = {item.key: item for item in SUMMARY_QUESTIONS}


class UnknownSummaryQuestion(qa.QAError):
    """The requested card key is not in SUMMARY_QUESTIONS."""


def get_question(key: str) -> SummaryQuestion:
    question = QUESTIONS_BY_KEY.get((key or "").strip())
    if question is None:
        raise UnknownSummaryQuestion(f"Unknown summary question '{key}'.")
    return question


def require_document(document_id: str):
    """Resolve a document id, with the same wording the chat path uses."""
    document = store.get_document((document_id or "").strip())
    if document is None:
        raise qa.NoDocuments("That document is no longer in the workspace.")
    return document


def answer_card(document_id: str, key: str) -> SummaryCard:
    """Answer one preset question about one document.

    Scoped to a single document on purpose: a summary card that silently mixed
    clauses from several contracts would be misleading, however well cited.
    """
    question = get_question(key)
    require_document(document_id)

    # The existing Q&A engine, unchanged — no second retrieval path. Only the
    # model differs: six full-document calls fire per document, so the cards run
    # on the cheaper GROQ_SUMMARY_MODEL while the chat keeps the larger one.
    result = qa.answer_question(
        question.question, [document_id], model=GROQ_SUMMARY_MODEL
    )

    return SummaryCard(
        key=question.key,
        heading=question.heading,
        question=question.question,
        answer=result.answer,
        answered=result.answered,
        citations=result.citations,
        scope_note=result.scope_note,
    )


def summarise_document(document_id: str) -> SummaryResponse:
    """Every preset question for one document, in order.

    Sequential, so the first call warms the prompt cache for the rest. A card
    that fails carries its error rather than sinking the whole panel.
    """
    document = require_document(document_id)

    cards: List[SummaryCard] = []
    for question in SUMMARY_QUESTIONS:
        try:
            cards.append(answer_card(document_id, question.key))
        except qa.QAError as exc:
            logger.info("Summary card %s failed: %s", question.key, exc)
            cards.append(
                SummaryCard(
                    key=question.key,
                    heading=question.heading,
                    question=question.question,
                    error=str(exc),
                )
            )

    return SummaryResponse(
        document_id=document.id,
        document=document.filename,
        cards=cards,
        disclaimer=SUMMARY_DISCLAIMER,
    )
