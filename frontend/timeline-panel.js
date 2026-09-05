/* ────────────────────────────────────────────────────────────
   "Contracts in progress" — the progress timeline.

   TWO KINDS OF CARD, always visually distinguishable:

   1. REAL contracts — documents the user uploaded and then finalized. The name,
      page count and dates are real, and "Open chat" reopens the Q&A engine
      scoped to that stored document.
   2. DEMO contracts — the invented backdrop in demo-seed.js. Tagged "Demo data",
      and "Open chat" declines honestly because there is no source document.

   WHAT IS SIMULATED ON BOTH: the delivery stage. Nothing here is connected to a
   carrier, an ERP or a delivery feed. A stage only ever changes because someone
   pressed a demo control. The controls are set apart in their own bar so a
   hand-driven simulation is never mistaken for live tracking.

   NOTHING IS EVER SENT. At-risk and late cards compose a supplier email and show
   it as a draft to review. There is no send path in this file — no fetch to any
   mail service, no mailto auto-trigger. "Review & send" is deliberately inert.

   STATE: stage positions live in `timelineState.cards`, an in-memory object that
   dies with the page. Reloading resets every card to its starting stage. That is
   deliberate — persisting hand-driven stages would imply monitoring we don't do.
   The DOCUMENTS themselves are persisted in SQLite and are unaffected.

   DEMO-SAFE: seeded cards render synchronously from demo-seed.js with no network
   call. Real contracts are fetched separately and merged in when that succeeds;
   if the API is down, the seeded backdrop still renders.
   ──────────────────────────────────────────────────────────── */

const TIMELINE_STAGES = [
  { key: 'uploaded',   label: 'Uploaded' },
  { key: 'signed',     label: 'Signed' },
  { key: 'dispatched', label: 'Dispatched' },
  { key: 'transit',    label: 'In transit' },
  { key: 'delivered',  label: 'Delivered' }
];

const TIMELINE_CONDITIONS = {
  'on-time': { label: 'On time',       tone: 'ok'   },
  'at-risk': { label: 'Trending late', tone: 'warn' },
  'late':    { label: 'Late',          tone: 'bad'  }
};

const TONE = {
  ok:   { text: 'text-ok',    chip: 'text-ok border-ok/25 bg-ok/5',       dot: '#0E7A5F' },
  warn: { text: 'text-warn',  chip: 'text-warn border-warn/30 bg-warn/5', dot: '#B45309' },
  bad:  { text: 'text-bad',   chip: 'text-bad border-bad/20 bg-bad/5',    dot: '#B42318' }
};

const DONE_GREEN = '#0E7A5F';
const PENDING_GREY = '#E3E6EA';

/* In-memory only — see header. contractId -> { stage, condition } */
const timelineState = { cards: {}, real: [], declined: {} };

const tEl = {
  cards: document.getElementById('timelineCards'),
  empty: document.getElementById('timelineEmpty')
};

/* ── Contract list: seeds (no network) merged with real finalized contracts ── */

function seededContracts() {
  // demo-seed.js may have been removed entirely; that is a supported state.
  if (typeof DEMO_CONTRACTS === 'undefined') return [];
  return DEMO_CONTRACTS.map(seed => ({
    id: seed.id,
    isDemo: true,
    title: seed.company,
    description: seed.description,
    reference: seed.reference,
    value: seed.value,
    signedOn: seed.signedOn,
    dueOn: seed.dueOn,
    advanceable: Boolean(seed.advanceable),
    reliability: reliabilityFor(seed),
    startStage: seed.stage,
    startCondition: seed.condition
  }));
}

function realContracts() {
  return (timelineState.real || []).map(document_ => ({
    id: document_.id,
    isDemo: false,
    title: document_.filename,
    description: `Uploaded contract · ${document_.page_count} page${document_.page_count === 1 ? '' : 's'}`,
    reference: null,
    value: null,
    signedOn: formatDate(document_.finalized_at),
    dueOn: null,
    advanceable: true,          // real cards keep the demo stage controls
    reliability: null,          // no delivery history exists for a real upload
    startStage: 1,              // finalizing means signed
    startCondition: 'on-time',
    document: document_
  }));
}

function allContracts() {
  // Real contracts first: they are the point, the seeds are backdrop.
  return [...realContracts(), ...seededContracts()];
}

function formatDate(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' });
}

function cardState(contract) {
  if (!timelineState.cards[contract.id]) {
    timelineState.cards[contract.id] = {
      stage: contract.startStage,
      condition: contract.startCondition
    };
  }
  return timelineState.cards[contract.id];
}

/* ── Drafted supplier email ──
   Real contracts get their genuine filename; everything not actually known
   stays a [bracketed placeholder]. We never invent a supplier contact, a PO
   number or a delivery date for a real contract, because this build has no
   structured extraction to draw them from. */
function draftSupplierEmail(contract, state_) {
  const late = state_.condition === 'late';
  const stage = TIMELINE_STAGES[state_.stage].label.toLowerCase();
  const subjectName = contract.isDemo
    ? `${contract.company || contract.title}${contract.reference ? ' (' + contract.reference + ')' : ''}`
    : contract.title;
  const due = contract.dueOn || '[scheduled delivery date]';

  const subject = late
    ? `Late delivery — ${subjectName} — revised date required`
    : `Delivery at risk — ${subjectName} — confirmation requested`;

  const body = [
    'Dear [supplier contact],',
    '',
    late
      ? `We are writing about the consignment under ${subjectName}, which has not been received by the scheduled delivery date of ${due}.`
      : `We are writing about the consignment under ${subjectName}. It is currently recorded as ${stage} and is at risk of missing the scheduled delivery date of ${due}.`,
    '',
    'Please confirm by return:',
    '  1. the revised delivery date;',
    '  2. the cause of the delay; and',
    '  3. whether any part of the consignment can be released earlier.',
    '',
    late
      ? 'We reserve our rights under the agreement in respect of late delivery, including any compensation or credit provided for at [clause reference]. Please treat this as written notice for the purposes of that clause.'
      : 'If the date cannot be met, please give written notice within the period required by the agreement at [clause reference].',
    '',
    'Regards,',
    '[Your name]',
    '[Your company]'
  ].join('\n');

  return { subject, body };
}

/* ── Stage progression rail ── */

function stageRail(state_) {
  const condition = TIMELINE_CONDITIONS[state_.condition];
  const tone = TONE[condition.tone];

  const segments = TIMELINE_STAGES.map((stage, index) => {
    const done = index < state_.stage;
    const current = index === state_.stage;

    let dot;
    if (done) {
      dot = `<span class="block w-2.5 h-2.5 rounded-full" style="background:${DONE_GREEN}"></span>`;
    } else if (current) {
      dot = `<span class="block w-3.5 h-3.5 rounded-full ring-4" style="background:${tone.dot};--tw-ring-color:${tone.dot}22"></span>`;
    } else {
      dot = `<span class="block w-2.5 h-2.5 rounded-full bg-white border-[1.5px]" style="border-color:${PENDING_GREY}"></span>`;
    }

    const labelClass = current ? 'text-ink font-medium' : done ? 'text-muted' : 'text-faint';
    const barBefore = index === 0 ? '' :
      `<span class="h-px flex-1" style="background:${index <= state_.stage ? DONE_GREEN : PENDING_GREY}"></span>`;
    const barAfter = index === TIMELINE_STAGES.length - 1 ? '' :
      `<span class="h-px flex-1" style="background:${index < state_.stage ? DONE_GREEN : PENDING_GREY}"></span>`;

    return `
      <div class="flex-1 min-w-0">
        <div class="flex items-center">${barBefore}${dot}${barAfter}</div>
        <p class="font-mono text-[10.5px] mt-2 text-center leading-tight ${labelClass}">${esc(stage.label)}</p>
      </div>`;
  }).join('');

  return `
    <div class="flex items-start gap-0">${segments}</div>
    <p class="mt-3 text-[12.5px] text-center ${tone.text}">
      ${esc(TIMELINE_STAGES[state_.stage].label)} · ${esc(condition.label)}
    </p>`;
}

/* ── Meta row: dates, value, reliability ── */

function metaRow(contract) {
  const cells = [];

  if (contract.signedOn) cells.push({ label: 'Signed', value: contract.signedOn });
  if (contract.dueOn) cells.push({ label: 'Delivery due', value: contract.dueOn });
  if (contract.value) cells.push({ label: 'Value', value: contract.value });

  let reliabilityCell = '';
  if (contract.reliability) {
    const tone = TONE[contract.reliability.tone];
    reliabilityCell = `
      <div class="min-w-0">
        <p class="font-mono text-[10.5px] text-faint uppercase tracking-wider">Reliability</p>
        <p class="text-[13.5px] mt-1 flex items-center gap-2 flex-wrap">
          <span class="font-medium">${contract.reliability.percent}%</span>
          <span class="font-mono text-[11.5px] text-muted">${contract.reliability.onTimeOrders}/${contract.reliability.totalOrders} on time</span>
          <span class="font-mono text-[10.5px] border rounded px-1.5 py-0.5 ${tone.chip}">${esc(contract.reliability.label)}</span>
        </p>
      </div>`;
  } else {
    // A real upload has no monitored delivery history — say so rather than
    // showing a flattering blank or, worse, an invented score.
    reliabilityCell = `
      <div class="min-w-0">
        <p class="font-mono text-[10.5px] text-faint uppercase tracking-wider">Reliability</p>
        <p class="text-[13px] mt-1 text-faint leading-[1.45]">No delivery history yet</p>
      </div>`;
  }

  const dateCells = cells.map(cell => `
    <div class="min-w-0">
      <p class="font-mono text-[10.5px] text-faint uppercase tracking-wider">${esc(cell.label)}</p>
      <p class="text-[13.5px] mt-1">${esc(cell.value)}</p>
    </div>`).join('');

  return `<div class="grid grid-cols-2 md:grid-cols-4 gap-x-5 gap-y-4">${dateCells}${reliabilityCell}</div>`;
}

/* ── Card ── */

function contractCard(contract) {
  const state_ = cardState(contract);
  const condition = TIMELINE_CONDITIONS[state_.condition];
  const tone = TONE[condition.tone];
  const atRisk = state_.condition !== 'on-time';
  const draft = atRisk ? draftSupplierEmail(contract, state_) : null;

  const originTag = contract.isDemo
    ? '<span class="font-mono text-[10px] text-warn border border-warn/30 bg-warn/5 rounded px-1.5 py-0.5">Demo data</span>'
    : '<span class="font-mono text-[10px] text-ok border border-ok/25 bg-ok/5 rounded px-1.5 py-0.5">Your contract</span>';

  const subline = [contract.reference, contract.description].filter(Boolean).join(' · ');

  const draftPanel = draft ? `
    <div class="border-t border-line bg-surface px-5 py-4">
      <div class="flex flex-wrap items-center justify-between gap-3">
        <div class="flex flex-wrap items-center gap-2">
          <p class="text-[13.5px] font-medium">Drafted supplier email</p>
          <span class="font-mono text-[10px] text-warn border border-warn/30 bg-warn/5 rounded px-1.5 py-0.5">draft · not sent</span>
        </div>
        <div class="flex items-center gap-2">
          <button type="button" data-role="copy" class="text-[12.5px] font-medium border border-line bg-white px-3 py-1.5 rounded-md hover:border-ink transition-colors">Copy draft</button>
          <!-- Intentionally inert: this build has no send path of any kind. -->
          <button type="button" data-role="send" class="text-[12.5px] font-medium border border-line bg-white px-3 py-1.5 rounded-md text-faint cursor-not-allowed">Review &amp; send</button>
        </div>
      </div>
      <p class="mt-2 text-[12.5px] text-muted leading-[1.55]">
        Nothing is sent by Provision. This is a draft for you to read, edit and send yourself. Text in [brackets] needs filling in.
      </p>
      <div class="mt-3 bg-white border border-line rounded-lg p-4">
        <p class="font-mono text-[10.5px] text-faint uppercase tracking-wider">Subject</p>
        <p class="text-[13.5px] mt-1">${esc(draft.subject)}</p>
        <p class="font-mono text-[10.5px] text-faint uppercase tracking-wider mt-4">Body</p>
        <pre class="mt-1 text-[13px] leading-[1.6] whitespace-pre-wrap font-sans text-ink/90">${esc(draft.body)}</pre>
      </div>
      <p data-role="copy-note" class="mt-2 font-mono text-[11px] text-ok hidden">Draft copied — nothing was sent.</p>
    </div>` : '';

  const demoControls = contract.advanceable ? `
    <div class="flex flex-wrap items-center gap-x-3 gap-y-2 border border-dashed border-line rounded-lg px-3 py-2 bg-surface">
      <span class="font-mono text-[10px] text-warn uppercase tracking-wider">Demo controls</span>
      <div class="flex items-center gap-1">
        <button type="button" data-role="back" class="text-[12px] border border-line bg-white px-2 py-1 rounded hover:border-ink transition-colors disabled:opacity-40 disabled:hover:border-line" ${state_.stage === 0 ? 'disabled' : ''}>&larr;</button>
        <button type="button" data-role="next" class="text-[12px] border border-line bg-white px-2 py-1 rounded hover:border-ink transition-colors disabled:opacity-40 disabled:hover:border-line" ${state_.stage === TIMELINE_STAGES.length - 1 ? 'disabled' : ''}>&rarr;</button>
      </div>
      <div class="flex items-center gap-1">
        ${Object.entries(TIMELINE_CONDITIONS).map(([key, value]) => `
          <button type="button" data-role="condition" data-condition="${key}"
            class="text-[12px] px-2 py-1 rounded border transition-colors ${
              state_.condition === key ? TONE[value.tone].chip : 'border-line bg-white text-muted hover:border-ink'
            }">${esc(value.label)}</button>`).join('')}
      </div>
      <button type="button" data-role="reset" class="text-[12px] text-muted hover:text-ink transition-colors">Reset</button>
    </div>` : '';

  return `
    <article class="border border-line rounded-xl bg-white overflow-hidden" data-contract="${esc(contract.id)}" data-demo="${contract.isDemo}">
      <div class="px-5 py-4 flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-line">
        <div class="min-w-0 flex-1">
          <div class="flex items-center gap-2 flex-wrap">
            <h3 class="text-[15.5px] font-medium truncate">${esc(contract.title)}</h3>
            ${originTag}
          </div>
          ${subline ? `<p class="font-mono text-[11px] text-faint mt-1.5 truncate">${esc(subline)}</p>` : ''}
        </div>
        <span class="font-mono text-[11px] border rounded px-2 py-0.5 shrink-0 ${tone.chip}">${esc(condition.label)}</span>
      </div>

      <div class="px-5 pt-5 pb-4">${stageRail(state_)}</div>

      <div class="px-5 pb-5">${metaRow(contract)}</div>

      <div class="px-5 py-3 border-t border-line flex flex-wrap items-center justify-between gap-3">
        <div class="flex items-center gap-2">
          <button type="button" data-role="chat" class="text-[13px] font-medium border border-line bg-white px-3 py-1.5 rounded-md hover:border-ink transition-colors">Ask about this contract</button>
        </div>
        ${demoControls}
      </div>

      <p data-role="decline" class="hidden px-5 pb-4 -mt-1 text-[12.5px] text-warn leading-[1.5]"></p>

      ${draftPanel}
    </article>`;
}

/* ── Rendering ── */

function renderTimeline() {
  const contracts = allContracts();

  tEl.empty.classList.toggle('hidden', contracts.length > 0);
  tEl.cards.innerHTML = contracts.map(contractCard).join('');

  // Drop stage state for contracts that no longer exist (deleted documents).
  const live = new Set(contracts.map(c => c.id));
  Object.keys(timelineState.cards).forEach(id => {
    if (!live.has(id)) delete timelineState.cards[id];
  });

  contracts.forEach(contract => {
    const card = tEl.cards.querySelector(`article[data-contract="${CSS.escape(contract.id)}"]`);
    if (!card) return;
    const state_ = cardState(contract);
    const on = (role, handler) => {
      const button = card.querySelector(`[data-role="${role}"]`);
      if (button) button.addEventListener('click', handler);
    };

    on('next', () => {
      state_.stage = Math.min(state_.stage + 1, TIMELINE_STAGES.length - 1);
      renderTimeline();
    });
    on('back', () => {
      state_.stage = Math.max(state_.stage - 1, 0);
      renderTimeline();
    });
    on('reset', () => {
      state_.stage = contract.startStage;
      state_.condition = contract.startCondition;
      renderTimeline();
    });
    card.querySelectorAll('[data-role="condition"]').forEach(button => {
      button.addEventListener('click', () => {
        state_.condition = button.dataset.condition;
        renderTimeline();
      });
    });

    // Open chat. Real contracts reopen the Q&A engine scoped to their stored
    // document; demo contracts have no document, and say so rather than
    // pretending to answer.
    on('chat', () => {
      const note = card.querySelector('[data-role="decline"]');
      if (contract.isDemo) {
        note.textContent =
          'Sample demo contract — no source document attached, so chat isn’t available here. ' +
          'Upload and finalize a real contract to ask questions about it.';
        note.classList.remove('hidden');
        return;
      }
      if (typeof openChatForDocument === 'function') {
        openChatForDocument(contract.document);
      }
    });

    // Copy writes to the clipboard. It transmits nothing.
    on('copy', async () => {
      const draft = draftSupplierEmail(contract, state_);
      try {
        await navigator.clipboard.writeText(`Subject: ${draft.subject}\n\n${draft.body}`);
        const note = card.querySelector('[data-role="copy-note"]');
        if (note) {
          note.classList.remove('hidden');
          setTimeout(() => note.classList.add('hidden'), 4000);
        }
      } catch (err) {
        /* Clipboard unavailable (insecure context or denied). The draft is on
           screen and can be selected by hand, so no error state is warranted. */
      }
    });

    on('send', () => {
      window.alert(
        'Sending is not implemented in this build.\n\n' +
        'Provision never sends email on your behalf. Copy the draft, review it, ' +
        'and send it from your own mail client.'
      );
    });
  });
}

/* Fetch the user's finalized contracts and merge them in. Failure is survivable
   on purpose: the seeded backdrop has already rendered without any network. */
async function loadFinalizedContracts() {
  try {
    const response = await fetch('/api/documents?status=finalized');
    if (!response.ok) throw new Error('unavailable');
    timelineState.real = await response.json();
  } catch (err) {
    timelineState.real = timelineState.real || [];
  }
  renderTimeline();
}

/* Called from renderDocuments() in index.html via a guarded hook. */
function onDocumentsRenderedTimeline() {
  loadFinalizedContracts();
  refreshIllustrativeEmptyStates();
}

/* The obligation register and alerts sections are sample-only. Their empty
   states quote the real document count so it is obvious the sample rows there
   are not derived from the user's documents. */
function refreshIllustrativeEmptyStates() {
  const count = (typeof state !== 'undefined' && state.documents) ? state.documents.length : 0;
  const phrase = count === 0
    ? 'You have no documents in the workspace right now.'
    : `Your ${count} workspace document${count === 1 ? '' : 's'} ${count === 1 ? 'has' : 'have'} not been through extraction — nothing below comes from ${count === 1 ? 'it' : 'them'}.`;

  const obligations = document.getElementById('obligationEmptyCount');
  if (obligations) obligations.textContent = phrase;

  const alerts = document.getElementById('alertsEmptyCount');
  if (alerts) {
    alerts.textContent = count === 0
      ? 'You have no documents in the workspace right now.'
      : `None of your ${count} workspace document${count === 1 ? '' : 's'} is being monitored.`;
  }
}

/* Seeded cards paint immediately, with no network call — the demo backdrop is
   up before any request is made, and stays up if every request fails. */
renderTimeline();
loadFinalizedContracts();
