/* ────────────────────────────────────────────────────────────
   Auto-summary panel — added in Phase 1.5. Additive only.

   Runs a fixed set of preset questions against ONE document the moment it is
   uploaded, so the user sees what matters without having to know what to ask.
   Every card is answered by /api/summary/card, which calls the same Q&A engine
   the chat box uses — same grounding rules, same citation verification, same
   honest "not covered" behaviour. There is no second retrieval path.

   IMPORTANT: these cards are LLM-generated PROSE SUMMARIES for orientation.
   They are NOT verified structured obligations — no typed fields, no confidence
   score, no human review step. That extraction is separate, later work and
   belongs in backend/obligations.py. Nothing here should be presented to a user
   as an audited obligation, which is why the panel carries a visible caveat.

   This file does not modify the chat script in index.html. It is called from it
   through two guarded hooks (onUploadComplete, onDocumentsRendered), and it
   deliberately reuses the chat's own esc(), renderBody(), renderCitations() and
   errorFrom() helpers so a citation in a card looks identical to one in a chat
   reply.
   ──────────────────────────────────────────────────────────── */

const SUMMARY_API = {
  questions: '/api/summary/questions',
  card:      '/api/summary/card'
};

/* How many cards to request at once.
   1, deliberately. This Groq key allows 8,000 tokens per minute, and Groq counts
   prompt + max_output_tokens against that ceiling before the call runs — so a
   single contract question reserves roughly 5,000 of it. Firing several at once
   earns 429s (and 413s for oversized requests). Sequential still renders
   progressively: each card paints as it lands rather than the panel waiting on
   all six. Raise this if the account's TPM limit goes up. */
const SUMMARY_CONCURRENCY = 1;

const summaryState = {
  questions: [],      // [{key, heading, question}] fetched from the backend
  documentId: null,   // the document the panel is currently about
  documentName: '',
  running: false,
  results: {},        // documentId -> { key -> card }, so revisiting is free
  autoRun: false      // set from /api/config; see SUMMARY_AUTO_RUN in config.py
};

const sEl = {
  panel:    document.getElementById('summaryPanel'),
  cards:    document.getElementById('summaryCards'),
  idle:     document.getElementById('summaryIdle'),
  docName:  document.getElementById('summaryDocName'),
  progress: document.getElementById('summaryProgress'),
  refresh:  document.getElementById('summaryRefresh')
};

/* ── Card rendering ── */

function summaryCardShell(question, index) {
  const number = String(index + 1).padStart(2, '0');
  return `
    <article class="border border-line rounded-xl bg-white p-5" data-key="${esc(question.key)}">
      <div class="flex items-start justify-between gap-3">
        <div class="min-w-0">
          <p class="font-mono text-[10.5px] text-faint uppercase tracking-wider">${number}</p>
          <h3 class="font-medium text-[15px] mt-1.5 leading-tight">${esc(question.heading)}</h3>
        </div>
        <div class="shrink-0 pt-1" data-role="status"></div>
      </div>
      <div class="mt-3 space-y-3" data-role="body"></div>
    </article>`;
}

function summaryCardNode(key) {
  return sEl.cards.querySelector(`[data-key="${CSS.escape(key)}"]`);
}

function setCardPending(key) {
  const node = summaryCardNode(key);
  if (!node) return;
  node.querySelector('[data-role="status"]').innerHTML =
    '<span class="dot-typing inline-block"><span></span><span></span><span></span></span>';
  node.querySelector('[data-role="body"]').innerHTML =
    '<p class="text-[13.5px] text-faint">Reading the document…</p>';
}

function setCardIdle(key) {
  const node = summaryCardNode(key);
  if (!node) return;
  node.querySelector('[data-role="status"]').innerHTML = '';
  node.querySelector('[data-role="body"]').innerHTML =
    '<p class="text-[13.5px] text-faint">Not generated yet.</p>';
}

function setCardError(key, message) {
  const node = summaryCardNode(key);
  if (!node) return;
  node.querySelector('[data-role="status"]').innerHTML =
    '<span class="font-mono text-[10.5px] text-bad border border-bad/20 bg-bad/5 rounded px-1.5 py-0.5">Failed</span>';
  node.querySelector('[data-role="body"]').innerHTML =
    `<p class="text-[13.5px] text-bad leading-[1.55]">${esc(message)}</p>` +
    '<button type="button" data-role="retry" class="text-[13px] font-medium border border-line px-3 py-1.5 rounded-md hover:border-ink transition-colors">Retry this card</button>';
  const retry = node.querySelector('[data-role="retry"]');
  if (retry) retry.addEventListener('click', () => runOneCard(summaryState.documentId, key));
}

function setCardAnswer(key, card) {
  const node = summaryCardNode(key);
  if (!node) return;

  const notCovered = card.answered === false;
  node.querySelector('[data-role="status"]').innerHTML = notCovered
    ? '<span class="font-mono text-[10.5px] text-faint border border-line rounded px-1.5 py-0.5">Not found</span>'
    : '<span class="font-mono text-[10.5px] text-ok border border-ok/25 bg-ok/5 rounded px-1.5 py-0.5">Found</span>';

  const scope = card.scope_note
    ? `<p class="text-[12.5px] text-warn leading-[1.5]">${esc(card.scope_note)}</p>`
    : '';

  // Same helpers the chat uses, so citations render identically in both places.
  node.querySelector('[data-role="body"]').innerHTML =
    renderBody(card.answer || '') + scope + renderCitations(card.citations, card.answered);
}

/* ── Panel state ── */

function renderSummaryShell() {
  sEl.cards.innerHTML = summaryState.questions
    .map((question, index) => summaryCardShell(question, index))
    .join('');
}

function updateSummaryChrome() {
  const hasDocument = Boolean(summaryState.documentId);
  sEl.panel.classList.toggle('hidden', !hasDocument);
  sEl.docName.textContent = summaryState.documentName || '';
  sEl.refresh.disabled = !hasDocument || summaryState.running;

  const cached = summaryState.results[summaryState.documentId] || {};
  const done = Object.keys(cached).length;
  const total = summaryState.questions.length;
  const started = done > 0 || summaryState.running;

  sEl.idle.classList.toggle('hidden', started);
  sEl.cards.classList.toggle('hidden', !started);

  if (summaryState.running) {
    sEl.progress.textContent = `${done} of ${total} answered…`;
    sEl.refresh.textContent = 'Generating…';
  } else {
    sEl.progress.textContent = done ? `${done} of ${total} answered` : '';
    sEl.refresh.textContent = done ? 'Refresh summary' : 'Generate summary';
  }
}

/* ── Running the preset questions ── */

async function runOneCard(documentId, key) {
  setCardPending(key);
  try {
    const response = await fetch(SUMMARY_API.card, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ document_id: documentId, key })
    });
    if (!response.ok) throw new Error(await errorFrom(response));
    const card = await response.json();

    // Ignore a late reply for a document the user has since moved away from.
    if (summaryState.documentId !== documentId) return;

    summaryState.results[documentId] = summaryState.results[documentId] || {};
    summaryState.results[documentId][key] = card;
    setCardAnswer(key, card);
  } catch (err) {
    if (summaryState.documentId !== documentId) return;
    setCardError(key, err.message || 'This card could not be generated.');
  } finally {
    updateSummaryChrome();
  }
}

async function runSummary(documentId) {
  if (summaryState.running || !documentId || !summaryState.questions.length) return;

  summaryState.running = true;
  summaryState.results[documentId] = {};
  renderSummaryShell();
  summaryState.questions.forEach(question => setCardPending(question.key));
  updateSummaryChrome();

  try {
    const keys = summaryState.questions.map(question => question.key);

    // First card alone — warms Groq's prefix cache for this document when the
    // cache hits (it does so intermittently; see SUMMARY_CONCURRENCY above).
    await runOneCard(documentId, keys[0]);
    if (summaryState.documentId !== documentId) return;

    // The rest run in parallel, each card painting as soon as it lands.
    const queue = keys.slice(1);
    const workers = Array.from(
      { length: Math.min(SUMMARY_CONCURRENCY, queue.length) },
      async () => {
        while (queue.length) {
          const key = queue.shift();
          if (summaryState.documentId !== documentId) return;
          await runOneCard(documentId, key);
        }
      }
    );
    await Promise.all(workers);
  } finally {
    summaryState.running = false;
    updateSummaryChrome();
  }
}

/* Restore an already-generated summary rather than spending six more calls. */
function restoreSummary(documentId) {
  const cached = summaryState.results[documentId];
  renderSummaryShell();
  summaryState.questions.forEach(question => {
    const card = cached && cached[question.key];
    if (card) setCardAnswer(question.key, card);
    else setCardIdle(question.key);
  });
  updateSummaryChrome();
}

function setSummaryTarget(target, options) {
  const autoRun = Boolean(options && options.autoRun);
  const changed = summaryState.documentId !== target.id;
  summaryState.documentId = target.id;
  summaryState.documentName = target.filename;

  if (summaryState.results[target.id]) {
    restoreSummary(target.id);
  } else if (changed || !sEl.cards.children.length) {
    renderSummaryShell();
    summaryState.questions.forEach(question => setCardIdle(question.key));
    updateSummaryChrome();
  } else {
    updateSummaryChrome();
  }

  if (autoRun && !summaryState.results[target.id]) runSummary(target.id);
}

function clearSummaryTarget() {
  summaryState.documentId = null;
  summaryState.documentName = '';
  sEl.cards.innerHTML = '';
  updateSummaryChrome();
}

/* ── Hooks called from the existing chat script in index.html ── */

/* Auto-run. A freshly uploaded document is summarised immediately — that is the
   whole point of the panel: the user should not have to know what to ask. */
function onUploadComplete(documents) {
  const uploaded = (documents || [])[0];
  if (!uploaded) return;
  // Gated on SUMMARY_AUTO_RUN (backend config, default off). With it off the
  // panel still targets the new document and shows its Generate button, so the
  // six calls only happen when the user asks for them.
  setSummaryTarget(uploaded, { autoRun: summaryState.autoRun });
  sEl.panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* Retarget when the rail changes. Selecting a single document points the panel
   at it but does NOT auto-run — six model calls per click would be a surprise
   in both latency and cost. An existing summary is restored from memory. */
function onDocumentsRendered() {
  const documents = state.documents || [];
  if (!documents.length) {
    clearSummaryTarget();
    return;
  }

  const selected = Array.from(state.selected || []);
  let target = null;

  if (selected.length === 1) {
    target = documents.find(item => item.id === selected[0]) || null;
  }
  if (!target && summaryState.documentId) {
    target = documents.find(item => item.id === summaryState.documentId) || null;
  }
  if (!target) target = documents[0];   // newest upload

  if (target) setSummaryTarget(target, { autoRun: false });
  else clearSummaryTarget();
}

sEl.refresh.addEventListener('click', () => {
  if (summaryState.documentId) runSummary(summaryState.documentId);
});

/* ── Start ── */

(async function startSummary() {
  try {
    const settings = await (await fetch('/api/config')).json();
    summaryState.autoRun = Boolean(settings.summary_auto_run);
  } catch (err) {
    summaryState.autoRun = false;   // never auto-spend if we can't confirm
  }
  try {
    const response = await fetch(SUMMARY_API.questions);
    if (!response.ok) throw new Error(await errorFrom(response));
    summaryState.questions = await response.json();
  } catch (err) {
    // Panel stays hidden. The chat is entirely unaffected.
    summaryState.questions = [];
    return;
  }
  // The rail may already have rendered before this file finished loading.
  onDocumentsRendered();
})();
