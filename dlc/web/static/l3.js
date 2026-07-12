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

  // Mode A (upper): needs failing rows from a finished per-row run.
  const failing = l3FailingRowCount(file.filename);
  if (!file.summary || !file.summary.has_testcases) {
    _l3PaintBoard("a", {
      status: "This file has no testcases, so there are no failing rows to analyze.",
      cls: "muted",
      bodyHtml: savedCard(saved.modeA),
    });
  } else if (failing === null) {
    _l3PaintBoard("a", {
      status:
        `Run tests in per-row mode on the Dashboard first — Mode A picks ` +
        `up the failing rows from there.`,
      cls: "muted",
      bodyHtml: savedCard(saved.modeA),
    });
  } else if (failing === 0) {
    _l3PaintBoard("a", {
      status:
        "All rows pass — nothing to debug here. Try the Coverage Coach " +
        "below for gaps your tests might be missing.",
      cls: "ready",
      bodyHtml: savedCard(saved.modeA),
    });
  } else {
    _l3PaintBoard("a", {
      status:
        `${failing} failing row${failing === 1 ? "" : "s"} detected — ` +
        `ready to analyze.`,
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
    const rep = saved.modeB.report;
    const n = rep.total_flags || 0;
    _l3PaintBoard("b", {
      status: n > 0
        ? `Scan done: ${n} cell${n === 1 ? "" : "s"} where a test row and ` +
          `the circuit disagree — details below.`
        : "Scan done: tests and circuit agree everywhere. Coverage notes below.",
      cls: n > 0 ? "blocked" : "ready",
      enabled: true,
      bodyHtml: l3ModeBBodyHtml(saved.modeB),
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