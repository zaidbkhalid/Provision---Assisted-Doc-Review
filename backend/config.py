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

# ── OpenAI ───────────────────────────────────────────────────────────────────
# Read from the environment only. Never hardcode a key, never log this value.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

# Single configurable model constant for the whole app.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.5").strip()

# Reasoning depth for the GPT-5 family: minimal | low | medium | high.
# Contract questions reward care over speed, but "high" is rarely worth the
# latency here because the answer is extraction, not open-ended reasoning.
OPENAI_REASONING_EFFORT = os.getenv("OPENAI_REASONING_EFFORT", "medium").strip()

# Ceiling on one answer, reasoning tokens included — not a target. Reasoning
# models spend part of this budget before writing anything, so it is generous.
ANSWER_MAX_TOKENS = 8000

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
    """True when an API key is present. Used to fail Q&A with a clear message."""
    return bool(OPENAI_API_KEY)
