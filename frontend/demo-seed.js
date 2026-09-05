/* ════════════════════════════════════════════════════════════
   HARDCODED DEMO DATA — the only place seeded contracts live.

   Everything in this file is INVENTED. These companies do not exist, no
   document was uploaded for any of them, and none of it comes from an API.
   It exists so the "Contracts in progress" section looks populated during a
   demo instead of showing a single real upload against empty space.

   TO REMOVE THE DEMO BACKDROP ENTIRELY: set DEMO_CONTRACTS to [] below, or
   delete the <script src="demo-seed.js"> tag from index.html. Nothing else
   references this data.

   DEMO-SAFE BY DESIGN: this is a plain array. Rendering it makes no network
   call, so the section still fills in with the API down, the key missing or
   the machine offline. Real contracts are fetched separately and merged in
   when available; if that fetch fails, these still render.

   Every card built from this file is tagged "Demo data" in the UI, and its
   chat button declines honestly — there is no source document behind it.
   ════════════════════════════════════════════════════════════ */

/* Reliability grade boundaries. The percentage and the grade are both DERIVED
   from onTimeOrders / totalOrders at render time (see reliabilityFor below), so
   the three can never drift out of step with each other. */
const RELIABILITY_GRADES = [
  { min: 93, label: 'Reliable', tone: 'ok' },
  { min: 80, label: 'Watch',    tone: 'warn' },
  { min: 0,  label: 'Poor',     tone: 'bad' }
];

/* Derive percentage and grade from the raw order history. Single source of
   truth: change onTimeOrders/totalOrders and everything else follows. */
function reliabilityFor(seed) {
  const { onTimeOrders, totalOrders } = seed.reliability;
  const percent = Math.round((onTimeOrders / totalOrders) * 100);
  const grade = RELIABILITY_GRADES.find(g => percent >= g.min);
  return { onTimeOrders, totalOrders, percent, label: grade.label, tone: grade.tone };
}

/* ── The seeded contracts ──
   stage:     index into TIMELINE_STAGES — 0 Uploaded, 1 Signed, 2 Dispatched,
              3 In transit, 4 Delivered
   condition: 'on-time' | 'at-risk' | 'late'
   advanceable: true  -> gets the manual demo stage controls, so it can be
              pushed into "trending late" live to trigger the drafted email  */
const DEMO_CONTRACTS = [
  {
    id: 'demo-northbridge',
    company: 'Northbridge Freight Ltd',
    description: 'Master logistics agreement — palletised freight, UK & EU lanes',
    reference: 'MSA-2026-0114',
    stage: 3,
    condition: 'at-risk',
    advanceable: true,              // demo-advanceable
    signedOn: '14 Jan 2026',
    dueOn: '12 Sep 2026',
    value: '£240,000 / year',
    reliability: { onTimeOrders: 38, totalOrders: 44 }
  },
  {
    id: 'demo-vertex',
    company: 'Vertex IT Services',
    description: 'Managed infrastructure SLA — 99.9% uptime, 4h response',
    reference: 'SLA-2026-0088',
    stage: 1,
    condition: 'on-time',
    advanceable: true,              // demo-advanceable
    signedOn: '02 Mar 2026',
    dueOn: '30 Sep 2026',
    value: '£96,000 / year',
    reliability: { onTimeOrders: 51, totalOrders: 52 }
  },
  {
    id: 'demo-caldera',
    company: 'Caldera Components',
    description: 'Supply of precision fittings — Schedule 2 quarterly draw-down',
    reference: 'PO-4471',
    stage: 2,
    condition: 'on-time',
    signedOn: '19 Apr 2026',
    dueOn: '18 Sep 2026',
    value: '£58,400',
    reliability: { onTimeOrders: 27, totalOrders: 30 }
  },
  {
    id: 'demo-halden',
    company: 'Halden Packaging Co.',
    description: 'Corrugated packaging supply — volume tiers at 5k / 12k units',
    reference: 'SUP-2026-0233',
    stage: 4,
    condition: 'on-time',
    signedOn: '08 Feb 2026',
    dueOn: '21 Aug 2026',
    value: '£31,900',
    reliability: { onTimeOrders: 61, totalOrders: 63 }
  },
  {
    id: 'demo-orrin',
    company: 'Orrin Chemicals',
    description: 'Reagent supply agreement — temperature-controlled delivery',
    reference: 'SUP-2026-0190',
    stage: 3,
    condition: 'late',
    signedOn: '27 Jan 2026',
    dueOn: '24 Aug 2026',
    value: '£112,750',
    reliability: { onTimeOrders: 19, totalOrders: 31 }
  },
  {
    id: 'demo-lyndhurst',
    company: 'Lyndhurst Print Group',
    description: 'Print and fulfilment MOU — marketing collateral',
    reference: 'MOU-2026-0042',
    stage: 1,
    condition: 'on-time',
    signedOn: '11 Jun 2026',
    dueOn: '15 Oct 2026',
    value: '£18,200',
    reliability: { onTimeOrders: 22, totalOrders: 26 }
  },
  {
    id: 'demo-arkwright',
    company: 'Arkwright Steel',
    description: 'Structural steel purchase order — staged delivery',
    reference: 'PO-4488',
    stage: 2,
    condition: 'at-risk',
    signedOn: '03 May 2026',
    dueOn: '09 Sep 2026',
    value: '£287,000',
    reliability: { onTimeOrders: 33, totalOrders: 41 }
  },
  {
    id: 'demo-mesner',
    company: 'Mesner Instruments',
    description: 'Calibration services contract — annual, auto-renewing',
    reference: 'SVC-2026-0117',
    stage: 4,
    condition: 'on-time',
    signedOn: '22 Nov 2025',
    dueOn: '14 Aug 2026',
    value: '£44,600 / year',
    reliability: { onTimeOrders: 47, totalOrders: 48 }
  },
  {
    id: 'demo-pellworth',
    company: 'Pellworth Textiles',
    description: 'Woven materials supply — minimum 15,000 units per contract year',
    reference: 'SUP-2026-0261',
    stage: 3,
    condition: 'on-time',
    signedOn: '30 Mar 2026',
    dueOn: '26 Sep 2026',
    value: '£73,300',
    reliability: { onTimeOrders: 29, totalOrders: 38 }
  }
];
