/* Layer 3 coach tab — everything the L3 tab renders and stores.
   Loaded AFTER app.js (classic script): it reads app.js top-level globals
   (loaded, currentIdx, sessionId, testState, CY_STYLE, GRAPH_LIBS_OK,
   escapeHtml, logEvent, fileL1Errors) at call time, and app.js calls back
   into the functions defined here (l3ExpireAll, l3ConfirmNav,
   l3PageVisible, renderL3Tab, renderL3Boards, l3ResetDom) only at runtime,
   never during load — so the split is order-safe.
*/

// --- Layer 3 coach tab  --------------------------------------
// Read-only mirror of the selected circuit + two boards (Mode A upper /
// Mode B lower). This round builds the chrome, the L1 lock, the per-circuit
// sticky result store (cards expire on re-upload, l3.debug) and the
// circuit-switch guard; 

const l3ARunBtn  = document.getElementById("l3-a-run");
const l3BRunBtn  = document.getElementById("l3-b-run");

let l3Cy = null;
let l3GraphFilename = null;    // which file the mirror currently shows
let l3Store = {};              // filename -> { modeA, modeB, cards: [] }
const l3RunState = { a: false, b: false };   // set while an analysis runs (P2/P3)

function l3Slot(filename) {
  if (!l3Store[filename]) l3Store[filename] = { modeA: null, modeB: null, cards: [] };
  return l3Store[filename];
}

// Every /api/circuit POST re-uploads the whole set into a fresh session, so
// stored L3 results (incl. hypothesis cards) are stale the moment it succeeds.
function l3ExpireAll(reason) {
  const had = Object.values(l3Store).some(
    (s) => s && (s.modeA || s.modeB || (s.cards || []).length),
  );
  l3Store = {};
  l3GraphFilename = null;      // circuit content may have changed: rebuild mirror
  if (had && reason === "re-upload") logEvent("l3_circuit_re_uploaded", {});
}

function l3Busy() {
  return !!(l3RunState.a || l3RunState.b);
}

// Guard used by every circuit-switch path (file picker, dropdown, prev/next,
// clear, Test-all row). No-op while nothing runs; once Mode A/B jobs exist,
// switching circuits mid-run costs the run — make that explicit.
function l3ConfirmNav() {
  if (!l3Busy()) return true;
  return confirm(
    "A Layer-3 analysis is still running. Switching or clearing circuits " +
    "resets everything on the L3 boards. Continue?",
  );
}

function l3PageVisible() {
  const page = document.querySelector('.page[data-page="l3"]');
  return !!page && !page.hasAttribute("hidden");
}

function l3ResetDom() {
  if (l3Cy) { try { l3Cy.destroy(); } catch {} l3Cy = null; }
  l3GraphFilename = null;
  const ph = document.getElementById("l3-placeholder");
  if (ph) {
    ph.classList.remove("hidden");
    ph.innerHTML =
      `No circuit loaded. Add a <code>.dig</code> file from the toolbar ` +
      `above &mdash; the coach works on the file selected there.`;
  }
  const chip = document.getElementById("l3-file-chip");
  if (chip) chip.classList.add("hidden");
  renderL3Boards(null);
}

function renderL3Tab() {
  const file = loaded.length > 0 ? loaded[currentIdx] : null;
  renderL3Graph(file);
  renderL3Boards(file);
}

// The mirror is its own Cytoscape instance over a DEEP COPY of the exported
// graph (two instances must never share mutable element data), view-only:
// no dragging or selecting. Later phases draw animations and fix badges here.
function renderL3Graph(file) {
  const box = document.getElementById("l3-cy");
  const ph = document.getElementById("l3-placeholder");
  const chip = document.getElementById("l3-file-chip");
  if (!box || !ph || !chip) return;

  if (!file || file.error || !GRAPH_LIBS_OK) {
    if (l3Cy) { try { l3Cy.destroy(); } catch {} l3Cy = null; }
    l3GraphFilename = null;
    chip.classList.add("hidden");
    ph.classList.remove("hidden");
    if (!file) {
      ph.innerHTML =
        `No circuit loaded. Add a <code>.dig</code> file from the toolbar ` +
        `above &mdash; the coach works on the file selected there.`;
    } else if (file.error) {
      ph.innerHTML =
        `<span style="color:#991b1b">${escapeHtml(file.filename)} failed to ` +
        `parse &mdash; fix it on the Dashboard first.</span>`;
    } else {
      ph.innerHTML =
        `<span class="muted">Graph libraries unavailable; the boards on ` +
        `the right still work.</span>`;
    }
    return;
  }

  ph.classList.add("hidden");
  chip.textContent = "coaching " + file.filename;
  chip.classList.remove("hidden");

  if (l3GraphFilename === file.filename && l3Cy) {
    const inst = l3Cy;
    setTimeout(() => { try { inst.resize(); inst.fit(undefined, 40); } catch {} }, 0);
    return;
  }

  if (l3Cy) { try { l3Cy.destroy(); } catch {} l3Cy = null; }
  const elements = JSON.parse(JSON.stringify(
    { nodes: file.graph.nodes, edges: file.graph.edges },
  ));
  l3Cy = cytoscape({
    container: box,
    elements,
    style: CY_STYLE,
    layout: {
      name: "dagre", rankDir: "LR",
      nodeSep: 30, rankSep: 60, edgeSep: 10, animate: false,
    },
    wheelSensitivity: 0.2,
    minZoom: 0.15, maxZoom: 3,
    autoungrabify: true,
    boxSelectionEnabled: false,
    autounselectify: true,
  });
  const inst = l3Cy;
  inst.once("layoutstop", () => {
    setTimeout(() => { try { inst.resize(); inst.fit(undefined, 40); } catch {} }, 0);
  });
  l3GraphFilename = file.filename;
}

// Mode A analyzes the per-row failures of the selected file, so its readiness
// needs a finished per-row run (general mode has no row detail).
function l3FailingRowCount(filename) {
  const st = testState[filename];
  if (!st || st.status !== "done" || !st.payload || st.mode !== "per_row") return null;
  let n = 0;
  for (const spec of st.payload.specs || []) {
    for (const row of spec.rows || []) {
      if (row.status === "failed") n += 1;
    }
  }
  return n;
}

function _l3PaintBoard(which, state) {
  const board  = document.getElementById(`l3-board-${which}`);
  const lockEl = document.getElementById(`l3-${which}-lock`);
  const status = document.getElementById(`l3-${which}-status`);
  const body   = document.getElementById(`l3-${which}-body`);
  const btn    = which === "a" ? l3ARunBtn : l3BRunBtn;
  if (!board || !lockEl || !status || !body || !btn) return;
  board.classList.toggle("locked", !!state.locked);
  lockEl.classList.toggle("hidden", !state.locked);
  status.textContent = state.status;
  status.className = "l3-status " + (state.cls || "muted");
  body.innerHTML = state.bodyHtml || "";
  btn.disabled = !state.enabled;
}

function renderL3Boards(file) {
  const saved = file && !file.error ? l3Slot(file.filename) : null;
  const savedCard = (slotVal) => slotVal
    ? `<div class="l3-note-card">${escapeHtml(slotVal.note)}</div>`
    : "";

  // shared no-go states
  if (!file) {
    _l3PaintBoard("a", { status: "No file loaded.", cls: "muted" });
    _l3PaintBoard("b", { status: "No file loaded.", cls: "muted" });
    return;
  }
  if (file.error) {
    const s = { status: "This file failed to parse — fix it on the Dashboard.", cls: "blocked" };
    _l3PaintBoard("a", s);
    _l3PaintBoard("b", s);
    return;
  }
  const nErr = fileL1Errors(file).length;
  if (nErr > 0) {
    const s = {
      locked: true,
      cls: "blocked",
      status:
        `Locked: ${nErr} Layer-1 error${nErr === 1 ? "" : "s"} unresolved. ` +
        `Fix the structural errors on the Dashboard first.`,
    };
    _l3PaintBoard("a", s);
    _l3PaintBoard("b", s);
    return;
  }

  // Mode A (upper): needs failing rows from a finished per-row run. When
  // Mode B's accepted rows fail on the temp circuit, say so here — that is
  // the ratified hand-off hint (NO auto-run; the engine lands in Phase 3).
  const failing = l3FailingRowCount(file.filename);
  const mbA = saved.modeB;
  const coachHint = (mbA && mbA.injectFailing > 0)
    ? ` ALSO: ${mbA.injectFailing} coach row${mbA.injectFailing === 1 ? "" : "s"} ` +
      `fail on the temp circuit '${mbA.tempName || "coach copy"}' — Mode A ` +
      `will debug the temp file (engine lands in Phase 3).`
    : "";
  if (!file.summary || !file.summary.has_testcases) {
    _l3PaintBoard("a", {
      status: "This file has no testcases, so there are no failing rows to analyze." + coachHint,
      cls: "muted",
      bodyHtml: savedCard(saved.modeA),
    });
  } else if (failing === null) {
    _l3PaintBoard("a", {
      status:
        `Run tests in per-row mode on the Dashboard first — Mode A picks ` +
        `up the failing rows from there.` + coachHint,
      cls: coachHint ? "blocked" : "muted",
      bodyHtml: savedCard(saved.modeA),
    });
  } else if (failing === 0) {
    _l3PaintBoard("a", {
      status:
        "All rows pass — nothing to debug here. Try the Coverage Coach " +
        "below for gaps your tests might be missing." + coachHint,
      cls: coachHint ? "blocked" : "ready",
      bodyHtml: savedCard(saved.modeA),
    });
  } else {
    _l3PaintBoard("a", {
      status:
        `${failing} failing row${failing === 1 ? "" : "s"} detected — ` +
        `ready to analyze.` + coachHint,
      cls: "ready",
      enabled: true,
      bodyHtml: savedCard(saved.modeA),
    });
  }

  // Mode B (lower): L1-clean is the only gate (runs whether or not the
  // current tests pass); scope is tree-wide incl. subcircuit testcases.
  if (l3RunState.b) {
    _l3PaintBoard("b", {
      status: "Scanning this file and every subcircuit's testcases…",
      cls: "muted",
      bodyHtml: l3ModeBBodyHtml(saved.modeB),
    });
  } else if (saved.modeB && saved.modeB.report) {
    const mb = saved.modeB;
    const rep = mb.report;
    const n = rep.total_flags || 0;
    let status, cls;
    if (mb.locked) {
      status = "You're all set — every row (old and coach) passes. " +
               "Coverage Coach is done for today on this circuit.";
      cls = "ready";
    } else if (n > 0) {
      status = `Scan done: ${n} cell${n === 1 ? "" : "s"} where a test row ` +
               `and the circuit disagree — details below.`;
      cls = "blocked";
    } else {
      status = "Scan done: tests and circuit agree everywhere. " +
               "Coverage notes below.";
      cls = "ready";
    }
    _l3PaintBoard("b", {
      status,
      cls,
      enabled: !mb.locked,
      bodyHtml: l3ModeBBodyHtml(mb) + l3ProposalsHtml(mb) + l3InjectHtml(mb),
    });
  } else {
    _l3PaintBoard("b", {
      status:
        "Ready. Scans this file AND every subcircuit's testcases for rows " +
        "that assert the wrong value, then reports your coverage gaps.",
      cls: "ready",
      enabled: true,
      bodyHtml: savedCard(saved.modeB),
    });
  }
}

// --- Mode B: coverage scan render -------------------------------------------
// The board body for a finished /api/l3/coverage run: one section per circuit
// in the tree (root first), disagreement cards on top, then the coverage
// notes ("good report"). Falls back to the legacy stub note shape.

function l3ModeBBodyHtml(savedB) {
  if (!savedB) return "";
  if (!savedB.report) {
    return savedB.note
      ? `<div class="l3-note-card">${escapeHtml(savedB.note)}</div>`
      : "";
  }
  const rep = savedB.report;
  let html = "";
  for (const c of rep.circuits || []) {
    html += `<div class="l3-cov-circuit">`;
    html += `<div class="l3-cov-head">` +
      `<span class="l3-cov-file">${escapeHtml(c.file)}</span>` +
      _l3CircuitChips(c) + `</div>`;
    for (const f of c.flags || []) html += _l3FlagCardHtml(f);
    html += _l3NotesHtml(c.notes || []);
    html += `</div>`;
  }
  if ((rep.notes || []).length) {
    html += `<div class="l3-cov-circuit"><div class="l3-cov-head">` +
      `<span class="l3-cov-file">whole tree</span></div>` +
      _l3NotesHtml(rep.notes) + `</div>`;
  }
  return html;
}

function _l3CircuitChips(c) {
  const chips = [];
  if (!c.has_testcases) {
    chips.push(`<span class="l3-chip l3-chip-none">no tests</span>`);
  } else {
    chips.push(`<span class="l3-chip">${c.row_count} row${c.row_count === 1 ? "" : "s"}</span>`);
  }
  const flags = (c.flags || []).length;
  if (flags) {
    chips.push(`<span class="l3-chip l3-chip-bad">${flags} disagreement${flags === 1 ? "" : "s"}</span>`);
  }
  const unresolved = (c.specs || [])
    .reduce((n, s) => n + (s.unresolved_cells || 0), 0);
  if (unresolved) {
    // honesty guard visibility: these cells were counted, never accused
    chips.push(`<span class="l3-chip l3-chip-warn" title="The evaluator could not resolve these output cells, so they were never accused.">${unresolved} unchecked</span>`);
  }
  return chips.join("");
}

function _l3FlagCardHtml(f) {
  return `<div class="l3-flag">
    <div class="l3-flag-title">'${escapeHtml(f.spec_name)}' row ${f.row_index} &middot; ${escapeHtml(f.column)} — test and circuit disagree</div>
    <div class="l3-flag-body">This row expects <b>${escapeHtml(f.column)} = ${escapeHtml(f.asserted_fmt)}</b>, but the circuit as built computes <b>${escapeHtml(f.computed_fmt)}</b>. One of them is wrong: either the row's expected value is a typo — fix the testcase — or the circuit has a bug at this output (run per-row tests, then the Failed-test analysis above).</div>
  </div>`;
}

function _l3NotesHtml(notes) {
  if (!notes.length) return "";
  return `<ul class="l3-notes">` +
    notes.map((n) => `<li>${escapeHtml(n)}</li>`).join("") + `</ul>`;
}

// --- Mode B: proposals + accept-flow (2.3 UI + 2.7) --------------------------
// State lives in slot.modeB: { report, proposing, proposals, accepting,
// inject: {file: outcomeBody}, injectFailing, tempName, locked }.

function l3ProposalsHtml(mb) {
  if (!mb.report || mb.report.total_flags > 0 || mb.locked) return "";
  if (mb.proposing) {
    return `<div class="l3-note-card">Asking the coach for new rows…</div>`;
  }
  if (!mb.proposals) {
    return `<div class="l3-prop-bar">
      <button class="btn" data-l3-act="propose">Propose new test rows</button>
      <span class="l3-prop-hint">One hidden model call, grounded on the scan
      above; every row is validated, and nothing touches your file until you
      accept.</span></div>`;
  }
  const p = mb.proposals;
  if (p.error) {
    return `<div class="l3-note-card">Proposer unavailable: ${escapeHtml(p.error)}</div>` +
      `<div class="l3-prop-bar"><button class="btn" data-l3-act="propose">Try again</button></div>`;
  }
  if (!p.proposals.length) {
    return `<div class="l3-note-card">${escapeHtml((p.notes || []).join(" ") ||
      "No usable proposals this time.")}</div>` +
      `<div class="l3-prop-bar"><button class="btn" data-l3-act="propose">Try again</button></div>`;
  }
  let html = `<div class="l3-sec-title">Coach proposals
    <span class="muted">(model: ${escapeHtml(p.model || "?")})</span></div>`;
  p.proposals.forEach((g, gi) => {
    const rows = g.rows.map((r) =>
      `<div class="l3-prop-row">${escapeHtml(r)}</div>`).join("");
    html += `<label class="l3-prop-card">
      <input type="checkbox" data-l3-group="${gi}" checked />
      <div class="l3-prop-body">
        <div class="l3-prop-target">${escapeHtml(g.file)} · '${escapeHtml(g.spec_name)}'</div>
        ${rows}
        <div class="l3-prop-why">${escapeHtml(g.why)}</div>
      </div></label>`;
  });
  if ((p.notes || []).length) {
    html += `<div class="l3-prop-hint">${escapeHtml(p.notes.join(" "))}</div>`;
  }
  html += `<div class="l3-prop-bar">
    <button class="btn" data-l3-act="accept"${mb.accepting ? " disabled" : ""}>
      ${mb.accepting ? "Verifying on a temp copy…" : "Accept & verify selected"}
    </button>
    <span class="l3-prop-hint">Accepted rows run on a TEMP copy through the
    real simulator — your original file is never modified.</span></div>`;
  return html;
}

function l3InjectHtml(mb) {
  if (!mb.inject) return "";
  const current = loaded[currentIdx] ? loaded[currentIdx].filename : null;
  let html = `<div class="l3-sec-title">Verification on the temp circuit</div>`;
  for (const [file, out] of Object.entries(mb.inject)) {
    if (!out.ok) {
      html += `<div class="l3-note-card">${escapeHtml(file)}: ${escapeHtml(out.warning || "inject failed")}</div>`;
      continue;
    }
    const badge = out.outcome === "all_set"
      ? `<span class="l3-chip">all pass</span>`
      : `<span class="l3-chip l3-chip-bad">rows fail</span>`;
    const clickable = file === current;
    const headers = out.headers || [];
    const head = `<tr><td class="l3-idx">idx</td>` +
      headers.map((h) => `<td>${escapeHtml(h)}</td>`).join("") +
      `<td>status</td></tr>`;
    const rows = (out.rows || []).map((r) => {
      const cells = (r.raw || "").split(/\s+/).filter(Boolean).slice(0, headers.length);
      const tds = headers.map((_, i) => `<td>${escapeHtml(cells[i] ?? "")}</td>`).join("");
      const cls = [r.status === "failed" ? "l3-row-fail" : "",
                   r.added ? "l3-row-added" : "",
                   clickable ? "l3-row-click" : ""].join(" ").trim();
      const attrs = clickable
        ? ` data-l3-simfile="${escapeHtml(out.temp_filename || "")}"` +
          ` data-l3-spec="${out._spec_index ?? 0}" data-l3-row="${r.index}"`
        : "";
      return `<tr class="${cls}"${attrs}>
        <td class="l3-idx">${r.index}${r.added ? "＋" : ""}</td>${tds}
        <td>${escapeHtml(r.status)}</td></tr>`;
    }).join("");
    html += `<div class="l3-cov-circuit">
      <div class="l3-cov-head"><span class="l3-cov-file">${escapeHtml(file)}</span>${badge}
        <span class="l3-chip l3-chip-none">${escapeHtml(out.temp_filename || "")}</span></div>
      <div class="l3-inj-wrap"><table class="l3-inj-table">${head}${rows}</table></div>
      ${clickable
        ? `<div class="l3-prop-hint">Click a row to drive its signal flow on the circuit at the left — exactly like Layer 1. ＋ marks coach rows.</div>`
        : `<div class="l3-prop-hint">Rows for ${escapeHtml(file)} — switch to that file to view their signal flow (auto-drill lands with 2.8).</div>`}
    </div>`;
  }
  return html;
}

async function l3ProposeClick() {
  const file = loaded.length > 0 ? loaded[currentIdx] : null;
  if (!file || file.error || !sessionId) return;
  const slot = l3Slot(file.filename);
  if (!slot.modeB || !slot.modeB.report || slot.modeB.proposing) return;
  slot.modeB.proposing = true;
  logEvent("l3_modeB_propose_started", { filename: file.filename });
  renderL3Boards(file);
  let body = null;
  try {
    const res = await fetch("/api/l3/propose", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, filename: file.filename }),
    });
    body = res.ok ? await res.json()
                  : { ok: false, error: `Server error ${res.status}` };
  } catch (err) {
    body = { ok: false, error: `Network error: ${err}` };
  }
  slot.modeB.proposing = false;
  slot.modeB.proposals = body.ok
    ? body
    : { proposals: [], notes: [], model: body.model, error: body.error };
  logEvent("l3_modeB_proposed", {
    filename: file.filename, ok: !!body.ok,
    n_rows: (body.proposals || []).reduce((n, g) => n + g.rows.length, 0),
  });
  if (l3PageVisible() && loaded[currentIdx]
      && loaded[currentIdx].filename === file.filename) {
    renderL3Boards(loaded[currentIdx]);
  }
}

async function l3AcceptClick() {
  const file = loaded.length > 0 ? loaded[currentIdx] : null;
  if (!file || file.error || !sessionId) return;
  const slot = l3Slot(file.filename);
  const mb = slot.modeB;
  if (!mb || !mb.proposals || mb.accepting) return;
  const body = document.getElementById("l3-b-body");
  const picked = [];
  body.querySelectorAll("input[data-l3-group]:checked").forEach((cb) => {
    const g = mb.proposals.proposals[parseInt(cb.dataset.l3Group, 10)];
    if (g) picked.push(g);
  });
  if (!picked.length) return;

  mb.accepting = true;
  l3RunState.b = true;                       // circuit-switch guard is live
  logEvent("l3_modeB_accept_started", {
    filename: file.filename,
    n_rows: picked.reduce((n, g) => n + g.rows.length, 0),
  });
  renderL3Boards(file);

  mb.inject = {};
  mb.injectFailing = 0;
  let allSet = true;
  for (const g of picked) {                  // one inject per target file
    let out;
    try {
      const res = await fetch("/api/l3/inject", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId, filename: g.file,
          spec_name: g.spec_name, rows: g.rows,
        }),
      });
      out = res.ok ? await res.json()
                   : { ok: false, warning: `Server error ${res.status}` };
    } catch (err) {
      out = { ok: false, warning: `Network error: ${err}` };
    }
    if (out.ok) {
      // spec index inside that file (temp preserves testcase order)
      const cov = (mb.report.circuits || []).find((c) => c.file === g.file);
      const sp = cov && (cov.specs || []).find((s) => s.name === g.spec_name);
      out._spec_index = sp ? sp.spec_index : 0;
      const failedAdded = (out.rows || [])
        .filter((r) => r.added && r.status === "failed").length;
      mb.injectFailing += failedAdded;
      if (g.file === file.filename) mb.tempName = out.temp_filename;
      if (out.outcome !== "all_set") allSet = false;
    } else {
      allSet = false;
    }
    mb.inject[g.file] = out;
    logEvent("l3_modeB_inject_outcome", {
      file: g.file, outcome: out.outcome || "error",
    });
  }
  mb.accepting = false;
  l3RunState.b = false;
  if (allSet && mb.injectFailing === 0) {
    mb.locked = true;                        // "you're all set" — done today
    logEvent("l3_modeB_all_set", { filename: file.filename });
  }
  if (l3PageVisible() && loaded[currentIdx]
      && loaded[currentIdx].filename === file.filename) {
    renderL3Boards(loaded[currentIdx]);
  }
}

async function l3SimTempRow(tr) {
  const filename = tr.dataset.l3Simfile;
  const specIdx = parseInt(tr.dataset.l3Spec, 10) || 0;
  const rowIdx = parseInt(tr.dataset.l3Row, 10);
  if (!filename || Number.isNaN(rowIdx) || !sessionId || !l3Cy) return;
  let sim;
  try {
    const res = await fetch("/api/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId, filename,
        spec_index: specIdx, row_index: rowIdx,
      }),
    });
    if (!res.ok) return;
    sim = await res.json();
  } catch { return; }
  if (!sim || sim.ok === false) return;
  document.querySelectorAll("#l3-b-body tr.l3-row-sel")
    .forEach((t) => t.classList.remove("l3-row-sel"));
  tr.classList.add("l3-row-sel");
  applySignalFlow(sim, l3Cy);               // same painter Layer 1 uses
  logEvent("l3_modeB_temp_row_viewed", { filename, row: rowIdx });
}

// One delegated listener serves every dynamically rendered control.
(function wireL3BoardB() {
  const body = document.getElementById("l3-b-body");
  if (!body) return;
  body.addEventListener("click", (evt) => {
    const btn = evt.target.closest("[data-l3-act]");
    if (btn) {
      if (btn.dataset.l3Act === "propose") l3ProposeClick();
      if (btn.dataset.l3Act === "accept") l3AcceptClick();
      return;
    }
    const tr = evt.target.closest("tr[data-l3-simfile]");
    if (tr) l3SimTempRow(tr);
  });
})();

// Skeleton run buttons: store a note card in the per-circuit slot so the
// stickiness is testable end-to-end (switch file and back — it persists;
// re-upload — it's gone). Phases 2/3 replace these bodies with real runs.
l3ARunBtn.addEventListener("click", () => {
  const file = loaded.length > 0 ? loaded[currentIdx] : null;
  if (!file || file.error) return;
  logEvent("l3_modeA_stub_clicked", { filename: file.filename });
  l3Slot(file.filename).modeA = {
    stub: true,
    note:
      "Board wired and ready — the Mode A engine (cluster failing rows → " +
      "hypothesize → verify the fix on a temp copy → animated diagnosis) " +
      "lands in Phase 3. This card is stored per circuit: switch files and " +
      "come back, it persists; re-upload clears it.",
  };
  renderL3Boards(file);
});

// Mode B run: synchronous scan (sub-second even on a full CPU tree). The
// result is stored per circuit (sticky; expires on re-upload) under the
// filename the run STARTED on, so a mid-run circuit switch can't misfile it.
l3BRunBtn.addEventListener("click", async () => {
  const file = loaded.length > 0 ? loaded[currentIdx] : null;
  if (!file || file.error || !sessionId || l3RunState.b) return;
  const filename = file.filename;
  logEvent("l3_modeB_run_started", { filename });

  l3RunState.b = true;
  l3BRunBtn.textContent = "Scanning…";
  renderL3Boards(file);

  let body = null;
  let failText = null;
  try {
    const res = await fetch("/api/l3/coverage", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, filename }),
    });
    if (!res.ok) failText = `Server error ${res.status}: ${await res.text()}`;
    else body = await res.json();
  } catch (err) {
    failText = `Network error: ${err}`;
  }
  l3RunState.b = false;
  l3BRunBtn.textContent = "Check my test coverage";

  if (body && body.ok) {
    l3Slot(filename).modeB = { report: body };
    logEvent("l3_modeB_run_complete", {
      filename, ok: true, total_flags: body.total_flags || 0,
    });
  } else {
    const warn = failText || (body && body.warning) || "Scan failed.";
    l3Slot(filename).modeB = null;
    logEvent("l3_modeB_run_complete", { filename, ok: false });
    const status = document.getElementById("l3-b-status");
    if (status && l3PageVisible()
        && loaded[currentIdx] && loaded[currentIdx].filename === filename) {
      renderL3Boards(loaded[currentIdx]);
      status.textContent = `Coverage scan failed: ${warn}`;
      status.className = "l3-status blocked";
      return;
    }
  }
  // re-render only if the user is still looking at the file the run was for
  if (l3PageVisible() && loaded[currentIdx]
      && loaded[currentIdx].filename === filename) {
    renderL3Boards(loaded[currentIdx]);
  }
});