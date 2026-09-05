"""Grounded question answering over the uploaded documents.

Deliberately no embeddings and no vector retrieval. Contracts are small enough
to put in the model's context whole, and doing so keeps cross-references intact
— clause 7.3 is meaningless without the cap in 7.4 and the schedule six pages
earlier, and chunk retrieval routinely returns one without the others.

Calls go to Groq via its OpenAI-compatible endpoint, using the official `openai`
SDK pointed at GROQ_BASE_URL.

The request is built as: stable instructions -> the full document text (one
input block) -> the question, last. Groq's automatic prefix caching needs
exactly that shape. Measured, the cache hits intermittently on this key (0% on
most repeats, 89% on one), so the layout earns a discount when it lands but is
not something to budget for. See config.py for the numbers.
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
    GROQ_API_KEY,
    GROQ_BASE_URL,
    GROQ_MODEL,
    GROQ_REASONING_EFFORT,
    MAX_CONTEXT_CHARS,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_MODEL,
    OPENAI_SUMMARY_MODEL,
    configured_providers,
)
from .models import AskResponse, Citation, Document

logger = logging.getLogger("provision.qa")

# Ids of answers that hit the output ceiling, so answer_question can attach a
# warning without changing _call_model's return type.
_TRUNCATED: set[int] = set()


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

Style — short and exact:
- The first sentence is the direct answer. Put nothing in front of it: no greeting, no restating the question, no describing what the document is.
- After it, give only what changes whether or how that answer applies: conditions, thresholds, caps, notice periods, deadlines, exceptions. Then stop.
- No background, no general explanation the question did not ask for, no advice on what to do next, no closing summary.
- Use a short list only when the answer is genuinely several items.
- Write the answer as clean prose with NO citation strings inside it. Do not append references like "(agreement.docx, p. 2, cl. 7.3)" or "[p.4]" to sentences, and do not add a sources line at the end. Naming a clause as part of the sentence is fine when the sentence is about that clause ("the cap in clause 7.4 applies"); a parenthetical reference tacked on to show your working is not. Every citation belongs in the separate citations field, which is rendered under the answer for the user — putting it in both places just repeats it.
- Brevity never outranks accuracy. A qualifier is part of the answer, not detail to trim: "a 2% credit" without "if the delivery is more than five business days late, capped at 10% of quarterly invoice value" is a wrong answer, not a concise one. Where the document leaves out something the answer depends on, say so in one clause rather than dropping it.

Example of the required shape:
Q: "What do we get if a delivery is late?"
A: "A credit of 2% of the affected consignment's invoice value, where delivery is more than five business days beyond the scheduled date, capped at 10% of aggregate invoice value in any calendar quarter. The claim must be made in writing within 30 days of receipt."
(Note the absence of any bracketed document or page reference in that answer text — the citations travel in the citations field instead.)
"""


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


def answer_question(
    question: str,
    document_ids: Optional[Sequence[str]] = None,
    model: Optional[str] = None,
) -> AskResponse:
    """Answer `question` strictly from the selected (or all) documents.

    `model` overrides the chat model for this one call. The auto-summary passes
    the cheaper GROQ_SUMMARY_MODEL through it; everything else leaves it None and
    gets GROQ_MODEL. Retrieval, prompt and verification are identical either way.
    """
    question = (question or "").strip()
    if not question:
        raise InvalidQuestion("Please enter a question.")
    if not configured_providers():
        raise MissingApiKey(
            "No LLM API key is configured. Add OPENAI_API_KEY (recommended) or "
            "GROQ_API_KEY to the .env file in the project root and restart."
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
        parsed = _call_model(context, question, documents, model)
    except openai.AuthenticationError as exc:
        raise QAError(
            "The LLM API key was rejected. Check OPENAI_API_KEY / GROQ_API_KEY in .env."
        ) from exc
    except openai.PermissionDeniedError as exc:
        raise QAError(
            "This API key is not allowed to use the configured model. Set "
            "OPENAI_MODEL or GROQ_MODEL in .env to a model the key can reach."
        ) from exc
    except openai.NotFoundError as exc:
        raise QAError(
            "The configured model was not found. Set OPENAI_MODEL or GROQ_MODEL "
            "in .env to a model your account can use."
        ) from exc
    except openai.RateLimitError as exc:
        # Only reached after the client's own retries have been exhausted.
        raise QAError(
            "Every configured provider is rate limited right now. Groq's free "
            "tier allows 8,000 tokens/minute; adding OPENAI_API_KEY to .env "
            "gives the chain somewhere to fail over to."
        ) from exc
    except openai.APIStatusError as exc:
        logger.error("LLM API error %s", exc.status_code)
        if exc.status_code == 400 and "json_validate" in str(exc).lower():
            # Groq rejects a structured response whose JSON was cut off mid-way
            # rather than returning it truncated. More output room fixes it.
            raise QAError(
                "The model's answer was too long to return in full. Ask a "
                "narrower question, or raise ANSWER_MAX_TOKENS in .env."
            ) from exc
        if exc.status_code == 413:
            # Groq counts prompt + max_output_tokens against the per-minute
            # token limit before running the call. Either the document is large
            # or other calls have already consumed this minute's budget.
            raise QAError(
                "This request exceeds the provider's per-minute token limit. "
                "Wait a minute and retry, ask about a single document, or lower "
                "ANSWER_MAX_TOKENS in .env."
            ) from exc
        raise QAError(
            "The LLM provider returned an error. Try again in a moment."
            if exc.status_code >= 500
            else "The question could not be processed by the model."
        ) from exc
    except openai.APIConnectionError as exc:
        raise QAError(
            "Could not reach any configured LLM provider. Check the network connection."
        ) from exc

    if id(parsed) in _TRUNCATED:
        _TRUNCATED.discard(id(parsed))
        scope_note = _join_notes(
            scope_note,
            "This answer reached the length limit and may be incomplete — treat "
            "any list in it as partial.",
        )

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


# ── Providers ────────────────────────────────────────────────────────────────
# Both providers speak the OpenAI wire format, so one SDK serves both — only the
# base URL, key and model names differ. The chain is tried in order and a
# provider that rate-limits or errors hands off to the next, which is what keeps
# a live demo answering when Groq's 8,000 tokens/minute ceiling is reached.


class _Provider:
    """One LLM endpoint: how to reach it and which models to use."""

    def __init__(self, name, api_key, base_url, chat_model, summary_model, extra=None):
        self.name = name
        self.api_key = api_key
        self.base_url = base_url or None
        self.chat_model = chat_model
        self.summary_model = summary_model
        self.extra = extra or {}  # provider-specific request params

    def model_for(self, requested: Optional[str]) -> str:
        """Map a requested model onto this provider's equivalent.

        The summary panel asks for the cheap model by name. If we have failed
        over to a different provider that exact id will not exist there, so the
        request is translated to that provider's own cheap model rather than
        404ing.
        """
        if not requested:
            return self.chat_model
        if requested in (self.chat_model, self.summary_model):
            return requested
        # A model id belonging to some other provider: pick the same tier here.
        if requested in (GROQ_SUMMARY_MODEL_NAME, OPENAI_SUMMARY_MODEL):
            return self.summary_model
        return self.chat_model

    def client(self) -> OpenAI:
        kwargs = dict(api_key=self.api_key, max_retries=3, timeout=120.0)
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return OpenAI(**kwargs)


# Imported lazily to avoid a circular import in model_for above.
from .config import GROQ_SUMMARY_MODEL as GROQ_SUMMARY_MODEL_NAME  # noqa: E402


def _build_providers() -> List[_Provider]:
    """The provider chain, in configured order, skipping any without a key."""
    catalogue = {
        "openai": lambda: _Provider(
            "openai", OPENAI_API_KEY, OPENAI_BASE_URL,
            OPENAI_MODEL, OPENAI_SUMMARY_MODEL,
            # Reasoning effort is supported on the GPT-5 family.
            extra={"reasoning": {"effort": GROQ_REASONING_EFFORT}},
        ),
        "groq": lambda: _Provider(
            "groq", GROQ_API_KEY, GROQ_BASE_URL,
            GROQ_MODEL, GROQ_SUMMARY_MODEL_NAME,
            extra={"reasoning": {"effort": GROQ_REASONING_EFFORT}},
        ),
    }
    return [catalogue[name]() for name in configured_providers() if name in catalogue]


# Errors worth handing to the next provider rather than surfacing to the user.
_FAILOVER_STATUSES = {408, 409, 413, 429, 500, 502, 503, 504}


def _should_failover(exc: Exception) -> bool:
    # Rate limits, timeouts and transient server errors: the next provider may
    # well succeed, so try it.
    if isinstance(exc, (openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError)):
        return True
    # A rejected key, a missing model or a forbidden model means THIS provider is
    # misconfigured — not that the question is unanswerable. Fall through rather
    # than taking the whole app down over one bad entry in .env.
    if isinstance(exc, (openai.AuthenticationError, openai.NotFoundError, openai.PermissionDeniedError)):
        return True
    if isinstance(exc, openai.APIStatusError):
        return exc.status_code in _FAILOVER_STATUSES
    return False


def _call_model(
    context: str,
    question: str,
    documents: Sequence[Document],
    model: Optional[str] = None,
) -> _ModelAnswer:
    """Ask the first available provider; fail over to the next on a rate limit.

    This is what stops a demo dying on Groq's 8,000 tokens/minute ceiling: the
    question is simply re-asked against the next provider in the chain.
    """
    providers = _build_providers()
    if not providers:
        raise MissingApiKey(
            "No LLM API key is configured. Add OPENAI_API_KEY or GROQ_API_KEY "
            "to the .env file in the project root and restart the server."
        )

    last_error: Optional[Exception] = None
    for index, provider in enumerate(providers):
        try:
            return _call_provider(provider, context, question, model)
        except Exception as exc:
            last_error = exc
            has_next = index < len(providers) - 1
            if has_next and _should_failover(exc):
                logger.warning(
                    "provider %s failed (%s); falling back to %s",
                    provider.name, type(exc).__name__, providers[index + 1].name,
                )
                continue
            raise

    raise last_error  # pragma: no cover - loop always returns or raises


def _call_provider(
    provider: "_Provider",
    context: str,
    question: str,
    model: Optional[str] = None,
) -> _ModelAnswer:
    """One attempt against one provider."""
    client = provider.client()
    chosen_model = provider.model_for(model)

    response = client.responses.parse(
        model=chosen_model,
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
        # Provider-specific request params (reasoning effort today). No
        # prompt_cache_key: Groq keys its cache off the prefix bytes itself.
        **provider.extra,
    )

    usage = response.usage
    if usage is not None:
        # Useful for confirming the prefix cache is actually being hit; the
        # document text dominates input cost, so a zero here on a repeat
        # question means something upstream of the question changed.
        # cached_tokens is Groq's own counter. If it stays 0 across repeated
        # questions on one document, prompt caching is not active for this key.
        details = getattr(usage, "input_tokens_details", None)
        logger.info(
            "provider=%s model=%s tokens in=%s (cached %s) out=%s",
            provider.name,
            chosen_model,
            usage.input_tokens,
            getattr(details, "cached_tokens", 0) if details else 0,
            usage.output_tokens,
        )

    parsed = response.output_parsed

    if response.status == "incomplete":
        reason = getattr(response.incomplete_details, "reason", None)
        logger.warning("Model response incomplete: %s", reason)
        if parsed is None:
            raise QAError(
                "The answer was cut short before it finished. Try a narrower "
                "question, or scope it to a single document."
            )
        # Hitting the output ceiling mid-answer still often yields valid JSON.
        # Keeping it beats failing the whole card, but the user must be told it
        # may be missing items — a silently half-listed set of obligations is
        # exactly the failure this product exists to prevent.
        _TRUNCATED.add(id(parsed))

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
