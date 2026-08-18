# Provision

Contract obligation intelligence for small-to-mid B2B companies. Upload the
contracts, MOUs, quotations and purchase orders you sign, then ask questions
about them in plain language and get answers that cite the document and page
they came from.

**This is Phase 1.** Two things work end to end:

- **Document ingestion** — PDF and DOCX are parsed page by page and stored.
- **Grounded Q&A** — answers come only from the uploaded documents, with a
  citation (document, page, and clause where identifiable) behind each claim.
  When the documents don't cover a question, it says so instead of guessing.

Obligation extraction, delivery tracking, alerts, loophole detection, order
optimisation and letter drafting are **not** built yet. The landing page shows
them as illustrative sections, clearly labelled as such.

---

## Setup (Windows)

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Open `.env` and set your OpenAI API key:

```
OPENAI_API_KEY=sk-proj-...
```

Get one at <https://platform.openai.com/api-keys>. `.env` is gitignored — the
key never enters the repository, the code or the logs.

Run it:

```
uvicorn backend.main:app --reload
```

Then open <http://127.0.0.1:8000>.

On macOS/Linux the only differences are `source .venv/bin/activate` and
`cp .env.example .env`.

Uploading and listing documents work without an API key; asking questions
returns a clear error until the key is set.

---

## Using it

1. Scroll to **Your workspace** (or click *Open workspace*).
2. Drop PDF or DOCX files onto the upload area, or click to browse. Each file is
   parsed on upload; unreadable files are reported individually and the rest
   still go through.
3. Ask a question. By default every uploaded document is searched. Click a
   document in the rail to narrow the question to it (click again to clear).
4. Each answer lists its sources underneath — document, page, clause and the
   quoted wording where relevant.

Answers are grounded, not authoritative. Check the cited clause before acting
on anything that costs money.

---

## How the Q&A works

There is **no vector database and no embeddings retrieval**, by design. For each
question the full text of the selected documents goes into the model prompt and
the model is asked to answer from that text alone.

Contracts are small enough to fit in context, and chunk retrieval is actively
harmful here: clause 7.3 ("2% compensation for late delivery") is wrong without
clause 7.4 (the 10% quarterly cap) and the schedule six pages earlier. Whole
documents keep those cross-references intact.

Supporting details:

- **Prompt shape** — system rules, then the document text as a single stable
  block, then the question last. OpenAI caches long prompt prefixes
  automatically, and the request carries a `prompt_cache_key` derived from the
  document set, so repeated questions over the same documents reuse the cached
  prefix instead of paying full price for the contract text every time.
- **Context budget** — `MAX_CONTEXT_TOKENS` (default 150,000) caps how much
  document text one question may carry. Over that, whole documents are dropped
  from the search, newest first, and the answer says which ones were read.
  Documents are never silently truncated.
- **Citation verification** — the backend checks every citation the model
  returns against a real document and a real page number, and discards any that
  don't match. A citation you cannot open is worse than no citation.
- **Model** — set once in `backend/config.py` (`OPENAI_MODEL`, default
  `gpt-5.5`); override with `OPENAI_MODEL` in `.env`. `OPENAI_REASONING_EFFORT`
  (default `medium`) trades latency for care.
- **Structured output** — the model must return `{answered, answer, citations}`
  against a strict schema, so a "not covered" answer is a typed flag the UI can
  render, not a phrase to pattern-match.

---

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/documents` | Multipart upload (field `files`, repeatable). Parses, stores and indexes each file. Returns per-file results. |
| `GET` | `/api/documents` | List uploaded documents with metadata, newest first. |
| `DELETE` | `/api/documents/{id}` | Delete a document, its page text and its stored file. |
| `POST` | `/api/ask` | `{"question": "...", "document_ids": ["..."] \| null}` → grounded answer plus citations. |
| `GET` | `/api/config` | Upload limits and whether an API key is configured. |
| `GET` | `/api/health` | Liveness, active model. |
| `GET` | `/` | The frontend. |

Errors come back as `{"error": "message written for a person"}` with a real
status code. Interactive API docs are at `/docs`.

### `/api/ask` response

```json
{
  "answer": "Acme owes 2% of the consignment invoice value ...",
  "answered": true,
  "citations": [
    {
      "document_id": "5f711e...",
      "document": "Supplier Agreement — Acme Logistics.pdf",
      "page": 18,
      "clause": "Clause 7.3",
      "quote": "two per cent (2%) of the invoice value"
    }
  ],
  "documents_searched": ["Supplier Agreement — Acme Logistics.pdf"],
  "scope_note": null
}
```

`answered` is `false` when the documents genuinely don't cover the question —
the UI renders that as an explicit "not covered in these documents" state.
`scope_note` is set when the context budget narrowed the search, or when a
citation could not be verified.

---

## Layout

```
backend/
  main.py          FastAPI app, routes, error shape, static mount
  config.py        settings, model constant, .env loading
  models.py        SQLModel tables (documents, pages) + API schemas
  store.py         DB init, document/page persistence, file storage
  ingest.py        PDF/DOCX -> per-page text
  qa.py            prompt construction, OpenAI call, citation verification
  obligations.py   stub — the seam for later phases, no behaviour
frontend/
  index.html       the workspace UI (adapted from provision-poc.html)
data/
  uploads/         stored files          (gitignored)
  provision.db     SQLite                (gitignored)
provision-poc.html the original design reference
```

---

## Known limits in Phase 1

- **Digital documents only.** Scanned or image-only PDFs produce no text and
  are rejected with a message saying they need OCR. OCR is out of scope.
- **DOCX pages are approximate.** Word paginates at render time, so there are no
  real page boundaries in the file. Explicit page breaks are honoured where the
  author inserted them; otherwise the document is split into ~2,800-character
  pages so citations still point somewhere consistent. PDF page numbers are
  exact.
- **No accounts, no auth, no multi-tenancy.** Anyone who can reach the server
  can read and delete every document. Run it locally.
- **No conversation memory.** Each question is answered independently; follow-up
  questions should restate their subject.
- `.doc` (pre-2007 Word) is not supported — re-save as `.docx`.
