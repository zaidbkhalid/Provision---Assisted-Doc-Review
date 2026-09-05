"""Application settings.

Everything environment-dependent is resolved here once, at import time, so no
other module needs to touch os.environ. Secrets are read from .env and are
never logged or returned by the API.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Project root = the directory containing backend/, frontend/ and data/.
BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

# ── Paths ────────────────────────────────────────────────────────────────────
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
FRONTEND_DIR = BASE_DIR / "frontend"
DB_PATH = DATA_DIR / "provision.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

# ── Groq (OpenAI-compatible API) ─────────────────────────────────────────────
# Read from the environment only. Never hardcode a key, never log this value.
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()

# Groq speaks the OpenAI wire format, so the official `openai` SDK is used with
# this base URL. Without it the SDK would talk to OpenAI, not Groq.
GROQ_BASE_URL = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").strip()

# ── OpenAI (fallback / primary provider) ─────────────────────────────────────
# Groq's free tier caps this key at 8,000 tokens per minute, which is enough to
# stall a live demo after two or three questions. OpenAI is wired alongside it —
# the Groq path below is untouched and still used, it is simply no longer the
# only option.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

# Left overridable for Azure/proxy setups; empty means the SDK default.
OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "").strip()

OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.5").strip()
OPENAI_SUMMARY_MODEL = os.getenv("OPENAI_SUMMARY_MODEL", "gpt-5.5-mini").strip()

# Which providers to try, in order. The first one holding an API key answers;
# if it rate-limits or errors, the next is tried automatically. Set to
# "groq,openai" to prefer Groq, or to a single name to pin one provider.
LLM_PROVIDER_ORDER = [
    name.strip().lower()
    for name in os.getenv("LLM_PROVIDER_ORDER", "openai,groq").split(",")
    if name.strip()
]

# ── Models ───────────────────────────────────────────────────────────────────
# Both IDs below were taken from Groq's own /models listing for this key, not
# guessed. Check with: GET https://api.groq.com/openai/v1/models
#
# Chat Q&A: the larger model. Accuracy matters most here — the user is asking a
# specific question and acting on the answer.
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b").strip()

# Auto-summary cards: the smaller, cheaper, faster model. Six questions fire per
# document, each carrying the whole contract, so this is where spend accumulates.
# The cards are orientation, not the basis for a decision, so the smaller model
# is the right trade. Set to GROQ_MODEL if you want them on the large one.
GROQ_SUMMARY_MODEL = os.getenv("GROQ_SUMMARY_MODEL", "openai/gpt-oss-20b").strip()

# Reasoning depth for the gpt-oss family: low | medium | high.
GROQ_REASONING_EFFORT = os.getenv("GROQ_REASONING_EFFORT", "medium").strip()

# PROMPT CACHING ON GROQ — measured, not assumed:
# Groq documents automatic prefix caching (50% off cached input tokens) for
# exactly these gpt-oss models, with no code changes required. It is real but
# INTERMITTENT on this key. Repeating a byte-identical ~2,000 token document
# prefix returned cached_tokens of 0, 0, 0 on gpt-oss-20b and 0, 768, 0 on
# gpt-oss-120b, yet a separate run reported 1,792 of 2,002 tokens cached (89%).
# So it does fire, but not predictably enough to budget for. The prompt is built
# prefix-stable (document text ahead of the volatile question) so the discount
# lands whenever the cache does hit; treat any saving as a bonus, not a plan.

# Ceiling on one answer, reasoning tokens included — not a target.
#
# Keep this SMALL on Groq. Groq counts prompt + max_output_tokens against the
# per-minute token limit BEFORE running the call, so an oversized ceiling gets
# the request rejected outright with HTTP 413 rather than merely capping the
# answer. This key's limit is 8,000 TPM on both gpt-oss models, and a typical
# contract prompt is ~2,000 tokens, so 8,000 here made every call fail
# (2,000 + 8,000 = 10,000 > 8,000).
#
# It must not be too small either: when a structured answer is truncated
# mid-JSON, Groq rejects the whole response with 400 json_validate_failed rather
# than returning a partial one. The broad summary questions were observed using
# 1,500-2,200 output tokens, so 3,000 leaves headroom while keeping
# prompt + output (~1,900 + 3,000) inside the 8,000 TPM ceiling.
ANSWER_MAX_TOKENS = int(os.getenv("ANSWER_MAX_TOKENS", "3000"))

# ── Auto-summary behaviour ───────────────────────────────────────────────────
# When True, uploading a document immediately fires all six preset questions.
# Default OFF: that is six full-document calls per upload, which is expensive
# during development. With it off the panel shows its "Generate summary" button
# and the user runs the summary deliberately. The auto-run code path is intact
# either way — this only decides whether it fires by itself.
SUMMARY_AUTO_RUN = os.getenv("SUMMARY_AUTO_RUN", "false").strip().lower() in {"1", "true", "yes", "on"}

# ── Uploads ──────────────────────────────────────────────────────────────────
SUPPORTED_EXTENSIONS = {".pdf", ".docx"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB per file

# ── Q&A context budget ───────────────────────────────────────────────────────
# We deliberately put whole documents in the prompt rather than doing vector
# retrieval (contracts are small, and cross-references between clauses survive).
# This is the ceiling on how much document text one question may carry. Beyond
# it we narrow to a single document and tell the user we did.
#
# Measured in characters to avoid a token-counting API round-trip per question;
# CHARS_PER_TOKEN is a deliberately conservative estimate for English legal prose.
CHARS_PER_TOKEN = 3.5
MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "150000"))
MAX_CONTEXT_CHARS = int(MAX_CONTEXT_TOKENS * CHARS_PER_TOKEN)


def ensure_directories() -> None:
    """Create the on-disk layout the app expects. Safe to call repeatedly."""
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def llm_configured() -> bool:
    """True when at least one provider in the chain has a key."""
    return bool(GROQ_API_KEY or OPENAI_API_KEY)


def configured_providers() -> list[str]:
    """Provider names, in try order, that actually hold a key."""
    keys = {"openai": OPENAI_API_KEY, "groq": GROQ_API_KEY}
    return [name for name in LLM_PROVIDER_ORDER if keys.get(name)]
