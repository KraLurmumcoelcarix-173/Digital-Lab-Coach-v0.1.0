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

  l3HideDrillBar();          // returning to the selected file ends any drill view
  l3BuildMirror(file);
}

// Build the mirror for ANY loaded entry (the selected file normally; during a
// auto-drill descent, each parent level and finally the child as own top).
function l3BuildMirror(file) {
  const box = document.getElementById("l3-cy");
  if (!box || !file || file.error || !file.graph) return;
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
    boxSelectionEnabled: false,
    autounselectify: true,
  });
  const inst = l3Cy;
  inst.once("layoutstop", () => {
    setTimeout(() => { try { inst.resize(); inst.fit(undefined, 40); } catch {} }, 0);
  });
  l3WireMirrorInteractions(inst);
  l3ApplyNetIds();               // keep the net-id toggle across rebuilds
  l3GraphFilename = file.filename;
}

// the mirror interacts like the Layer-1 board — grabbable
// nodes, hover fade/highlight, and the same node/edge detail popups (the
// L3 pane has its own popup element; app.js's lives inside the hidden
// Dashboard page). Row-driven extras (clock ticking, sub-hint drill) stay
// L1-only; Mode A will lock interaction during its animations.
function l3WireMirrorInteractions(inst) {
  inst.on("mouseover", "node", (evt) => {
    const node = evt.target;
    inst.elements().addClass("faded");
    const nb = node.closedNeighborhood();
    nb.removeClass("faded");
    nb.edges().addClass("highlight");
    l3ShowNodePopup(node);
  });
  inst.on("mouseout", "node", () => {
    inst.elements().removeClass("faded");
    inst.edges().removeClass("highlight");
    l3HidePopup();
  });
  inst.on("mouseover", "edge", (evt) => {
    const edge = evt.target;
    const keep = edge.union(edge.connectedNodes());
    inst.elements().not(keep).addClass("hover-dim");
    edge.addClass("wire-focus");
    l3ShowEdgePopup(edge, inst);
  });
  inst.on("mouseout", "edge", (evt) => {
    inst.elements().removeClass("hover-dim");
    evt.target.removeClass("wire-focus");
    l3HidePopup();
  });
}

function _l3Popup() {
  return {
    el: document.getElementById("l3-hover-popup"),
    title: document.getElementById("l3-hover-popup-title"),
    body: document.getElementById("l3-hover-popup-body"),
  };
}

function l3ShowNodePopup(node) {
  const p = _l3Popup();
  if (!p.el) return;
  const d = node.data();
  p.title.textContent = d.comp_label
    ? `${d.element_name} - ${d.comp_label}` : d.element_name;
  const bits = (d.attributes && d.attributes.Bits !== undefined)
    ? d.attributes.Bits : null;
  const bitsRow = bits !== null
    ? `<tr><td class="k">bits</td><td class="v">${escapeHtml(String(bits))}</td></tr>`
    : "";
  const attrRows = Object.entries(d.attributes || {})
    .filter(([k]) => k !== "Label" && k !== "Bits")
    .map(([k, v]) =>
      `<tr><td class="k">${escapeHtml(k)}</td><td class="v">${escapeHtml(String(v))}</td></tr>`
    ).join("");
  const inputsHtml = renderPinList(
    groupBy(node.incomers("edge"), (e) => e.data("sink_pin") || "?"), "input");
  const outputsHtml = renderPinList(
    groupBy(node.outgoers("edge"), (e) => e.data("driver_pin") || "?"), "output");
  p.body.innerHTML = `
    <table>
      <tr><td class="k">family</td><td class="v">${escapeHtml(d.family_display || d.family)}</td></tr>
      <tr><td class="k">index</td><td class="v">${escapeHtml(d.id)}</td></tr>
      ${bitsRow}
      <tr><td class="k">.dig pos</td><td class="v">(${d.x_dig}, ${d.y_dig})</td></tr>
      ${attrRows}
    </table>
    ${inputsHtml}
    ${outputsHtml}
  `;
  p.el.classList.remove("hidden");
}

function l3ShowEdgePopup(edge, inst) {
  const p = _l3Popup();
  if (!p.el) return;
  const d = edge.data();
  p.title.textContent = `Net ${d.net_id ?? "?"}`;
  const sourceNode = inst.getElementById(d.source);
  const targetNode = inst.getElementById(d.target);
  const sourceLabel = sourceNode.data("comp_label") || sourceNode.data("element_name");
  const targetLabel = targetNode.data("comp_label") || targetNode.data("element_name");
  p.body.innerHTML = `
    <table>
      <tr><td class="k">net id</td><td class="v">${escapeHtml(d.net_id ?? "?")}</td></tr>
      <tr><td class="k">bits</td><td class="v">${escapeHtml(d.bits ?? "?")}</td></tr>
      <tr><td class="k">from</td><td class="v">${escapeHtml(sourceLabel)} [${escapeHtml(d.source)}] . ${escapeHtml(d.driver_pin || "?")}</td></tr>
      <tr><td class="k">to</td><td class="v">${escapeHtml(targetLabel)} [${escapeHtml(d.target)}] . ${escapeHtml(d.sink_pin || "?")}</td></tr>
    </table>
  `;
  p.el.classList.remove("hidden");
}

function l3HidePopup() {
  const p = _l3Popup();
  if (p.el) p.el.classList.add("hidden");
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
  // Mode B's accepted rows fail on the temp circuit, the run targets the
  // registered coach temp.
  const failing = l3FailingRowCount(file.filename);
  const mbA = saved.modeB;
  const coachHint = (mbA && mbA.injectFailing > 0)
    ? ` ALSO: ${mbA.injectFailing} coach row${mbA.injectFailing === 1 ? "" : "s"} ` +
      `disagree with the temp circuit '${mbA.tempName || "coach copy"}' — ` +
      `either the row is wrong (discard it on the lower board) or your ` +
      `circuit has a bug there (Analyze runs on the temp file).`
    : "";
  const ma = saved.modeA;
  if (l3RunState.a) {
    _l3PaintBoard("a", {
      status: "Analyzing… clustering failing rows, hypothesizing, " +
        "verifying every fix on a temp copy.",
      cls: "muted",
      bodyHtml:
        `<div class="l3-note-card"><span class="l3-spinner"></span> ` +
        `Nothing unverified is ever shown. This usually takes a few ` +
        `seconds per cluster.</div>`,
    });
  } else if (ma && ma.result) {
    const st = l3ModeAStatus(ma.result);
    _l3PaintBoard("a", {
      status: st.status + coachHint,
      cls: st.cls,
      // a finished run grays the button — a delivered analysis costs a
      // daily use and re-running the same upload cannot change the answer.
      // Re-uploading (which resets the store) re-enables it.
      enabled: false,
      bodyHtml: l3ModeABodyHtml(ma),
    });
  } else if (!file.summary || !file.summary.has_testcases) {
    _l3PaintBoard("a", {
      status: "This file has no testcases, so there are no failing rows to analyze." + coachHint,
      cls: "muted",
      bodyHtml: l3ModeABodyHtml(ma),
    });
  } else if (failing === null) {
    _l3PaintBoard("a", {
      status:
        `Run tests in per-row mode on the Dashboard first — Mode A picks ` +
        `up the failing rows from there.` + coachHint,
      cls: coachHint ? "blocked" : "muted",
      bodyHtml: l3ModeABodyHtml(ma),
    });
  } else if (failing === 0) {
    _l3PaintBoard("a", {
      status:
        "All rows pass — nothing to debug here. Try the Coverage Coach " +
        "below for gaps your tests might be missing." + coachHint,
      cls: coachHint ? "blocked" : "ready",
      bodyHtml: l3ModeABodyHtml(ma),
    });
  } else {
    _l3PaintBoard("a", {
      status:
        `${failing} failing row${failing === 1 ? "" : "s"} detected — ` +
        `ready to analyze.` + coachHint,
      cls: "ready",
      enabled: true,
      bodyHtml: l3ModeABodyHtml(ma) + _l3StandardsNote(),
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
      // a finished scan grays the button — every click costs a daily
      // use, and rescanning the same upload cannot change the answer.
      // Re-uploading (which resets the store) re-enables it.
      enabled: false,
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

// --- Mode A: analysis render -------------------------------------------------
// The board body for a finished /api/llm/debug run. Cards follow the ladder
// (l3.debug.v1.1 §6): hint_level 1 shows only the hint; "Show me more"
// reveals the verified fix + explanation. Falls back to the legacy stub
// note shape for cards stored by older builds.

function l3ModeAStatus(res) {
  if (res.mode === "clear") {
    return { status: "Every row of this testcase passes — nothing to debug.",
             cls: "ready" };
  }
  if (res.mode === "lazy") {
    return { status: "Analysis says: fundamentals first — see the " +
             "suggestions below. This run was free.", cls: "blocked" };
  }
  if (res.mode === "analysis") {
    const n = (res.cards || []).length;
    if (!n) {
      return { status: "No verified fix survived this run — the ideas it " +
               "tried are below. It did not count against today's uses.",
               cls: "blocked" };
    }
    return { status: `${n} verified hypothesis card${n === 1 ? "" : "s"} — ` +
             `every fix below passed the full re-run before you saw it.`,
             cls: "ready" };
  }
  return { status: res.warning || "Analysis failed.", cls: "blocked" };
}

function _l3OpDesc(op) {
  switch (op.op) {
    case "change_attribute":
      return `set [${op.component_index}] attribute ${op.name} = ${JSON.stringify(op.value)}`;
    case "replace_element":
      return `replace [${op.component_index}] with ${op.new_element}`;
    case "swap_pins":
      return `swap wires on [${op.component_index}] pins ${op.pin_a} ↔ ${op.pin_b}`;
    case "rewire_pin":
      // arrow = signal direction: the pin RECEIVES from its new source
      return `rewire [${op.component_index}].${op.pin} ← ` +
        `[${op.to && op.to.component_index}].${op.to && op.to.pin}`;
    case "add_wire":
      return `add wire (${(op.p1 || []).join(",")}) → (${(op.p2 || []).join(",")})`;
    case "delete_wire":
      return `delete wire (${(op.p1 || []).join(",")}) → (${(op.p2 || []).join(",")})`;
    case "add_component":
      return `add ${op.element_name} at (${(op.position || []).join(",")})`;
    case "delete_component":
      return `delete component [${op.component_index}]`;
    default:
      return JSON.stringify(op);
  }
}

function _l3CardHtml(ma, c) {
  const lvl = (ma.levels && ma.levels[c.rank]) || 1;
  const hint = c.hint || {};
  const fix = c.fix || {};
  const runner = (c.verified && c.verified.runner) || "";
  let html = `<div class="l3-cov-circuit">` +
    `<div class="l3-cov-head">` +
    `<span class="l3-chip">hypothesis #${c.rank}</span>` +
    `<span class="l3-chip l3-chip-good" title="The fix was applied to a temp copy and the whole testcase re-run (${escapeHtml(runner)}) before display — the original file is untouched.">verified fix ✓</span>` +
    `<span class="l3-chip">confidence ${Math.round((c.confidence || 0) * 100)}%</span>` +
    `<span class="l3-chip l3-chip-none">row${(c.cluster_rows || []).length === 1 ? "" : "s"} ${(c.cluster_rows || []).join(", ")}</span>` +
    `</div>`;
  html += `<div class="l3-hint-block"><b>Where to look:</b> ` +
    escapeHtml(hint.suspect_region || "") +
    ((hint.suspect_signals || []).length
      ? `<div class="l3-hint-signals">watch: ` +
        hint.suspect_signals.map((s) => `<span class="l3-prop-row">${escapeHtml(s)}</span>`).join(" ") + `</div>`
      : "") +
    (hint.why ? `<div class="l3-prop-why">${escapeHtml(hint.why)}</div>` : "") +
    `</div>`;
  if (lvl < 2) {
    html += `<div class="l3-prop-bar">` +
      `<button class="btn-ghost" data-l3a-more="${c.rank}">Show me more</button>` +
      `</div>`;
  } else {
    const opLines = (fix.ops_pretty && fix.ops_pretty.length)
      ? fix.ops_pretty
      : (fix.ops || []).map(_l3OpDesc);
    html += `<div class="l3-fix-block">` +
      opLines.map((line) => `<div class="l3-op">${escapeHtml(line)}</div>`).join("") +
      (fix.explanation_for_student
        ? `<div class="l3-prop-why">${escapeHtml(fix.explanation_for_student)}</div>` : "") +
      (ma.result && ma.result.on_coach_temp
        ? `<div class="l3-warn-note">If the circuit still looks right to ` +
          `you after this, remember: some failing rows were coach-added ` +
          `tests — a Mode B row itself could be the mistake (discard it ` +
          `on the lower board and re-run).</div>`
        : "") +
      `</div>`;
  }
  return html + `</div>`;
}

function l3ModeABodyHtml(ma) {
  if (!ma) return "";
  if (!ma.result) {
    return ma.note
      ? `<div class="l3-note-card">${escapeHtml(ma.note)}</div>`
      : "";
  }
  const res = ma.result;
  let html = "";
  const lim = res.limits;
  if (lim && lim.caps && lim.caps.modeA != null) {
    const used = (lim.used && lim.used.modeA) || 0;
    html += `<div class="l3-lim-bar">` +
      `<span class="l3-chip">Debug analysis today: ${used}/${lim.caps.modeA} used</span>` +
      (res.consumed_use === false && res.mode !== "clear"
        ? `<span class="l3-chip l3-chip-warn">this run was free</span>` : "") +
      (lim.enforced
        ? ""
        : `<span class="l3-chip l3-chip-warn" title="DLC_ENFORCE_LIMITS is off — counters tick but nothing blocks. Release flips this on.">dev mode — limit not enforced</span>`) +
      `</div>`;
  }
  if (res.on_coach_temp) {
    html += `<div class="l3-note-card">Analyzed your coach temp copy ` +
      `(original + accepted rows) — the Mode B hand-off.</div>`;
  }
  if (res.mode === "clear") {
    return html + `<div class="l3-note-card">Every row passes on this ` +
      `circuit — if you expected failures, re-run tests on the Dashboard ` +
      `first.</div>`;
  }
  if (res.mode === "lazy") {
    for (const s of res.suggestions || []) {
      html += `<div class="l3-flag">` +
        `<div class="l3-flag-title">${escapeHtml(s.question || "")}</div>` +
        `<div class="l3-flag-body">${escapeHtml(s.hint || "")}` +
        ((s.terms || []).length
          ? `<div class="l3-hint-signals">` +
            s.terms.map((t) => `<span class="l3-chip">${escapeHtml(t)}</span>`).join(" ") + `</div>`
          : "") +
        `</div></div>`;
    }
    return html;
  }
  for (const line of res.diagnosis_lines || []) {
    html += `<div class="l3-diag">${escapeHtml(line)}</div>`;
  }
  for (const c of res.cards || []) html += _l3CardHtml(ma, c);
  const dropped = res.dropped_ideas || [];
  if (dropped.length) {
    html += `<details class="l3-rej-details">` +
      `<summary>${dropped.length} idea${dropped.length === 1 ? "" : "s"} tried and dropped</summary>` +
      dropped.map((d) =>
        `<div class="l3-rej-pair"><span class="l3-rej-where">${escapeHtml(d.reason || "")}</span>` +
        ` — ${escapeHtml(d.why || d.detail || "no detail")}</div>`).join("") +
      `</details>`;
  }
  if ((res.notes || []).length) html += _l3NotesHtml(res.notes);
  // The failing rows themselves, wordless, so the student can stare at
  // them next to the hint. Gone once any fix is revealed — from there the
  // fix (and, next round, its animation) carries the attention.
  const revealed = Object.values(ma.levels || {}).some((v) => v >= 2);
  if (!revealed && !res.on_coach_temp) {
    html += _l3FailingRowsTable(res);
  }
  return html;
}

function _l3FailingRowsTable(res) {
  const file = loaded.length > 0 ? loaded[currentIdx] : null;
  if (!file) return "";
  const st = testState[file.filename];
  if (!st || !st.payload || st.mode !== "per_row") return "";
  const spec = (st.payload.specs || []).find((s) => s.name === res.spec_name)
    || (st.payload.specs || [])[0];
  if (!spec) return "";
  const failing = (spec.rows || []).filter((r) => r.status === "failed");
  if (!failing.length) return "";
  const headers = spec.headers || [];
  let html = `<div class="l3-inj-wrap"><table class="l3-inj-table">` +
    `<tr><td class="l3-idx">row</td>` +
    headers.map((h) => `<td>${escapeHtml(h)}</td>`).join("") + `</tr>`;
  for (const row of failing) {
    const toks = (row.raw || "").split(/\s+/).filter(Boolean);
    html += `<tr class="l3-row-fail"><td class="l3-idx">${row.index}</td>` +
      headers.map((_, i) => `<td>${escapeHtml(toks[i] ?? "")}</td>`).join("") +
      `</tr>`;
    if (Array.isArray(row.mismatches) && row.mismatches.length) {
      const parts = row.mismatches.map((m) =>
        `${escapeHtml(m.column ?? "?")}: expected ${escapeHtml(m.expected)}, got ${escapeHtml(m.found)}`);
      html += `<tr class="l3-mm-row"><td></td>` +
        `<td colspan="${headers.length}">${parts.join(" &middot; ")}</td></tr>`;
    }
  }
  return html + `</table></div>`;
}

// Shown before a run: the tiered bars under which Mode A coaches
// fundamentals (lazy branch) instead of hunting a single bug.
function _l3StandardsNote() {
  return `<div class="l3-note-card l3-standards">Analysis standards — ` +
    `below these bars the coach teaches fundamentals instead of chasing ` +
    `single rows:<br>&gt;10-row testcase: &le;10 failing AND &ge;90% ` +
    `passing &middot; 6&ndash;10 rows: &ge;80% passing &middot; ` +
    `1&ndash;5 rows: &ge;50% passing.</div>`;
}

// "Show me more" — reveal a card's fix level (per-card, sticky in the slot).
(function wireL3BoardA() {
  const body = document.getElementById("l3-a-body");
  if (!body) return;
  body.addEventListener("click", (evt) => {
    const btn = evt.target.closest("[data-l3a-more]");
    if (!btn) return;
    const file = loaded.length > 0 ? loaded[currentIdx] : null;
    if (!file) return;
    const ma = l3Slot(file.filename).modeA;
    if (!ma) return;
    ma.levels = ma.levels || {};
    ma.levels[btn.dataset.l3aMore] = 2;
    logEvent("l3_hint_level", { rank: Number(btn.dataset.l3aMore), level: 2 });
    renderL3Boards(file);
  });
})();

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
  const lim = rep.limits;
  if (lim && lim.caps && lim.caps.modeB != null) {
    const used = (lim.used && lim.used.modeB) || 0;
    html += `<div class="l3-lim-bar">` +
      `<span class="l3-chip">Coverage Coach today: ${used}/${lim.caps.modeB} used</span>` +
      (lim.enforced
        ? ""
        : `<span class="l3-chip l3-chip-warn" title="DLC_ENFORCE_LIMITS is off — counters tick but nothing blocks. Release flips this on.">dev mode — limit not enforced</span>`) +
      `</div>`;
  }
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
  if (c.categories_total) {
    const done = (c.categories_missing || []).length === 0;
    chips.push(done
      ? `<span class="l3-chip l3-chip-good" title="Every manifest category is exercised — raw vector % is informational only.">categories ✓ ${c.categories_total}/${c.categories_total}</span>`
      : `<span class="l3-chip l3-chip-warn">categories ${(c.categories_touched || []).length}/${c.categories_total}</span>`);
  }
  if (c.official_test === "official") {
    chips.push(`<span class="l3-chip" title="This testcase matches the instructor's fingerprint.">official test</span>`);
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
  const vals = `This row expects <b>${escapeHtml(f.column)} = ${escapeHtml(f.asserted_fmt)}</b>, but the circuit as built computes <b>${escapeHtml(f.computed_fmt)}</b>. `;
  const body = f.classification === "official"
    ? vals + `This is the OFFICIAL course testcase (fingerprint verified) — ` +
      `the row is right, so your circuit is wrong at this output. Run ` +
      `per-row tests, then the Failed-test analysis above.`
    : vals + `One of them is wrong — or both: the row's expected value may ` +
      `be a typo (fix the testcase), the circuit may have a bug at this ` +
      `output (run per-row tests, then the Failed-test analysis above), ` +
      `or both drifted together.`;
  return `<div class="l3-flag">
    <div class="l3-flag-title">'${escapeHtml(f.spec_name)}' row ${f.row_index} &middot; ${escapeHtml(f.column)} — test and circuit disagree</div>
    <div class="l3-flag-body">${body}</div>
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
    return `<div class="l3-note-card">Asking the coach for new rows<span
      class="l3-dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span></div>`;
  }
  if (!mb.proposals) {
    return `<div class="l3-prop-bar">
      <button class="btn" data-l3-act="propose">Propose new test rows</button>
    </div>`;
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
    const isProg = !!(g.program_words && g.program_words.length);
    const prog = isProg
      ? `<div class="l3-prop-row l3-prop-prog">+ ROM: ${escapeHtml(g.program_words.join(" "))}</div>`
      : "";
    const progHint = isProg
      ? `<div class="l3-prop-hint">Extends the instruction ROM by ${g.program_words.length}
         word(s); the rows run right AFTER your official rows on the coach's
         temp copy — your own file is never edited.</div>`
      : "";
    // Deterministic decode truth (07-17): say WHICH lab instruction each
    // ROM word is — from the manifest decode, never the model's claim.
    const words = (g.word_info || []).map((w) =>
      `<div class="l3-prop-wordinfo">${escapeHtml(w.word)} = <b>${escapeHtml(w.category)}</b>${
        w.closes_gap ? ` <span class="l3-chip l3-chip-good">closes a missing category</span>` : ""}${
        w.auto_readback ? ` <span class="l3-chip l3-chip-none">auto read-back · observes ${escapeHtml(w.observes || "")}</span>` : ""}</div>`).join("");
    html += `<label class="l3-prop-card">
      <span class="l3-prop-pick"><input type="checkbox" data-l3-group="${gi}" checked /> include</span>
      <div class="l3-prop-body">
        <div class="l3-prop-target">${escapeHtml(g.file)} · '${escapeHtml(g.spec_name)}'</div>
        ${prog}
        ${words}
        ${rows}
        <div class="l3-prop-why">${escapeHtml(g.why)}</div>
        ${progHint}
      </div></label>`;
  });
  if ((p.notes || []).length) {
    html += `<div class="l3-prop-hint">${escapeHtml(p.notes.join(" "))}</div>`;
  }
  if ((p.rejected || []).length) {
    const friendly = {
      duplicate: "already covered by an existing row — proposing it again adds nothing",
      undefined_op: "tests an operation this lab does not define — no test needed",
      wrong_expectation: "its expected outputs were wrong — the coach dropped its own mistake before showing it",
      lazy: "a lazy test — on those operands every instruction gives the same result (or the result is discarded), so it can't catch a wrong operation",
      unobserved: "computes a result nothing ever reads back — the write could silently fail and no row would notice",
      format: "not a legal row for that testcase",
    };
    const items = p.rejected.map((r) => {
      // when the gate paired each dropped row with ITS mismatch,
      // show one line per row instead of a wall of semicolons
      const raw = (r.details && r.details.length)
        ? r.details.map((d) =>
            `<div class="l3-rej-pair"><code>${escapeHtml(d.row)}</code>
             <small>${escapeHtml(d.detail)}</small></div>`).join("")
        : `<div class="l3-rej-raw"><code>${escapeHtml((r.rows || []).join(" | "))}</code>
           <small>${escapeHtml(r.reason || "")}</small></div>`;
      return `<li><span class="l3-rej-where">${escapeHtml(r.file || "?")} · '${escapeHtml(r.spec_name || "?")}'</span>
       — ${escapeHtml(friendly[r.kind] || friendly.format)}${
         (r.details && r.details.length)
           ? ` <small class="muted">(${escapeHtml(r.reason || "")})</small>` : ""}
       ${raw}</li>`;
    }).join("");
    html += `<details class="l3-rej-details"><summary>${p.rejected.length} idea(s)
      the coach dropped</summary><ul>${items}</ul></details>`;
  }
  const doneLine = mb.inject && Object.keys(mb.inject).length
    ? `<span class="l3-prop-hint l3-added-note">new tests added to temp .dig in layer 3</span>`
    : "";
  html += `<div class="l3-prop-bar">
    <button class="btn" data-l3-act="accept"${mb.accepting ? " disabled" : ""}>
      Accept & verify selected
    </button>
    ${mb.accepting ? `<span class="l3-spinner" aria-label="verifying"></span>` : doneLine}</div>`;
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
    const drillable = !clickable && !!out.temp_filename;   // auto-drill
    // Collapsible bulk: legacy _second warm-ups (origin "replay", nothing
    // asserted) and — append mode — the official rows re-running ahead
    // of the new ones (fully asserted; a FAILED official row is a
    // regression and always stays visible).
    const isAppend = !!out._append;
    const isBulk = (r) => r.origin === "replay" ||
      (isAppend && !r.added && r.status === "passed");
    const nWarm = (out.rows || []).filter(isBulk).length;
    const warmLabelHidden = isAppend
      ? `▸ ${nWarm} official rows re-ran ahead of the new ones (every cell
         still checked, all passing) — click to show`
      : `▸ ${nWarm} replay warm-up rows (your original program re-running,
         nothing asserted) — click to show`;
    const warmLabelShown = isAppend
      ? `▾ hide the ${nWarm} official re-run rows`
      : `▾ hide the ${nWarm} replay warm-up rows`;
    const showWarm = !!out._showWarm;
    let warmToggled = false;
    let rows = "";
    for (const r of out.rows || []) {
      const isWarm = isBulk(r);
      if (isWarm && !showWarm) {
        if (!warmToggled) {
          warmToggled = true;
          rows += `<tr class="l3-warm-toggle" data-l3-warmtoggle="${escapeHtml(file)}">
            <td class="l3-idx">…</td><td colspan="${headers.length + 1}">
            ${warmLabelHidden}</td></tr>`;
        }
        continue;
      }
      const cells = (r.raw || "").split(/\s+/).filter(Boolean).slice(0, headers.length);
      const tds = headers.map((_, i) => `<td>${escapeHtml(cells[i] ?? "")}</td>`).join("");
      const cls = [r.status === "failed" ? "l3-row-fail" : "",
                   r.added ? "l3-row-added" : "",
                   isWarm ? "l3-row-warm" : "",
                   (clickable || drillable) ? "l3-row-click" : ""].join(" ").trim();
      const attrs = clickable
        ? ` data-l3-simfile="${escapeHtml(out.temp_filename || "")}"` +
          ` data-l3-spec="${out._spec_index ?? 0}" data-l3-row="${r.index}"`
        : (drillable
          ? ` data-l3-drillfile="${escapeHtml(file)}"` +
            ` data-l3-simfile="${escapeHtml(out.temp_filename || "")}"` +
            ` data-l3-spec="${out._spec_index ?? 0}" data-l3-row="${r.index}"`
          : "");
      rows += `<tr class="${cls}"${attrs}>
        <td class="l3-idx">${r.index}${r.added ? "＋" : ""}</td>${tds}
        <td>${escapeHtml(r.status)}</td></tr>`;
    }
    if (showWarm && nWarm) {
      rows = `<tr class="l3-warm-toggle" data-l3-warmtoggle="${escapeHtml(file)}">
        <td class="l3-idx">…</td><td colspan="${headers.length + 1}">
        ${warmLabelShown}</td></tr>` + rows;
    }
    const baseLine = out.base_spec
      ? (isAppend
        ? `<div class="l3-prop-hint">Official rows re-ran ahead of the new ones:
           ${out.base_spec.passed}/${out.base_spec.total}
           ${out.base_spec.all_passed ? "still passing ✓" : "REGRESSED ✗"} —
           the coach rows execute after them, register state carried over.</div>`
        : `<div class="l3-prop-hint">Official testcase '${escapeHtml(out.base_spec.name)}'
           re-run unchanged: ${out.base_spec.passed}/${out.base_spec.total}
           ${out.base_spec.all_passed ? "still passing ✓" : "REGRESSED ✗"} —
           dimmed rows just replay your original program (nothing asserted).</div>`)
      : "";
    html += `<div class="l3-cov-circuit">
      <div class="l3-cov-head"><span class="l3-cov-file">${escapeHtml(file)}</span>${badge}
        <span class="l3-chip l3-chip-none">${escapeHtml(out.temp_filename || "")}</span>
        ${out.spec_name ? `<span class="l3-chip l3-chip-none">${escapeHtml(out.spec_name)}</span>` : ""}</div>
      ${baseLine}
      <div class="l3-inj-wrap"><table class="l3-inj-table">${head}${rows}</table></div>
      ${clickable
        ? `<div class="l3-prop-hint">Click a row to drive its signal flow on the circuit at the left.</div>`
        : (drillable
          ? `<div class="l3-prop-hint">Click a row to AUTO-DRILL into ${escapeHtml(file)}.</div>`
          : `<div class="l3-prop-hint">Rows for ${escapeHtml(file)} — switch to that file to view their signal flow.</div>`)}
      ${(out.rows || []).some((r) => r.added)
        ? `<div class="l3-prop-bar"><button class="btn-ghost" data-l3-act="copyrows" data-l3-file="${escapeHtml(file)}">Copy the coach rows</button>
           ${out.rom_program ? `<button class="btn-ghost" data-l3-act="copyrom" data-l3-file="${escapeHtml(file)}">Copy the full ROM program</button>` : ""}
           <span class="l3-prop-hint">These rows live on the coach's TEMP copy —
           <b>your ${escapeHtml(file)} is unchanged</b>. To keep them, paste
           them into your testcase in Digital${out.rom_program
             ? " and replace the Instruction Memory Data with the full ROM program"
             : ""}.</span></div>`
        : ""}
      ${out.outcome === "all_set" && (out.rows || []).some((r) => r.added)
        ? (out._adopted
          ? `<div class="l3-prop-bar"><span class="l3-prop-hint l3-added-note">official test updated ✓ — fingerprint ${escapeHtml(out._adopted)}; future scans hold this circuit to the merged standard.</span></div>`
          : `<div class="l3-prop-bar"><button class="btn-ghost" data-l3-act="adopt" data-l3-file="${escapeHtml(file)}">Adopt into official tests</button>
             <span class="l3-prop-hint">Adds the verified rows to this lab's official test <b>on this computer</b> — from then on, scans treat the merged set as the standard to pass.</span></div>`)
        : ""}
      ${out.outcome !== "all_set"
        ? (out._rom_words
          ? `<div class="l3-prop-bar"><button class="btn-ghost" data-l3-act="discardfail" data-l3-file="${escapeHtml(file)}">Discard the program extension</button>
             <span class="l3-prop-hint">A program extension verifies as a unit — later instructions read earlier results — so discarding removes the whole extension.</span></div>`
          : `<div class="l3-prop-bar"><button class="btn-ghost" data-l3-act="discardfail" data-l3-file="${escapeHtml(file)}">Discard failing coach rows &amp; re-verify</button>
             <span class="l3-prop-hint">A failing coach row can itself be wrong — discarding keeps only the rows your circuit and the coach agree on.</span></div>`)
        : ""}
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
    : { proposals: [], notes: body.notes || [], model: body.model,
        error: body.error };
  // an empty run refunds the day's use — refresh the limits bar
  if (body.limits && slot.modeB.report) {
    slot.modeB.report.limits = body.limits;
  }
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
      // program extensions APPEND to the official testcase on the
      // temp copy (state carries over) — as_second stays server-side for
      // genuinely isolated cases only.
      const res = await fetch("/api/l3/inject", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId, filename: g.file,
          spec_name: g.spec_name, rows: g.rows,
          rom_words: g.program_words || [],
        }),
      });
      out = res.ok ? await res.json()
                   : { ok: false, warning: `Server error ${res.status}` };
    } catch (err) {
      out = { ok: false, warning: `Network error: ${err}` };
    }
    if (out.ok) {
      // spec index inside that file (temp preserves testcase order); for a
      // second testcase the server says where it appended it
      const cov = (mb.report.circuits || []).find((c) => c.file === g.file);
      const sp = cov && (cov.specs || []).find((s) => s.name === g.spec_name);
      out._spec_index = (out.spec_index != null)
        ? out.spec_index : (sp ? sp.spec_index : 0);
      out._rom_words = (g.program_words && g.program_words.length)
        ? g.program_words : null;
      out._append = !!out._rom_words;      // append-mode rendering
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

// --- AUTO-DRILL: subcircuit-testcase rows play their own descent -------
// Clicking a coach row that belongs to a SUBCIRCUIT auto-plays the drill-in
// descent (any depth, no rectangle-touching): each parent level flashes the
// instance being entered, then the child renders as its OWN top with the
// row's inner signal flow, and a bar offers the way back + a re-run-Mode-A
// hint. Pure client work: graphs come from `loaded`, values come from
// /api/simulate on the child's registered coach temp.

let l3DrillBusy = false;

const l3Wait = (ms) => new Promise((r) => setTimeout(r, ms));

function l3LoadedByName() {
  const byName = {};
  loaded.forEach((f) => { if (!f.error && f.graph) byName[f.filename] = f; });
  return byName;
}

// BFS across the loaded graphs: the chain of subcircuit instances leading
// from `fromFile` down to `targetFile`. Steps: [{file, nodeId, child}].
// Multiple instances of the same child: the first found is played.
function l3FindDescent(fromFile, targetFile) {
  const byName = l3LoadedByName();
  const queue = [[fromFile, []]];
  const seen = new Set([fromFile]);
  while (queue.length) {
    const [file, path] = queue.shift();
    const entry = byName[file];
    if (!entry) continue;
    for (const n of entry.graph.nodes) {
      const en = n.data && n.data.element_name;
      if (typeof en !== "string" || !en.endsWith(".dig")) continue;
      const step = { file, nodeId: n.data.id, child: en };
      if (en === targetFile) return path.concat(step);
      if (!seen.has(en)) {
        seen.add(en);
        queue.push([en, path.concat(step)]);
      }
    }
  }
  return null;
}

function l3DrillBarEl() {
  let el = document.getElementById("l3-drill-bar");
  if (!el) {
    const box = document.getElementById("l3-cy");
    if (!box || !box.parentElement) return null;
    el = document.createElement("div");
    el.id = "l3-drill-bar";
    el.className = "l3-drill-bar hidden";
    box.parentElement.insertBefore(el, box);
    el.addEventListener("click", (evt) => {
      if (evt.target.closest("[data-l3-drillback]")) {
        l3HideDrillBar();
        const cur = loaded[currentIdx];
        if (cur && !cur.error) l3BuildMirror(cur);
      }
    });
  }
  return el;
}

function l3RenderDrillBar(crumb, rowIdx, targetFile) {
  const el = l3DrillBarEl();
  if (!el) return;
  const top = loaded[currentIdx] ? loaded[currentIdx].filename : "top";
  el.innerHTML =
    `<span class="l3-drill-crumb">` +
    crumb.map(escapeHtml).join('<span class="crumb-sep">&#9656;</span>') +
    ` <span class="crumb-row">row ${rowIdx}</span></span>` +
    `<span class="l3-drill-hint">the coach row playing inside ` +
    `${escapeHtml(targetFile)}</span>` +
    `<button class="btn-ghost" data-l3-drillback>&#9666; back to ${escapeHtml(top)}</button>`;
  el.classList.remove("hidden");
  // the "coaching <file>" chip names the SELECTED file — misleading (and
  // overlapping) while a drilled child owns the mirror, so hide it
  const chip = document.getElementById("l3-file-chip");
  if (chip) chip.classList.add("hidden");
}

function l3HideDrillBar() {
  const el = document.getElementById("l3-drill-bar");
  if (el) el.classList.add("hidden");
  const chip = document.getElementById("l3-file-chip");
  if (chip && chip.textContent) chip.classList.remove("hidden");
}

async function l3AutoDrillRow(tr) {
  const targetFile = tr.dataset.l3Drillfile;
  const tempName = tr.dataset.l3Simfile;
  const specIdx = parseInt(tr.dataset.l3Spec, 10) || 0;
  const rowIdx = parseInt(tr.dataset.l3Row, 10);
  const cur = loaded.length > 0 ? loaded[currentIdx] : null;
  if (!cur || cur.error || !sessionId || l3DrillBusy || Number.isNaN(rowIdx)) return;
  const byName = l3LoadedByName();
  if (!byName[targetFile]) return;          // child never uploaded: keep note
  l3DrillBusy = true;
  try {
    document.querySelectorAll("#l3-b-body tr.l3-row-sel")
      .forEach((t) => t.classList.remove("l3-row-sel"));
    tr.classList.add("l3-row-sel");

    // The descent: flash + zoom each instance on the way down.
    const steps = l3FindDescent(cur.filename, targetFile) || [];
    for (const step of steps) {
      l3BuildMirror(byName[step.file]);
      await l3Wait(350);
      const node = l3Cy && l3Cy.getElementById(String(step.nodeId));
      if (node && node.length) {
        node.style({ "border-width": 6, "border-color": "#f59e0b",
                     "border-opacity": 1 });
        try {
          l3Cy.animate({ fit: { eles: node, padding: 130 }, duration: 480 });
        } catch {}
        await l3Wait(820);
      }
    }

    // Land: the child as its OWN top, painted with its own row's flow.
    l3BuildMirror(byName[targetFile]);
    await l3Wait(300);
    let sim = null;
    try {
      const res = await fetch("/api/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId, filename: tempName,
          spec_index: specIdx, row_index: rowIdx,
        }),
      });
      if (res.ok) sim = await res.json();
    } catch {}
    if (sim && sim.ok !== false && l3Cy) applySignalFlow(sim, l3Cy);

    const crumb = [cur.filename].concat(steps.map((s) => s.child));
    if (crumb[crumb.length - 1] !== targetFile) crumb.push(targetFile);
    l3RenderDrillBar(crumb, rowIdx, targetFile);
    logEvent("l3_modeB_auto_drill", {
      from: cur.filename, to: targetFile, row: rowIdx, depth: steps.length,
    });
  } finally {
    l3DrillBusy = false;
  }
}

// the coach rows never touch the student's file — give them a
// one-click way to carry the rows into Digital themselves.
async function l3CopyRows(file, btn) {
  const cur = loaded.length > 0 ? loaded[currentIdx] : null;
  if (!cur) return;
  const mb = l3Slot(cur.filename).modeB;
  const out = mb && mb.inject && mb.inject[file];
  if (!out || !out.ok) return;
  const lines = [];
  if (out._rom_words && out._rom_words.length) {
    lines.push(`# ROM program words (append to the Instruction Memory Data):`);
    lines.push(out._rom_words.join(","));
    lines.push(`# test rows (append to the END of your '${out.spec_name || ""}' testcase):`);
  }
  for (const r of out.rows || []) {
    if (r.added && r.raw) lines.push(r.raw);
  }
  const text = lines.join("\n");
  try {
    await navigator.clipboard.writeText(text);
    if (btn) { const t = btn.textContent; btn.textContent = "copied ✓";
               setTimeout(() => { btn.textContent = t; }, 1500); }
  } catch {
    window.prompt("Copy the coach rows:", text);
  }
  logEvent("l3_modeB_rows_copied", { file, n: lines.length });
}

// merge the verified coach rows into this lab's LOCAL official test.
// Double-confirm, then the server reads the temp circuit itself — the
// content saved is the machine-verified spec, never client text.
async function l3AdoptOfficial(file) {
  const cur = loaded.length > 0 ? loaded[currentIdx] : null;
  if (!cur || !sessionId) return;
  const mb = l3Slot(cur.filename).modeB;
  const out = mb && mb.inject && mb.inject[file];
  if (!out || !out.ok || out.outcome !== "all_set") return;
  if (!confirm(
    `Adopt the verified coach rows into '${file}'s official test on THIS ` +
    `computer?\n\nFuture scans will hold the circuit to the merged, ` +
    `higher bar. Your .dig file itself is not touched.`)) return;
  let body = null;
  try {
    const res = await fetch("/api/l3/adopt_official", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, filename: file }),
    });
    body = await res.json();
  } catch (err) {
    body = { ok: false, warning: `Network error: ${err}` };
  }
  if (body && body.ok) {
    out._adopted = (body.sha1 || "").slice(0, 10);
    logEvent("l3_modeB_adopted_official", { file, rows: body.rows });
  } else {
    alert(body && body.warning ? body.warning : "Adopt failed.");
  }
  if (l3PageVisible() && loaded[currentIdx]
      && loaded[currentIdx].filename === cur.filename) {
    renderL3Boards(loaded[currentIdx]);
  }
}

async function l3CopyRom(file, btn) {
  const cur = loaded.length > 0 ? loaded[currentIdx] : null;
  if (!cur) return;
  const mb = l3Slot(cur.filename).modeB;
  const out = mb && mb.inject && mb.inject[file];
  if (!out || !out.rom_program) return;
  try {
    await navigator.clipboard.writeText(out.rom_program);
    if (btn) { const t = btn.textContent; btn.textContent = "copied ✓";
               setTimeout(() => { btn.textContent = t; }, 1500); }
  } catch {
    window.prompt("Copy the full ROM program:", out.rom_program);
  }
  logEvent("l3_modeB_rom_copied", { file });
}

function l3ToggleWarm(file) {
  const cur = loaded.length > 0 ? loaded[currentIdx] : null;
  if (!cur) return;
  const mb = l3Slot(cur.filename).modeB;
  const out = mb && mb.inject && mb.inject[file];
  if (!out) return;
  out._showWarm = !out._showWarm;
  renderL3Boards(cur);
}

// Drop the failing coach rows for one file and re-verify the survivors on
// a fresh temp copy; with no survivors the section just clears.
async function l3DiscardFail(file) {
  const cur = loaded.length > 0 ? loaded[currentIdx] : null;
  if (!cur || !sessionId) return;
  const mb = l3Slot(cur.filename).modeB;
  const out = mb && mb.inject && mb.inject[file];
  if (!out || !out.ok || mb.accepting) return;
  // A program extension is atomic (later instructions read earlier
  // results), so discarding drops the WHOLE extension — keep nothing.
  const keep = out._rom_words ? [] : (out.rows || [])
    .filter((r) => r.added && r.status === "passed")
    .map((r) => r.raw);
  logEvent("l3_modeB_discard_failing",
           { file, kept: keep.length, program: !!out._rom_words });
  if (!keep.length) {
    delete mb.inject[file];
    if (!Object.keys(mb.inject).length) mb.inject = null;
    try {
      fetch("/api/l3/uninject", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: sessionId, filename: file }),
      });
    } catch {}
  } else {
    mb.accepting = true;
    l3RunState.b = true;
    renderL3Boards(cur);
    let body;
    try {
      const res = await fetch("/api/l3/inject", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: sessionId, filename: file,
          spec_name: out.spec_name, rows: keep,
        }),
      });
      body = res.ok ? await res.json()
                    : { ok: false, warning: `Server error ${res.status}` };
    } catch (err) {
      body = { ok: false, warning: `Network error: ${err}` };
    }
    if (body.ok) body._spec_index = out._spec_index;
    mb.inject[file] = body;
    mb.accepting = false;
    l3RunState.b = false;
  }
  // recompute the fail count + the all-set lock from what remains
  mb.injectFailing = 0;
  let allSet = mb.inject ? true : false;
  for (const o of Object.values(mb.inject || {})) {
    if (!o.ok || o.outcome !== "all_set") allSet = false;
    mb.injectFailing += (o.rows || [])
      .filter((r) => r.added && r.status === "failed").length;
  }
  if (allSet && mb.injectFailing === 0 && mb.inject) {
    mb.locked = true;
    logEvent("l3_modeB_all_set", { filename: cur.filename });
    l3ShowAdoptPopup(cur.filename);
  }
  if (l3PageVisible() && loaded[currentIdx]
      && loaded[currentIdx].filename === cur.filename) {
    renderL3Boards(loaded[currentIdx]);
  }
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
      if (btn.dataset.l3Act === "discardfail") l3DiscardFail(btn.dataset.l3File);
      if (btn.dataset.l3Act === "copyrows") l3CopyRows(btn.dataset.l3File, btn);
      if (btn.dataset.l3Act === "copyrom") l3CopyRom(btn.dataset.l3File, btn);
      if (btn.dataset.l3Act === "adopt") l3AdoptOfficial(btn.dataset.l3File);
      return;
    }
    const trWarm = evt.target.closest("tr[data-l3-warmtoggle]");
    if (trWarm) { l3ToggleWarm(trWarm.dataset.l3Warmtoggle); return; }
    const trDrill = evt.target.closest("tr[data-l3-drillfile]");
    if (trDrill) { l3AutoDrillRow(trDrill); return; }
    const tr = evt.target.closest("tr[data-l3-simfile]");
    if (tr) l3SimTempRow(tr);
  });
})();

// Mode A run: the real /api/llm/debug coordinator (per-row verdict →
// cluster → one sub-agent per cluster → machine-verified fix cards). The
// result is stored per circuit (sticky; expires on re-upload) under the
// filename the run STARTED on, so a mid-run circuit switch can't misfile it.
l3ARunBtn.addEventListener("click", async () => {
  const file = loaded.length > 0 ? loaded[currentIdx] : null;
  if (!file || file.error || !sessionId || l3RunState.a) return;
  const filename = file.filename;
  logEvent("l3_modeA_started", { filename });

  l3RunState.a = true;
  l3ARunBtn.textContent = "Analyzing…";
  renderL3Boards(file);

  let body = null;
  let failText = null;
  try {
    const res = await fetch("/api/llm/debug", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, filename }),
    });
    if (!res.ok) failText = `Server error ${res.status}: ${await res.text()}`;
    else body = await res.json();
  } catch (err) {
    failText = `Network error: ${err}`;
  }
  l3RunState.a = false;
  l3ARunBtn.textContent = "Analyze failing rows";

  if (body && body.ok) {
    l3Slot(filename).modeA = { result: body, levels: {} };
    logEvent("l3_modeA_run_complete", {
      filename, mode: body.mode,
      cards: (body.cards || []).length, llm_calls: body.llm_calls || 0,
    });
  } else {
    const warn = failText || (body && (body.warning || body.error))
      || "Analysis failed.";
    l3Slot(filename).modeA = null;
    logEvent("l3_modeA_run_complete", { filename, ok: false });
    const status = document.getElementById("l3-a-status");
    if (status && l3PageVisible()
        && loaded[currentIdx] && loaded[currentIdx].filename === filename) {
      renderL3Boards(loaded[currentIdx]);
      status.textContent = `Analysis failed: ${warn}`;
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
/* --- Net-id overlay + adopt popup ---------------------------------------
   The net-id toggle labels every wire with the net id it belongs to (the
   same ids the hint evidence and hover popups speak), on both the L1 and
   the L3 graph. The adopt popup is the proactive Accept follow-up: every
   accepted row passes, so offer to save the merged testcase as the lab's
   official test right away. */

let l3NetIdsOn = false;

function l3ApplyNetIds() {
  if (!l3Cy) return;
  l3Cy.edges()[l3NetIdsOn ? "addClass" : "removeClass"]("show-netid");
  const btn = document.getElementById("l3-netid-toggle");
  if (btn) btn.classList.toggle("active", l3NetIdsOn);
}

(function wireL3NetIdToggle() {
  const btn = document.getElementById("l3-netid-toggle");
  if (!btn) return;
  btn.addEventListener("click", () => {
    l3NetIdsOn = !l3NetIdsOn;
    l3ApplyNetIds();
    logEvent("l3_netids_toggled", { on: l3NetIdsOn });
  });
})();

let _l3AdoptPending = null;    // selected filename at all-set time

function l3ShowAdoptPopup(filename) {
  const modal = document.getElementById("adopt-modal");
  if (!modal) return;
  _l3AdoptPending = filename;
  const msg = document.getElementById("adopt-modal-msg");
  if (msg) { msg.textContent = ""; msg.className = "modal-msg"; }
  modal.classList.remove("hidden");
  logEvent("l3_adopt_popup_shown", { filename });
}

(function wireAdoptPopup() {
  const modal = document.getElementById("adopt-modal");
  if (!modal) return;
  const yes = document.getElementById("adopt-yes-btn");
  const no = document.getElementById("adopt-no-btn");
  const msg = document.getElementById("adopt-modal-msg");
  if (no) no.addEventListener("click", () => {
    modal.classList.add("hidden");
    logEvent("l3_adopt_popup_declined", {});
  });
  if (yes) yes.addEventListener("click", async () => {
    const cur = loaded.length > 0 ? loaded[currentIdx] : null;
    if (!cur || !sessionId || cur.filename !== _l3AdoptPending) {
      modal.classList.add("hidden");
      return;
    }
    const mb = l3Slot(cur.filename).modeB;
    const files = Object.entries((mb && mb.inject) || {})
      .filter(([, o]) => o && o.ok && o.outcome === "all_set")
      .map(([f]) => f);
    yes.disabled = true;
    let okAll = true;
    for (const f of files) {
      try {
        const res = await fetch("/api/l3/adopt_official", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ session_id: sessionId, filename: f }),
        });
        const body = await res.json();
        if (body.ok) {
          if (mb.inject[f]) mb.inject[f]._adopted = body;
        } else {
          okAll = false;
          if (msg) {
            msg.textContent = `${f}: ${body.warning || "could not save"}`;
            msg.className = "modal-msg err";
          }
        }
      } catch (err) {
        okAll = false;
        if (msg) { msg.textContent = `Network error: ${err}`; msg.className = "modal-msg err"; }
      }
    }
    yes.disabled = false;
    logEvent("l3_adopt_popup_accepted", { files: files.length, ok: okAll });
    if (okAll) {
      modal.classList.add("hidden");
      renderL3Boards(cur);
    }
  });
})();
