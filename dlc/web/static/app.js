/* Digital Lab Coach front end. Loads Cytoscape, handles multi-file
   uploads, renders signal-flow graph, drives summary + hover popup.
*/

const GRAPH_LIBS_OK =
  typeof cytoscape !== "undefined" && typeof window.cytoscapeDagre !== "undefined";
if (GRAPH_LIBS_OK) {
  cytoscape.use(window.cytoscapeDagre);
} else {
  console.error(
    "DLC: graph libraries (cytoscape/dagre) failed to load from the CDN. " +
    "The signal-flow graph is disabled, but the rest of the UI still works.",
  );
}

const FAMILY_COLORS = {
  "io-in":      "#cfe5ff",
  "io-out":     "#ffdcb3",
  "gate":       "#b9e4c1",
  "arith":      "#f4b9b9",
  "mux":        "#d8c4ef",
  "splitter":   "#f1ea9a",
  "storage":    "#d3d3d3",
  "tunnel":     "#f7d7e8",
  "subcircuit": "#ffc1c1",
  "const":      "#dfdfdf",
  "clock":      "#dfdfdf",
  "other":      "#e9ecef",
};

const CY_STYLE = [
  {
    selector: "node",
    style: {
      "shape": "round-rectangle",
      "background-color": (n) => FAMILY_COLORS[n.data("family")] || "#e9ecef",
      "border-color": "#444",
      "border-width": 1,
      "label": "data(label)",
      "color": "#1f2933",
      "font-size": 10,
      "text-wrap": "wrap",
      "text-max-width": 90,
      "text-valign": "center",
      "text-halign": "center",
      "width": 70,
      "height": 38,
      "padding": "4px",
    },
  },
  { selector: "node.faded", style: { "opacity": 0.22 } },
  {
    selector: "edge",
    style: {
      "width": 1.5,
      "line-color": "#9aa1ab",
      "target-arrow-color": "#9aa1ab",
      "target-arrow-shape": "triangle",
      "curve-style": "bezier",
      "arrow-scale": 0.9,
    },
  },
  { selector: "edge.faded", style: { "opacity": 0.1 } },
  {
    selector: "edge.highlight",
    style: {
      "line-color": "#2563eb",
      "target-arrow-color": "#2563eb",
      "width": 2.5,
    },
  },
  {
    selector: "node.issue-target",
    style: {
      "border-color": "#dc2626",
      "border-width": 4,
      "background-color": "#fee2e2",
    },
  },
];

//DOM 

const MAX_FILES = 16;

const fileInput     = document.getElementById("file-input");
const fileSelect    = document.getElementById("file-select");
const prevBtn       = document.getElementById("prev-btn");
const nextBtn       = document.getElementById("next-btn");
const clearBtn      = document.getElementById("clear-btn");
const placeholder   = document.getElementById("placeholder");
const summaryEl     = document.getElementById("summary");
const issuesListEl  = document.getElementById("issues-list");
const issuesCountsEl= document.getElementById("issues-counts");
const testsStatusEl = document.getElementById("tests-status");
const testsResultsEl= document.getElementById("tests-results");
const testsProgressEl     = document.getElementById("tests-progress");
const testsProgressTextEl = document.getElementById("tests-progress-text");
const perRowToggle  = document.getElementById("perrow-toggle");
const runTestsBtn   = document.getElementById("run-tests-btn");
const testAllBtn    = document.getElementById("test-all-btn");
const testAllPanel  = document.getElementById("test-all-panel");
const testAllHeadEl = document.getElementById("test-all-headline");
const testAllListEl = document.getElementById("test-all-list");
const testAllClose  = document.getElementById("test-all-close");
const muteToggle    = document.getElementById("mute-toggle");
const popupEl       = document.getElementById("hover-popup");
const popupTitle    = document.getElementById("hover-popup-title");
const popupBody     = document.getElementById("hover-popup-body");
const jarChipBtn    = document.getElementById("jar-chip");
const jarStateEl    = document.getElementById("jar-state");
const jarModal      = document.getElementById("jar-modal");
const jarPathInput  = document.getElementById("jar-path-input");
const jarBrowseBtn  = document.getElementById("jar-browse-btn");
const jarSaveBtn    = document.getElementById("jar-save-btn");
const jarCancelBtn  = document.getElementById("jar-cancel-btn");
const jarModalMsg   = document.getElementById("jar-modal-msg");
const llmStubBtn    = document.getElementById("llm-stub-btn");
const keyChipBtn    = document.getElementById("key-chip");
const keyStateEl    = document.getElementById("key-state");
const keyModal      = document.getElementById("key-modal");
const keyCancelBtn  = document.getElementById("key-cancel-btn");


const KEY_PROVIDERS = ["anthropic", "openai"];
const keyEls = Object.fromEntries(KEY_PROVIDERS.map((p) => [p, {
  status: document.getElementById(`key-status-${p}`),
  input:  document.getElementById(`key-input-${p}`),
  msg:    document.getElementById(`key-msg-${p}`),
}]));

const l2ModelSelect = document.getElementById("l2-model-select");
const libraryGridEl = document.getElementById("library-grid");
const cardOverlay   = document.getElementById("card-overlay");
const cardDetail    = document.getElementById("card-detail");
const goalTextarea  = document.getElementById("goal-textarea");
const goalCountEl   = document.getElementById("goal-count");
const l2LlmBtn      = document.getElementById("l2-llm-btn");
const l2LlmStatus   = document.getElementById("l2-llm-status");
const l2LlmOutput   = document.getElementById("l2-llm-output");
const graderSelect  = document.getElementById("grader-model-select");
const gradeBody     = document.getElementById("grade-body");
let lastGradedSummary = null;
// Below this total, the grade panel suggests (but never auto-runs) a
// fresh Summarize attempt. The old silent auto-retry was removed: it
// re-spent tokens without consent and could replace a summary with a
// worse one.
const GRADE_HINT_THRESHOLD = 90;

let sessionId = null;

const eventLog = [];
function logEvent(kind, details = {}) {
  eventLog.push({ ts: Date.now(), kind, ...details });
}
window.dlcEventLog = eventLog;

const MUTE_THRESHOLD = 3;
let mutedByUser = new Set();   
let activeIssueIdx = null;     
const testState = {};  

//Session state 
let fileObjects = [];  
let loaded      = [];  
let currentIdx  = 0;
let cy          = null;


fileInput.addEventListener("change", async () => {
  if (!fileInput.files || fileInput.files.length === 0) return;

  const incomingNames = new Set();
  for (const f of fileInput.files) incomingNames.add(f.name);
  const keptExisting = fileObjects.filter((f) => !incomingNames.has(f.name));
  const projectedTotal = keptExisting.length + fileInput.files.length;
  if (projectedTotal > MAX_FILES) {
    alert(
      `File limit is ${MAX_FILES}. This upload would bring you to ` +
      `${projectedTotal}. Use "Clear all" to reset, or upload fewer ` +
      `files at once.`
    );
    fileInput.value = "";
    return;
  }

  for (const f of fileInput.files) {
    fileObjects = fileObjects.filter((existing) => existing.name !== f.name);
    fileObjects.push(f);
  }
  fileInput.value = "";
  await postAll();
});

clearBtn.addEventListener("click", () => {
  if (loaded.length === 0) return;
  if (!confirm("Clear all uploaded files and return to the dashboard?")) return;
  resetDashboard();
});

function resetDashboard() {
  fileObjects = [];
  loaded = [];
  currentIdx = 0;
  mutedByUser = new Set();
  activeIssueIdx = null;
  sessionId = null;
  for (const k of Object.keys(testState)) delete testState[k];
  if (cy) { cy.destroy(); cy = null; }
  fileSelect.innerHTML = "<option>(no file)</option>";
  fileSelect.disabled = true;
  prevBtn.disabled = true;
  nextBtn.disabled = true;
  clearBtn.disabled = true;
  runTestsBtn.disabled = true;
  testAllBtn.disabled = true;
  testAllBtn.textContent = "Test all";
  testAllPanel.classList.add("hidden");
  placeholder.classList.remove("hidden");
  placeholder.innerHTML =
    `No circuit loaded. Add a <code>.dig</code> file from the toolbar ` +
    `above &mdash; multiple files (parent + subcircuits) supported.`;
  summaryEl.innerHTML = `<span class="muted">No file loaded.</span>`;
  issuesListEl.innerHTML = `<span class="muted">No file loaded.</span>`;
  issuesCountsEl.innerHTML = `<span class="muted">&mdash;</span>`;
  testsStatusEl.textContent = "No file loaded.";
  testsStatusEl.className = "tests-status muted";
  testsResultsEl.innerHTML = "";
  testsResultsEl.classList.add("empty");
  libraryGridEl.innerHTML = `<div class="muted">Load a circuit on the Dashboard tab to populate the library.</div>`;
  l2LibraryFilename = null;
  goalTextarea.value = "";
  goalCountEl.textContent = "0 / 500 characters";
  l2LlmStatus.textContent = "";
  l2LlmOutput.innerHTML = "";
  l2LlmOutput.classList.add("empty");
  _resetGrade();
  hidePopup();
}

muteToggle.addEventListener("change", () => {
  if (loaded.length > 0) renderIssues(loaded[currentIdx]);
});

prevBtn.addEventListener("click", () => {
  if (loaded.length === 0) return;
  currentIdx = (currentIdx - 1 + loaded.length) % loaded.length;
  renderCurrent();
});

nextBtn.addEventListener("click", () => {
  if (loaded.length === 0) return;
  currentIdx = (currentIdx + 1) % loaded.length;
  renderCurrent();
});

fileSelect.addEventListener("change", () => {
  currentIdx = parseInt(fileSelect.value, 10) || 0;
  renderCurrent();
});

async function postAll() {
  summaryEl.innerHTML = `<span class="muted">Uploading ${fileObjects.length} file(s)...</span>`;

  const fd = new FormData();
  for (const f of fileObjects) fd.append("files", f);

  let res;
  try {
    res = await fetch("/api/circuit", { method: "POST", body: fd });
  } catch (err) {
    summaryEl.innerHTML =
      `<span style="color:#991b1b">Upload failed: ${escapeHtml(String(err))}.</span> ` +
      `<span class="muted">If this keeps happening (many/large files, or tests still ` +
      `running), click "Clear all" and re-upload.</span>`;
    return;
  }
  if (!res.ok) {
    const text = await res.text();
    summaryEl.innerHTML =
      `<span style="color:#991b1b">${escapeHtml(text)}</span> ` +
      `<span class="muted">If this keeps happening, click "Clear all" and re-upload.</span>`;
    return;
  }
  const data = await res.json();
  loaded = data.files || [];
  sessionId = data.session_id || null;
  logEvent("upload", { session_id: sessionId, count: loaded.length });
  if (loaded.length === 0) {
    summaryEl.innerHTML = `<span style="color:#991b1b">No .dig files were processed.</span>`;
    return;
  }
  if (currentIdx >= loaded.length) currentIdx = 0;

  fileSelect.innerHTML = loaded
    .map((f, i) => `<option value="${i}">${escapeHtml(f.filename)}</option>`)
    .join("");
  fileSelect.value = String(currentIdx);
  fileSelect.disabled = false;
  prevBtn.disabled = loaded.length < 2;
  nextBtn.disabled = loaded.length < 2;
  clearBtn.disabled = false;
  testAllBtn.disabled = false;

  renderCurrent();
}

function renderCurrent() {
  if (loaded.length === 0) return;
  const f = loaded[currentIdx];
  fileSelect.value = String(currentIdx);
  activeIssueIdx = null;

  if (f.error) {
    placeholder.classList.remove("hidden");
    placeholder.innerHTML = `<span style="color:#991b1b">${escapeHtml(f.filename)}: ${escapeHtml(f.error)}</span>`;
    if (cy) { cy.destroy(); cy = null; }
    summaryEl.innerHTML = `<span style="color:#991b1b">Parse error.</span>`;
    issuesListEl.innerHTML = `<span style="color:#991b1b">Could not parse; no issues to show.</span>`;
    issuesCountsEl.innerHTML = `<span class="muted">&mdash;</span>`;
    return;
  }

  renderGraph(f.graph);
  renderSummary(f.summary, f.issues || []);
  renderIssues(f);
  renderTestsForFile(f);
  l2LibraryFilename = null;
  l2LlmStatus.textContent = "";
  l2LlmStatus.className = "l2-llm-status";
  l2LlmOutput.innerHTML = "";
  l2LlmOutput.classList.add("empty");
  _resetGrade();
}

function renderGraph(graph) {
  placeholder.classList.add("hidden");

  if (!GRAPH_LIBS_OK) {
    const box = document.getElementById("cy");
    if (box) {
      box.innerHTML =
        `<div class="muted" style="padding:24px">Graph unavailable: the ` +
        `cytoscape/dagre libraries did not load (network or CDN blocked). ` +
        `Structural issues, tests, library, and the Layer 2 coach still work.</div>`;
    }
    return;
  }

  if (cy) cy.destroy();

  cy = cytoscape({
    container: document.getElementById("cy"),
    elements: { nodes: graph.nodes, edges: graph.edges },
    style: CY_STYLE,
    layout: {
      name: "dagre",
      rankDir: "LR",
      nodeSep: 30,
      rankSep: 60,
      edgeSep: 10,
      animate: false,
    },
    wheelSensitivity: 0.2,
    minZoom: 0.15,
    maxZoom: 3,
  });

  // If the container was still settling when dagre ran (e.g. graph
  // built while another tab was active), re-measure and re-fit once
  // so the tree always starts centered.
  const inst = cy;
  inst.once("layoutstop", () => {
    setTimeout(() => { try { inst.resize(); inst.fit(undefined, 40); } catch {} }, 0);
  });

  cy.on("mouseover", "node", (evt) => {
    const node = evt.target;
    cy.elements().addClass("faded");
    const nb = node.closedNeighborhood();
    nb.removeClass("faded");
    nb.edges().addClass("highlight");
    showNodePopup(node);
  });
  cy.on("mouseout", "node", () => {
    cy.elements().removeClass("faded");
    cy.edges().removeClass("highlight");
    hidePopup();
  });

  cy.on("mouseover", "edge", (evt) => {
    const edge = evt.target;
    edge.addClass("highlight");
    showEdgePopup(edge);
  });
  cy.on("mouseout", "edge", (evt) => {
    evt.target.removeClass("highlight");
    hidePopup();
  });
  cy.on("tap", (evt) => {
    if (evt.target === cy) clearIssueHighlight();
  });
}

function showNodePopup(node) {
  const d = node.data();
  const title = d.comp_label ? `${d.element_name} - ${d.comp_label}` : d.element_name;
  popupTitle.textContent = title;

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

  const incoming = node.incomers("edge");
  const outgoing = node.outgoers("edge");

  const inputsBySinkPin = groupBy(incoming, (e) => e.data("sink_pin") || "?");
  const outputsByDriverPin = groupBy(outgoing, (e) => e.data("driver_pin") || "?");

  const inputsHtml = renderPinList(inputsBySinkPin, "input");
  const outputsHtml = renderPinList(outputsByDriverPin, "output");

  popupBody.innerHTML = `
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

  popupEl.classList.remove("hidden");
}

function showEdgePopup(edge) {
  const d = edge.data();
  popupTitle.textContent = `Net ${d.net_id ?? "?"}`;

  const sourceNode = cy.getElementById(d.source);
  const targetNode = cy.getElementById(d.target);
  const sourceLabel = sourceNode.data("comp_label") || sourceNode.data("element_name");
  const targetLabel = targetNode.data("comp_label") || targetNode.data("element_name");

  popupBody.innerHTML = `
    <table>
      <tr><td class="k">net id</td><td class="v">${escapeHtml(d.net_id ?? "?")}</td></tr>
      <tr><td class="k">bits</td><td class="v">${escapeHtml(d.bits ?? "?")}</td></tr>
      <tr><td class="k">from</td><td class="v">${escapeHtml(sourceLabel)} [${escapeHtml(d.source)}] . ${escapeHtml(d.driver_pin || "?")}</td></tr>
      <tr><td class="k">to</td><td class="v">${escapeHtml(targetLabel)} [${escapeHtml(d.target)}] . ${escapeHtml(d.sink_pin || "?")}</td></tr>
    </table>
  `;
  popupEl.classList.remove("hidden");
}

function hidePopup() {
  popupEl.classList.add("hidden");
}

window.addEventListener("keydown", (e) => {
  if (popupEl.classList.contains("hidden")) return;
  if (e.key === "ArrowDown") {
    popupEl.scrollTop += 40;
    e.preventDefault();
  } else if (e.key === "ArrowUp") {
    popupEl.scrollTop -= 40;
    e.preventDefault();
  } else if (e.key === "PageDown") {
    popupEl.scrollTop += popupEl.clientHeight - 20;
    e.preventDefault();
  } else if (e.key === "PageUp") {
    popupEl.scrollTop -= popupEl.clientHeight - 20;
    e.preventDefault();
  }
});

function renderPinList(byPin, kind) {
  const keys = Object.keys(byPin).sort();
  if (keys.length === 0) return "";

  const sectionTitle = kind === "input" ? "INPUTS" : "OUTPUTS";

  const items = keys.map((pinName) => {
    const edges = byPin[pinName];
    const peers = edges.map((e) => {
      const d = e.data();
      const otherId = kind === "input" ? d.source : d.target;
      const otherPin = kind === "input" ? d.driver_pin : d.sink_pin;
      const otherNode = cy.getElementById(otherId);
      const otherLabel = otherNode.data("comp_label") || otherNode.data("element_name");
      const arrow = kind === "input" ? "&larr;" : "&rarr;";
      return `${arrow} ${escapeHtml(otherLabel)}[${escapeHtml(otherId)}].${escapeHtml(otherPin || "?")} (net ${escapeHtml(d.net_id ?? "?")})`;
    }).join("<br>");

    return `<li><strong>${escapeHtml(pinName)}</strong> ${peers}</li>`;
  }).join("");

  return `
    <div class="hover-popup-section">
      <div class="hover-popup-section-title">${sectionTitle}</div>
      <ul>${items}</ul>
    </div>
  `;
}

function groupBy(collection, keyFn) {
  const out = {};
  collection.forEach((item) => {
    const k = keyFn(item);
    (out[k] = out[k] || []).push(item);
  });
  return out;
}

function renderSummary(s, issues) {
  const stats = s.net_stats || {};
  const undrivenBadge = stats.undriven_with_pins
    ? `<span class="badge warn">${stats.undriven_with_pins} undriven</span>`
    : "";
  const multiBadge = stats.multi_driver
    ? `<span class="badge err">${stats.multi_driver} multi-driver</span>`
    : "";
  const widthKinds = new Set(["width_mismatch", "width_conflict"]);
  const widthCount = (issues || []).filter((i) => widthKinds.has(i.kind)).length;
  const widthBadge = widthCount
    ? `<span class="badge widx">${widthCount} width mismatch${widthCount === 1 ? "" : "es"}</span>`
    : "";
  const hasAny = undrivenBadge || multiBadge || widthBadge;

  const inventoryRows = Object.entries(s.inventory || {})
    .sort(([, a], [, b]) => b - a)
    .map(([name, count]) =>
      `<tr><td class="k">${escapeHtml(name)}</td><td class="v">${count}</td></tr>`
    ).join("");

  const inputsList = (s.inputs || [])
    .map((p) => `<li>${escapeHtml(p.label)} <span class="muted">${p.bits} bit${p.bits === 1 ? "" : "s"}</span></li>`)
    .join("");
  const outputsList = (s.outputs || [])
    .map((p) => `<li>${escapeHtml(p.label)} <span class="muted">${p.bits} bit${p.bits === 1 ? "" : "s"}</span></li>`)
    .join("");
  const subsList = (s.subcircuits || [])
    .map((sub) => {
      const badge = sub.resolved ? "" : `<span class="badge err">missing</span>`;
      return `<li>${escapeHtml(sub.reference)} ${badge}</li>`;
    })
    .join("");

  summaryEl.innerHTML = `
    <table>
      <tr><td class="k">nets</td><td class="v">${stats.total ?? 0}</td></tr>
      <tr><td class="k">driven</td><td class="v">${stats.driven ?? 0}</td></tr>
          <tr><td class="k">structural issues</td><td class="v">${undrivenBadge}${multiBadge}${widthBadge}${hasAny ? "" : '<span class="ok">none</span>'}</td></tr>
    </table>

    <h2 style="margin-top:14px">Inputs (${(s.inputs || []).length})</h2>
    ${inputsList ? `<ul>${inputsList}</ul>` : `<div class="muted">(none)</div>`}

    <h2>Outputs (${(s.outputs || []).length})</h2>
    ${outputsList ? `<ul>${outputsList}</ul>` : `<div class="muted">(none)</div>`}

    <h2>Subcircuits (${(s.subcircuits || []).length})</h2>
    ${subsList ? `<ul>${subsList}</ul>` : `<div class="muted">(none)</div>`}

    <h2>Inventory</h2>
    <table>${inventoryRows || '<tr><td class="muted">(empty)</td></tr>'}</table>
  `;
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function fileL1Errors(file) {
  // Blocking is on ERRORS only (any nesting depth): warnings stay
  // testable so the "mute when tests pass" flow keeps working.
  return ((file && file.issues) || []).filter((i) => i.severity === "error");
}

function getFileTestsPassed(filename) {
  const st = testState[filename];
  if (!st || st.status !== "done" || !st.payload) return null;
  if (st.payload.all_passed === true)  return true;
  if (st.payload.all_passed === false) return false;
  return null;
}

function countsBadge(issues) {
  if (!issues || issues.length === 0) {
    return `<span class="ok">clean</span>`;
  }
  const nErr  = issues.filter((i) => i.severity === "error").length;
  const nWarn = issues.filter((i) => i.severity === "warning").length;
  const nInfo = issues.filter((i) => i.severity === "info").length;
  const parts = [];
  if (nErr)  parts.push(`<span class="err">${nErr} err</span>`);
  if (nWarn) parts.push(`<span class="warn">${nWarn} warn</span>`);
  if (nInfo) parts.push(`<span class="info">${nInfo} info</span>`);
  return parts.join(" &middot; ");
}

function renderIssues(file) {
  const issues = file.issues || [];
  issuesCountsEl.innerHTML = countsBadge(issues);

  if (file.issues_error) {
    issuesListEl.innerHTML =
      `<span style="color:#991b1b">L1 check failed: ${escapeHtml(file.issues_error)}</span>`;
    return;
  }

  if (issues.length === 0) {
    issuesListEl.innerHTML =
      `<div class="muted-banner" style="cursor:default">No Layer 1 issues detected.</div>`;
    return;
  }
  const fileTestsPassed = getFileTestsPassed(file.filename);
  const shouldMute =
    muteToggle.checked
    && fileTestsPassed === true
    && issues.length <= MUTE_THRESHOLD
    && !mutedByUser.has(currentIdx);
  if (shouldMute) {
    issuesListEl.innerHTML =
      `<div class="muted-banner" id="mute-banner">
         Tests pass; ${issues.length} minor note${issues.length === 1 ? "" : "s"} muted. Click to show.
       </div>`;
    document.getElementById("mute-banner").addEventListener("click", () => {
      mutedByUser.add(currentIdx);
      renderIssues(file);
    });
    return;
  }

  issuesListEl.innerHTML = issues.map((iss, idx) => {
    const fixHtml = iss.suggested_fix
      ? `<div class="issue-fix">${escapeHtml(iss.suggested_fix)}</div>`
      : "";
    return `
      <div class="issue-card sev-${escapeHtml(iss.severity)}" data-issue-idx="${idx}">
        <span class="sev-badge">${escapeHtml(iss.severity)}</span>
        <span class="issue-title">${escapeHtml(iss.title)}</span>
        <div class="issue-msg">${escapeHtml(iss.message)}</div>
        ${fixHtml}
      </div>
    `;
  }).join("");

  issuesListEl.querySelectorAll(".issue-card").forEach((card) => {
    card.addEventListener("click", () => {
      const idx = parseInt(card.dataset.issueIdx, 10);
      const iss = issues[idx];
      highlightIssueComponents(iss, card);
    });
  });
}

function highlightIssueComponents(issue, card) {
  if (!cy) return;
  const comps = (issue.component_indices || []).map(String);
  if (comps.length === 0) return;

  if (activeIssueIdx === card) {
    clearIssueHighlight();
    return;
  }

  clearIssueHighlight();
  activeIssueIdx = card;
  card.classList.add("active");

  cy.elements().addClass("faded");
  comps.forEach((id) => {
    const n = cy.getElementById(id);
    if (n && n.nonempty && n.nonempty()) {
      n.removeClass("faded");
      n.addClass("issue-target");
      n.closedNeighborhood().removeClass("faded");
    }
  });

  const targets = cy.collection(
    comps.map((id) => cy.getElementById(id)).filter((n) => n && n.nonempty && n.nonempty())
  );
  if (targets.length > 0) {
    cy.animate({ fit: { eles: targets, padding: 80 } }, { duration: 250 });
  }
}

function clearIssueHighlight() {
  if (!cy) return;
  cy.elements().removeClass("faded");
  cy.nodes().removeClass("issue-target");
  if (activeIssueIdx && activeIssueIdx.classList) {
    activeIssueIdx.classList.remove("active");
  }
  activeIssueIdx = null;
}

// Tests panel 

function setTestSlot(filename, patch) {
  const prev = testState[filename] || { status: "idle", progress: "", payload: null, mode: null, jobId: null, message: null };
  testState[filename] = { ...prev, ...patch };
  if (loaded[currentIdx] && loaded[currentIdx].filename === filename) {
    renderTestsForFile(loaded[currentIdx]);
    if (testState[filename].status === "done") {
      renderIssues(loaded[currentIdx]);
    }
  }
}

function renderTestsForFile(file) {
  testsResultsEl.innerHTML = "";
  testsResultsEl.classList.add("empty");

  const hasTests = !!(file.summary && file.summary.has_testcases);
  if (!hasTests) {
    runTestsBtn.disabled = true;
    runTestsBtn.classList.remove("running");
    runTestsBtn.textContent = "Run tests";
    testsStatusEl.textContent = "No test data found in this file.";
    testsStatusEl.className = "tests-status muted";
    hideProgress();
    return;
  }

  const l1Errors = fileL1Errors(file);
  if (l1Errors.length > 0) {
    runTestsBtn.disabled = true;
    runTestsBtn.classList.remove("running");
    runTestsBtn.textContent = "Run tests";
    testsStatusEl.textContent =
      `Blocked: ${l1Errors.length} Layer 1 error${l1Errors.length === 1 ? "" : "s"} unresolved. ` +
      `Fix the structural errors above first - they make test results unreliable.`;
    testsStatusEl.className = "tests-status warning";
    hideProgress();
    return;
  }

  const slot = testState[file.filename];

  if (!slot || slot.status === "idle") {
    runTestsBtn.disabled = false;
    runTestsBtn.classList.remove("running");
    runTestsBtn.textContent = "Run tests";
    testsStatusEl.textContent =
      `Ready: ${file.summary.testcase_count} testcase${file.summary.testcase_count === 1 ? "" : "s"} found. Click "Run tests" to execute.`;
    testsStatusEl.className = "tests-status muted";
    hideProgress();
    return;
  }

  if (slot.status === "running") {
    runTestsBtn.disabled = true;
    runTestsBtn.classList.add("running");
    runTestsBtn.textContent = "Running...";
    testsStatusEl.textContent =
      slot.mode === "per_row" ? "Running per-row..." : "Running general...";
    testsStatusEl.className = "tests-status muted";
    showProgress(slot.progress || "starting...");
    return;
  }

  if (slot.status === "warning") {
    runTestsBtn.disabled = false;
    runTestsBtn.classList.remove("running");
    runTestsBtn.textContent = "Run tests";
    testsStatusEl.textContent = `Warning: ${slot.message || "Test runner error"}`;
    testsStatusEl.className = "tests-status warning";
    hideProgress();
    return;
  }

  runTestsBtn.disabled = false;
  runTestsBtn.classList.remove("running");
  runTestsBtn.textContent = "Run tests";
  hideProgress();

  const payload = slot.payload;
  if (!payload) return;

  if (payload.warning) {
    testsStatusEl.textContent = `Warning: ${payload.warning}`;
    testsStatusEl.className = "tests-status warning";
  } else if ((payload.specs || []).length === 0) {
    testsStatusEl.textContent = "No Testcase elements were found.";
    testsStatusEl.className = "tests-status muted";
  } else {
    const allPassed = payload.all_passed === true;
    testsStatusEl.textContent =
      allPassed ? "All rows passed." : "Some rows did not pass.";
    testsStatusEl.className =
      allPassed ? "tests-status passed" : "tests-status failed";
  }

  if (slot.mode === "general") {
    renderGeneralResults(payload);
  } else {
    renderTestResults(payload);
  }
}

runTestsBtn.addEventListener("click", async () => {
  if (!sessionId || loaded.length === 0) return;
  const file = loaded[currentIdx];
  if (!file || !file.summary || !file.summary.has_testcases) return;
  if (fileL1Errors(file).length > 0) return;  // blocked: fix L1 errors first
  const filename = file.filename;
  const mode = perRowToggle.checked ? "per_row" : "general";

  setTestSlot(filename, { status: "running", progress: "starting...", mode, jobId: null, payload: null, message: null });
  logEvent("tests_run_started", { filename, mode });

  let res;
  try {
    res = await fetch("/api/tests/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, filename, mode }),
    });
  } catch (err) {
    setTestSlot(filename, { status: "warning", message: `Network error: ${err}`, mode });
    return;
  }
  if (!res.ok) {
    const text = await res.text();
    setTestSlot(filename, { status: "warning", message: `Server error ${res.status}: ${text}`, mode });
    return;
  }
  const startResp = await res.json();

  if (startResp.mode === "general") {
    finalizeSlot(filename, startResp, "general");
    return;
  }

  setTestSlot(filename, { status: "running", progress: "starting...", jobId: startResp.job_id, mode: "per_row" });
  pollFor(filename, startResp.job_id);
});

async function pollFor(filename, jobId) {
  while (true) {
    await new Promise((r) => setTimeout(r, 400));
    const slot = testState[filename];
    if (!slot || slot.jobId !== jobId) return;

    let snap;
    try {
      const res = await fetch(`/api/tests/progress/${jobId}`);
      if (!res.ok) {
        const t = await res.text();
        setTestSlot(filename, { status: "warning", message: `Job lookup failed: ${t}`, mode: "per_row" });
        return;
      }
      snap = await res.json();
    } catch (err) {
      setTestSlot(filename, { status: "warning", message: `Polling error: ${err}`, mode: "per_row" });
      return;
    }

    const pct = snap.total_rows
      ? Math.floor((snap.done_rows * 100) / snap.total_rows)
      : 0;
    const progress = snap.total_rows
      ? `${pct}% (${snap.done_rows}/${snap.total_rows} rows)`
      : "starting...";

    if (snap.finished) {
      finalizeSlot(filename, snap, "per_row");
      return;
    }
    setTestSlot(filename, { status: "running", progress, jobId, mode: "per_row" });
  }
}

function finalizeSlot(filename, payload, mode) {
  logEvent("tests_run_complete", {
    filename, mode, ok: payload.ok, all_passed: payload.all_passed,
  });
  if (!payload.ok) {
    setTestSlot(filename, { status: "warning", message: payload.warning || "Test runner reported an error.", mode });
  } else {
    setTestSlot(filename, { status: "done", payload, mode });
  }
}

// "Test all" — one fast pass over every uploaded file.

testAllBtn.addEventListener("click", async () => {
  if (!sessionId || loaded.length === 0) return;
  testAllBtn.disabled = true;
  testAllBtn.textContent = "Testing...";
  testAllPanel.classList.remove("hidden");
  testAllHeadEl.textContent = "Testing all files...";
  testAllListEl.innerHTML = "";
  logEvent("tests_run_all_started", { count: loaded.length });

  let data;
  try {
    const res = await fetch("/api/tests/all", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
    if (!res.ok) throw new Error(`server ${res.status}: ${await res.text()}`);
    data = await res.json();
  } catch (err) {
    testAllHeadEl.textContent = `Test all failed: ${err}`;
    testAllBtn.disabled = false;
    testAllBtn.textContent = "Test all";
    return;
  }
  testAllBtn.disabled = false;
  testAllBtn.textContent = "Test all";
  logEvent("tests_run_all_complete", data.summary || {});
  renderTestAllPanel(data);

  // Each tested file's payload is general-mode shaped — drop it into
  // testState so the per-file Tests panel and issue muting update too.
  for (const f of data.files || []) {
    if (f.status === "no_tests" || f.status === "parse_error" || f.status === "blocked") continue;
    setTestSlot(f.filename, {
      status: "done",
      payload: { ok: f.ok, warning: f.warning, specs: f.specs, all_passed: f.all_passed },
      mode: "general", jobId: null, message: null,
    });
  }
});

testAllClose.addEventListener("click", () => testAllPanel.classList.add("hidden"));

function renderTestAllPanel(data) {
  const s = data.summary || {};
  if (!s.files_with_tests) {
    testAllHeadEl.textContent = "No testcases found in any uploaded file.";
  } else {
    const head = `${s.passed}/${s.files_with_tests} circuit${s.files_with_tests === 1 ? "" : "s"} pass`;
    const extras = [];
    if (s.blocked) extras.push(`${s.blocked} blocked by L1 errors`);
    if (s.errors) extras.push(`${s.errors} error${s.errors === 1 ? "" : "s"}`);
    testAllHeadEl.textContent = head + (extras.length ? " · " + extras.join(" · ") : "");
  }
  testAllListEl.innerHTML = (data.files || []).map((f) => {
    const chip = {
      passed: `<span class="ta-chip ta-pass">pass</span>`,
      failed: `<span class="ta-chip ta-fail">fail</span>`,
      blocked: `<span class="ta-chip ta-blocked">blocked</span>`,
      error: `<span class="ta-chip ta-err">error</span>`,
      parse_error: `<span class="ta-chip ta-err">parse error</span>`,
      no_tests: `<span class="ta-chip ta-none">no tests</span>`,
    }[f.status] || `<span class="ta-chip ta-none">?</span>`;
    const detail = (f.specs || [])
      .filter((sp) => sp.status === "failed" && sp.failing_rows != null)
      .map((sp) => `${sp.failing_rows} row${sp.failing_rows === 1 ? "" : "s"} failing`)
      .join(", ");
    const warn = f.warning ? ` · ${escapeHtml(f.warning)}` : "";
    return `<div class="test-all-row" data-fname="${escapeHtml(f.filename)}">
      ${chip}
      <span class="ta-name">${escapeHtml(f.filename)}</span>
      <span class="ta-detail">${escapeHtml(detail)}${warn}</span>
    </div>`;
  }).join("");
  testAllListEl.querySelectorAll(".test-all-row").forEach((rowEl) => {
    rowEl.addEventListener("click", () => {
      const idx = loaded.findIndex((x) => x.filename === rowEl.dataset.fname);
      if (idx >= 0) { currentIdx = idx; renderCurrent(); }
    });
  });
}

function showProgress(text) {
  testsProgressTextEl.textContent = text;
  testsProgressEl.classList.remove("hidden");
}
function hideProgress() {
  testsProgressEl.classList.add("hidden");
}

function renderTestResults(payload) {
  testsResultsEl.classList.remove("empty");

  const html = payload.specs.map((spec) => {
    const headers = spec.headers || [];
    const headerCells =
      `<td class="row-idx">idx</td>` +
      headers.map((h) => `<td>${escapeHtml(h)}</td>`).join("") +
      `<td class="row-status">status</td>`;

    const rowsHtml = spec.rows.map((row) => {
      const idxCell = `<td class="row-idx">${row.index}</td>`;
      if (row.error_message) {
        const span = headers.length + 1;
        return `<tr class="${escapeHtml(row.status)}">
          ${idxCell}
          <td class="row-err" colspan="${span}">${escapeHtml(row.error_message)}</td>
        </tr>`;
      }
      const tokens = (row.raw || "").split(/\s+/).filter(Boolean);
      const tokenCells = headers.map((_, i) =>
        `<td>${escapeHtml(tokens[i] ?? "")}</td>`
      ).join("");
      let mismatchHtml = "";
      if (row.status === "failed" && Array.isArray(row.mismatches) && row.mismatches.length) {
        const parts = row.mismatches.map((m) =>
          `${escapeHtml(m.column ?? "?")}: expected ${escapeHtml(m.expected)}, got ${escapeHtml(m.found)}`
        );
        mismatchHtml = `<tr class="mismatch-row">
          <td></td>
          <td colspan="${headers.length + 1}">${parts.join(" &middot; ")}</td>
        </tr>`;
      }
      return `<tr class="${escapeHtml(row.status)}">
        ${idxCell}
        ${tokenCells}
        <td class="row-status">${escapeHtml(row.status)}</td>
      </tr>${mismatchHtml}`;
    }).join("");

    return `
      <div class="spec-title">${escapeHtml(spec.name)} &middot; ${spec.rows.length} row${spec.rows.length === 1 ? "" : "s"}</div>
      <table>
        <thead><tr>${headerCells}</tr></thead>
        <tbody>${rowsHtml}</tbody>
      </table>
    `;
  }).join("");
  testsResultsEl.innerHTML = html;
}

function renderGeneralResults(payload) {
  testsResultsEl.classList.remove("empty");
  const html = payload.specs.map((spec) => {
    const headline = renderGeneralHeadline(spec);
    return `<div class="spec-title">${escapeHtml(spec.name)} &middot; ${spec.row_count} row${spec.row_count === 1 ? "" : "s"}</div>
            <div class="spec-headline">${headline}</div>`;
  }).join("");
  testsResultsEl.innerHTML = html;
}

function renderGeneralHeadline(spec) {
  if (spec.status === "error") {
    return `<span class="neutral">runner could not match this testcase</span>`;
  }
  const pp = spec.pass_pct ?? 0;
  const fp = spec.fail_pct ?? 0;
  const parts = [];
  if (pp > 0) parts.push(`<span class="pct-pass">${pp}% passed</span>`);
  if (fp > 0) parts.push(`<span class="pct-fail">${fp}% failed</span>`);
  if (parts.length === 0) parts.push(`<span class="neutral">no rows reported</span>`);
  return parts.join(" &middot; ");
}

async function refreshJarChip() {
  let info;
  try {
    const res = await fetch("/api/config/jar");
    info = await res.json();
  } catch {
    jarStateEl.innerHTML = `<span class="jar-state-unknown">unknown</span>`;
    return;
  }
  if (info.exists) {
    jarStateEl.innerHTML = `<span class="jar-state-good">found</span>`;
    jarChipBtn.title = `Configured: ${info.path}`;
  } else {
    jarStateEl.innerHTML = `<span class="jar-state-missing">not set</span>`;
    jarChipBtn.title = "Click to set Digital.jar location";
  }
}

jarChipBtn.addEventListener("click", async () => {
  let info;
  try {
    const r = await fetch("/api/config/jar");
    info = await r.json();
  } catch {
    info = {};
  }
  jarPathInput.value = info.path || "";
  jarModalMsg.textContent = "";
  jarModalMsg.className = "modal-msg";
  jarModal.classList.remove("hidden");
});

jarCancelBtn.addEventListener("click", () => jarModal.classList.add("hidden"));

jarBrowseBtn.addEventListener("click", async () => {
  jarModalMsg.textContent = "Opening native file picker on the server...";
  jarModalMsg.className = "modal-msg";
  let info;
  try {
    const r = await fetch("/api/config/jar/browse");
    info = await r.json();
  } catch (err) {
    jarModalMsg.textContent = `Browse failed: ${err}`;
    jarModalMsg.className = "modal-msg err";
    return;
  }
  if (info.ok) {
    jarPathInput.value = info.path;
    jarModalMsg.textContent = `Selected: ${info.path}. Click Save to persist.`;
    jarModalMsg.className = "modal-msg ok";
    return;
  }
  const reason = (info.reason || "").toLowerCase();
  if (reason.includes("cancel")) {
    jarModalMsg.textContent = "";
    jarModalMsg.className = "modal-msg";
  } else {
    jarModalMsg.textContent = `Browse unavailable (${info.reason || "no reason"}).`;
    jarModalMsg.className = "modal-msg warn";
  }
});

jarSaveBtn.addEventListener("click", async () => {
  const path = jarPathInput.value.trim();
  if (!path) {
    jarModalMsg.textContent = "Path is empty.";
    jarModalMsg.className = "modal-msg err";
    return;
  }
  let res;
  try {
    res = await fetch("/api/config/jar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    });
  } catch (err) {
    jarModalMsg.textContent = `Save failed: ${err}`;
    jarModalMsg.className = "modal-msg err";
    return;
  }
  if (!res.ok) {
    const text = await res.text();
    jarModalMsg.textContent = `Server rejected: ${text}`;
    jarModalMsg.className = "modal-msg err";
    return;
  }
  jarModalMsg.textContent = "Saved.";
  jarModalMsg.className = "modal-msg ok";
  await refreshJarChip();
  setTimeout(() => jarModal.classList.add("hidden"), 600);
});


llmStubBtn.addEventListener("click", async () => {
  try {
    const res = await fetch("/api/llm/explain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId }),
    });
    const body = await res.text();
    alert(`Layer 3 says: ${res.status} - ${body}`);
  } catch (err) {
    alert(`Layer 3 unreachable: ${err}`);
  }
});

refreshJarChip();

// Cached model catalog from /api/llm/models. Re-fetched on every key
let modelCatalog = [];

function _setKeyRowStatus(provider, configured) {
  const el = keyEls[provider].status;
  if (configured) {
    el.textContent = "set";
    el.className = "key-row-status set";
  } else {
    el.textContent = "missing";
    el.className = "key-row-status missing";
  }
}

async function refreshKeyChip() {
  try {
    const r = await fetch("/api/config/api_key");
    const info = await r.json();
    const per = info.providers || {};
    for (const p of KEY_PROVIDERS) _setKeyRowStatus(p, per[p]);
    const set = KEY_PROVIDERS.filter((p) => per[p]);
    if (set.length === 0) {
      keyStateEl.innerHTML = `<span class="jar-state-missing">missing</span>`;
      keyChipBtn.title = "No LLM API keys configured. Click to add.";
    } else if (set.length === KEY_PROVIDERS.length) {
      keyStateEl.innerHTML = `<span class="jar-state-good">${set.length}/${KEY_PROVIDERS.length}</span>`;
      keyChipBtn.title = "All providers configured.";
    } else {
      keyStateEl.innerHTML = `<span class="jar-state-good">${set.length}/${KEY_PROVIDERS.length}</span>`;
      keyChipBtn.title = `Configured: ${set.join(", ")}`;
    }
  } catch (err) {
    keyStateEl.innerHTML = `<span class="jar-state-unknown">unknown</span>`;
    keyChipBtn.title = "Could not read API key status. Click to add a key.";
    console.error("DLC: refreshKeyChip failed:", err);
  }
  await refreshModelCatalog();
}

async function refreshModelCatalog() {
  try {
    const r = await fetch("/api/llm/models");
    const d = await r.json();
    modelCatalog = d.models || [];
    populateModelSelect(d.default);
    populateGraderSelect();
  } catch {
  }
}

function populateModelSelect(defaultModel) {
  const byProvider = {};
  for (const m of modelCatalog) {
    (byProvider[m.provider] = byProvider[m.provider] || []).push(m);
  }
  const previous = l2ModelSelect.value;
  let html = "";
  for (const provider of ["anthropic", "openai"]) {
    const arr = byProvider[provider] || [];
    if (arr.length === 0) continue;
    html += `<optgroup label="${provider}">`;
    for (const m of arr) {
      const tag = m.key_configured ? "" : " (no key)";
      html += `<option value="${m.id}" ${m.key_configured ? "" : "disabled"}>${m.label}${tag}</option>`;
    }
    html += `</optgroup>`;
  }
  l2ModelSelect.innerHTML = html;
  const enabled = modelCatalog.filter((m) => m.key_configured).map((m) => m.id);
  if (enabled.includes(previous)) {
    l2ModelSelect.value = previous;
  } else if (enabled.length > 0) {
    l2ModelSelect.value = enabled[0];
  } else if (defaultModel) {
    l2ModelSelect.value = defaultModel;
  }
}

const GRADER_DEFAULT = "claude-sonnet-4-6";
function populateGraderSelect() {
  if (!graderSelect) return;
  const byProvider = {};
  for (const m of modelCatalog) {
    (byProvider[m.provider] = byProvider[m.provider] || []).push(m);
  }
  const previous = graderSelect.value;
  let html = "";
  for (const provider of ["anthropic", "openai"]) {
    const arr = byProvider[provider] || [];
    if (arr.length === 0) continue;
    html += `<optgroup label="${provider}">`;
    for (const m of arr) {
      const tag = m.key_configured ? "" : " (no key)";
      html += `<option value="${m.id}" ${m.key_configured ? "" : "disabled"}>${m.label}${tag}</option>`;
    }
    html += `</optgroup>`;
  }
  graderSelect.innerHTML = html;
  const enabled = modelCatalog.filter((m) => m.key_configured).map((m) => m.id);
  if (enabled.includes(previous)) graderSelect.value = previous;
  else if (enabled.includes(GRADER_DEFAULT)) graderSelect.value = GRADER_DEFAULT;
  else if (enabled.length > 0) graderSelect.value = enabled[0];
}

if (graderSelect) {
  graderSelect.addEventListener("change", () => {
    if (lastGradedSummary) gradeCurrentSummary(lastGradedSummary);
  });
}

refreshKeyChip();

keyChipBtn.addEventListener("click", () => {
  for (const p of KEY_PROVIDERS) {
    keyEls[p].input.value = "";
    keyEls[p].msg.textContent = "";
    keyEls[p].msg.className = "modal-msg key-row-msg";
  }
  keyModal.classList.remove("hidden");
});
keyCancelBtn.addEventListener("click", () => keyModal.classList.add("hidden"));

function _showKeyRowMsg(provider, text, cls) {
  const el = keyEls[provider].msg;
  el.textContent = text;
  el.className = `modal-msg key-row-msg ${cls}`;
}

document.querySelectorAll(".key-save-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const provider = btn.dataset.provider;
    const key = keyEls[provider].input.value.trim();
    if (!key) {
      _showKeyRowMsg(provider, "Empty.", "err");
      return;
    }
    let res;
    try {
      res = await fetch("/api/config/api_key", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ provider, key }),
      });
    } catch (err) {
      _showKeyRowMsg(provider, `Save failed: ${err}`, "err");
      return;
    }
    if (!res.ok) {
      const t = await res.text();
      _showKeyRowMsg(provider, `Rejected: ${t}`, "err");
      return;
    }
    _showKeyRowMsg(provider, "Saved.", "ok");
    keyEls[provider].input.value = "";
    await refreshKeyChip();
  });
});

document.querySelectorAll(".key-clear-btn").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const provider = btn.dataset.provider;
    if (!confirm(`Clear the saved ${provider} key from ~/.dlc/config.json?`)) return;
    let res;
    try {
      res = await fetch(`/api/config/api_key?provider=${provider}`, { method: "DELETE" });
    } catch (err) {
      _showKeyRowMsg(provider, `Clear failed: ${err}`, "err");
      return;
    }
    if (!res.ok) {
      const t = await res.text();
      _showKeyRowMsg(provider, `Clear failed: ${t}`, "err");
      return;
    }
    const d = await res.json();
    _showKeyRowMsg(
      provider,
      d.configured ? "Cleared from config (env var still set)." : "Cleared.",
      "ok",
    );
    keyEls[provider].input.value = "";
    await refreshKeyChip();
  });
});

let l2LibraryFilename = null;  

async function refreshLibrary() {
  if (!sessionId || loaded.length === 0) {
    libraryGridEl.innerHTML = `<div class="muted">Load a circuit on the Dashboard tab to populate the library.</div>`;
    l2LibraryFilename = null;
    return;
  }
  const file = loaded[currentIdx];
  if (file.error) {
    libraryGridEl.innerHTML = `<div class="muted">Could not parse this file; no library to show.</div>`;
    return;
  }
  if (l2LibraryFilename === file.filename) return;

  libraryGridEl.innerHTML = `<div class="muted">Loading library...</div>`;
  let res;
  try {
    res = await fetch(`/api/library?session_id=${encodeURIComponent(sessionId)}&filename=${encodeURIComponent(file.filename)}`);
  } catch (err) {
    libraryGridEl.innerHTML = `<div style="color:#991b1b">Library fetch failed: ${escapeHtml(String(err))}</div>`;
    return;
  }
  if (!res.ok) {
    libraryGridEl.innerHTML = `<div style="color:#991b1b">Library error: ${res.status}</div>`;
    return;
  }
  const data = await res.json();
  renderLibrary(data.cards || []);
  l2LibraryFilename = file.filename;
}

function renderLibrary(cards) {
  if (cards.length === 0) {
    libraryGridEl.innerHTML = `<div class="muted">No components in this circuit.</div>`;
    return;
  }
  libraryGridEl.innerHTML = cards.map((c, i) => `
    <div class="library-card" data-card-idx="${i}">
      ${c.count > 1 ? `<span class="count">${c.count}</span>` : ""}
      <img src="/static/images/components/${escapeHtml(c.image)}"
           alt="${escapeHtml(c.display_name)}"
           onerror="this.onerror=null;this.src='/static/images/components/placeholder.png';" />
      <div class="name">${escapeHtml(c.display_name)}</div>
    </div>
  `).join("");
  libraryGridEl.querySelectorAll(".library-card").forEach((el) => {
    const card = cards[parseInt(el.dataset.cardIdx, 10)];
    el.addEventListener("click", () => openCardDetail(card, { pinned: true }));
    if (CARD_HOVER_CAPABLE) {
      el.addEventListener("mouseenter", () => onCardHover(card));
      el.addEventListener("mouseleave", scheduleCardClose);
    }
  });
}

const CARD_HOVER_CAPABLE =
  !!(window.matchMedia && window.matchMedia("(hover: hover) and (pointer: fine)").matches);
const CARD_OPEN_DELAY = 180;   
const CARD_CLOSE_DELAY = 240; 
let cardPinned = false;
let cardOpenTimer = null;
let cardCloseTimer = null;

function _clearCardTimers() {
  if (cardOpenTimer) { clearTimeout(cardOpenTimer); cardOpenTimer = null; }
  if (cardCloseTimer) { clearTimeout(cardCloseTimer); cardCloseTimer = null; }
}

function onCardHover(card) {
  if (cardPinned) return;                     
  _clearCardTimers();
  if (!cardOverlay.classList.contains("hidden")) {
    openCardDetail(card, { pinned: false });    
  } else {
    cardOpenTimer = setTimeout(() => openCardDetail(card, { pinned: false }), CARD_OPEN_DELAY);
  }
}

function scheduleCardClose() {
  if (cardPinned) return;
  _clearCardTimers();
  cardCloseTimer = setTimeout(closeCardDetail, CARD_CLOSE_DELAY);
}

function openCardDetail(card, { pinned = false } = {}) {
  if (!card) return;
  _clearCardTimers();
  cardPinned = pinned;
  cardDetail.innerHTML =
    `<button class="card-close" type="button" aria-label="Close">&times;</button>` +
    renderCardDetail(card);
  cardOverlay.classList.remove("hidden");
  cardOverlay.classList.toggle("preview", !pinned); 
  if (pinned) cardDetail.focus({ preventScroll: true });
  cardDetail.scrollTop = 0;
}

function closeCardDetail() {
  _clearCardTimers();
  cardPinned = false;
  cardOverlay.classList.add("hidden");
  cardOverlay.classList.remove("preview");
  cardDetail.innerHTML = "";
}

cardDetail.addEventListener("mouseenter", () => { if (!cardPinned) _clearCardTimers(); });
cardDetail.addEventListener("mouseleave", scheduleCardClose);

cardDetail.addEventListener("click", (e) => {
  if (e.target.closest(".card-close")) closeCardDetail();
});

cardOverlay.addEventListener("click", (e) => {
  if (e.target === cardOverlay) closeCardDetail();
});

window.addEventListener("keydown", (e) => {
  if (cardOverlay.classList.contains("hidden")) return;
  if (e.key === "ArrowDown") {
    cardDetail.scrollTop += 40; e.preventDefault();
  } else if (e.key === "ArrowUp") {
    cardDetail.scrollTop -= 40; e.preventDefault();
  } else if (e.key === "PageDown") {
    cardDetail.scrollTop += cardDetail.clientHeight - 30; e.preventDefault();
  } else if (e.key === "PageUp") {
    cardDetail.scrollTop -= cardDetail.clientHeight - 30; e.preventDefault();
  } else if (e.key === "Escape") {
    closeCardDetail(); e.preventDefault();
  }
});

function renderCardDetail(card) {
  const extra = card.extra || {};
  const truth2 = (extra.truth_table_2 || []).length
    ? `<h4>Truth table (2 inputs)</h4>` + renderTruthTable(extra.truth_table_2)
    : "";
  const truth3 = (extra.truth_table_3 || []).length
    ? `<h4>Truth table (3 inputs)</h4>` + renderTruthTable(extra.truth_table_3)
    : "";
  const behaviour = extra.behavior_example
    ? `<h4>Example behavior</h4><div class="behavior">${escapeHtml(extra.behavior_example)}</div>`
    : "";
  const note = card.transistor_note
    ? `<p class="muted" style="font-size:11.5px;">${escapeHtml(card.transistor_note)}</p>`
    : "";
  return `
    <div>
      <img class="detail-img" src="/static/images/components/${escapeHtml(card.image)}"
           alt="${escapeHtml(card.display_name)}"
           onerror="this.onerror=null;this.src='/static/images/components/placeholder.png';" />
    </div>
    <div>
      <div class="detail-head">
        <div class="detail-name">${escapeHtml(card.display_name)}</div>
        <div class="detail-meta">
          <span class="pill-small">${escapeHtml(card.port_summary || "")}</span>
          <span>transistors: ${escapeHtml(card.transistor_count || "?")}</span>
        </div>
      </div>
      <div class="detail-body">
        <p>${escapeHtml(card.description || "")}</p>
        ${note}
        ${truth2}
        ${truth3}
        ${behaviour}
      </div>
    </div>
  `;
}

function renderTruthTable(rows) {
  if (rows.length === 0) return "";
  const inLen = rows[0].in.length;
  const headers = [];
  for (let i = 0; i < inLen; i++) headers.push(`<td>in${i}</td>`);
  headers.push(`<td>out</td>`);
  const body = rows.map((r) => {
    const cls = r.out ? "true" : "false";
    const cells = r.in.map((v) => `<td>${v}</td>`).join("");
    return `<tr class="${cls}">${cells}<td class="out">${r.out}</td></tr>`;
  }).join("");
  return `<table class="truth-table"><thead><tr>${headers.join("")}</tr></thead><tbody>${body}</tbody></table>`;
}

goalTextarea.addEventListener("input", () => {
  const chars = goalTextarea.value.length;
  goalCountEl.textContent = `${chars} / 500 characters`;
  goalCountEl.style.color = chars >= 500 ? "#b91c1c" : "";
});

l2LlmBtn.addEventListener("click", async () => {
  if (!sessionId || loaded.length === 0) {
    l2LlmStatus.textContent = "Load a circuit first.";
    l2LlmStatus.className = "l2-llm-status error";
    return;
  }
  const file = loaded[currentIdx];
  if (file.error) {
    l2LlmStatus.textContent = "Current file failed to parse.";
    l2LlmStatus.className = "l2-llm-status error";
    return;
  }

  const goal = goalTextarea.value.trim();
  if (goal.length > 500) {
    l2LlmStatus.textContent = "Goal too long (500 character max).";
    l2LlmStatus.className = "l2-llm-status error";
    return;
  }

  let testSummary = null;
  const slot = testState[file.filename];
  if (slot && slot.status === "done" && slot.payload) {
    if (slot.payload.all_passed === true) testSummary = "All rows passed.";
    else if (slot.payload.all_passed === false) testSummary = "Some rows failed.";
  }

  const selectedModel = l2ModelSelect.value || null;
  const selectedInfo = modelCatalog.find((m) => m.id === selectedModel);
  if (selectedInfo && !selectedInfo.key_configured) {
    l2LlmStatus.textContent =
      `No ${selectedInfo.provider} API key configured. Open the API keys chip in the toolbar.`;
    l2LlmStatus.className = "l2-llm-status error";
    return;
  }

  l2LlmBtn.disabled = true;
  l2LlmStatus.innerHTML =
    `Talking to ${escapeHtml(selectedInfo ? selectedInfo.label : "the model")}` +
    `<span class="llm-dots" aria-hidden="true"><i></i><i></i><i></i></span>`;
  l2LlmStatus.className = "l2-llm-status running";
  l2LlmOutput.innerHTML = "";
  l2LlmOutput.classList.add("empty");
  logEvent("l2_llm_started", {
    filename: file.filename, has_goal: goal.length > 0, model: selectedModel,
  });

  let res;
  try {
    res = await fetch("/api/llm/explain", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        filename: file.filename,
        student_goal: goal || null,
        test_summary: testSummary,
        model: selectedModel,
      }),
    });
  } catch (err) {
    l2LlmStatus.textContent = `Network error: ${err}`;
    l2LlmStatus.className = "l2-llm-status error";
    l2LlmBtn.disabled = false;
    return;
  }
  l2LlmBtn.disabled = false;

  if (!res.ok) {
    const t = await res.text();
    l2LlmStatus.textContent = `Server error ${res.status}: ${t}`;
    l2LlmStatus.className = "l2-llm-status error";
    return;
  }
  const payload = await res.json();
  logEvent("l2_llm_complete", { filename: file.filename, ok: payload.ok, gated: !!payload.gate_message });

  if (!payload.ok) {
    l2LlmStatus.textContent = `Error: ${payload.error || "unknown"}`;
    l2LlmStatus.className = "l2-llm-status error";
    return;
  }

  if (payload.gate_message) {
    l2LlmStatus.textContent = "Precheck blocked the summary.";
    l2LlmStatus.className = "l2-llm-status gated";
    l2LlmOutput.classList.remove("empty");
    l2LlmOutput.textContent = payload.gate_message;
    return;
  }

  l2LlmStatus.textContent = "Done.";
  l2LlmStatus.className = "l2-llm-status done";
  l2LlmOutput.classList.remove("empty");
  l2LlmOutput.innerHTML = renderL2ParagraphCards(payload.text || "(empty response)");
  wireL2CardEvents();

  // Grade the summary that was just shown.
  if (payload.text) gradeCurrentSummary(payload.text);
});

// ---------- L2 summary grade: credibility donut + hover detail ----------
const GRADE_COLORS = ["#3b82f6", "#10b981", "#8b5cf6", "#f59e0b", "#ec4899", "#06b6d4", "#f97316"];

function _bandColor(band) {
  return band === "green" ? "#16a34a" : band === "yellow" ? "#d97706" : "#dc2626";
}
function _polar(cx, cy, r, deg) {
  const a = (deg - 90) * Math.PI / 180;
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
}
function _arcPath(cx, cy, r, start, end) {
  if (end - start >= 359.999) end = start + 359.999;
  const [x1, y1] = _polar(cx, cy, r, start);
  const [x2, y2] = _polar(cx, cy, r, end);
  const large = end - start > 180 ? 1 : 0;
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

function _resetGrade() {
  lastGradedSummary = null;
  if (gradeBody) {
    gradeBody.innerHTML = `<div class="muted">Summarize a circuit to see its credibility grade out of 100.</div>`;
  }
}

async function gradeCurrentSummary(summaryText) {
  if (!gradeBody || !sessionId || loaded.length === 0) return;
  const file = loaded[currentIdx];
  if (!file || file.error) return;
  lastGradedSummary = summaryText;
  const graderModel = graderSelect ? graderSelect.value || null : null;

  gradeBody.innerHTML =
    `<span class="muted">Grading with ${escapeHtml(graderModel || "default")}` +
    `<span class="llm-dots" aria-hidden="true"><i></i><i></i><i></i></span></span>`;

  let res;
  try {
    res = await fetch("/api/llm/grade", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        filename: file.filename,
        summary_text: summaryText,
        student_goal: goalTextarea.value.trim() || null,
        grader_model: graderModel,
      }),
    });
  } catch (err) {
    gradeBody.innerHTML = `<span style="color:#b91c1c">Grade request failed: ${escapeHtml(String(err))}</span>`;
    return;
  }
  let g;
  try { g = await res.json(); } catch { g = null; }
  if (!g || !g.ok) {
    gradeBody.innerHTML = `<span style="color:#b91c1c">${escapeHtml((g && g.error) || ("Grader error " + res.status))}</span>`;
    return;
  }
  renderGradeDonut(g);

  // One summary -> one grade. A low score only SUGGESTS a re-run; the
  // user decides (the old auto-retry silently re-clicked Summarize).
  if (typeof g.total === "number" && g.total < GRADE_HINT_THRESHOLD) {
    const host = gradeBody.querySelector(".grade-info") || gradeBody;
    const n = document.createElement("div");
    n.className = "grade-note";
    n.textContent =
      `Score ${g.total} is below ${GRADE_HINT_THRESHOLD} - click ` +
      `"Summarize circuit" again if you want a fresh attempt.`;
    host.appendChild(n);
  }
}

function renderGradeDonut(g) {
  const subs = g.sub_scores || [];
  const cx = 80, cy = 80, r = 62, sw = 22, gap = 3;
  let cursor = 0, arcs = "";
  for (let i = 0; i < subs.length; i++) {
    const s = subs[i];
    const span = (s.max / 100) * 360;
    const start = cursor + gap / 2;
    const end = cursor + span - gap / 2;
    const valEnd = start + (end - start) * (s.max ? s.score / s.max : 0);
    const color = GRADE_COLORS[i % GRADE_COLORS.length];
    arcs += `<path d="${_arcPath(cx, cy, r, start, end)}" stroke="${color}" stroke-opacity="0.18" stroke-width="${sw}" fill="none"></path>`;
    if (valEnd > start + 0.2) {
      arcs += `<path class="grade-val" data-i="${i}" d="${_arcPath(cx, cy, r, start, valEnd)}" stroke="${color}" stroke-width="${sw}" fill="none"></path>`;
    }
    arcs += `<path class="grade-seg-hit" data-i="${i}" d="${_arcPath(cx, cy, r, start, end)}" stroke="transparent" stroke-width="${sw + 6}" fill="none"></path>`;
    cursor += span;
  }
  const svg =
    `<svg class="grade-donut" viewBox="0 0 160 160" role="img" aria-label="grade ${g.total} of 100">` +
    arcs +
    `<text class="grade-total" x="80" y="86" fill="${_bandColor(g.band)}">${g.total}</text>` +
    `<text class="grade-outof" x="80" y="104">/ 100</text></svg>`;

  let legend = `<div class="grade-legend">`;
  for (let i = 0; i < subs.length; i++) {
    const s = subs[i];
    legend +=
      `<div class="grade-legend-row" data-i="${i}">` +
      `<span class="grade-swatch" style="background:${GRADE_COLORS[i % GRADE_COLORS.length]}"></span>` +
      `<span class="lg-label">${escapeHtml(s.label)}</span>` +
      `<span class="lg-src">${escapeHtml(s.source)}</span>` +
      `<span class="lg-score">${s.score}/${s.max}</span></div>`;
  }
  legend += `</div>`;

  const note = g.capped
    ? `<div class="grade-note capped">Capped at ${g.total} - hallucinated: ${escapeHtml((g.hallucinated_items || []).join(", ") || "yes")}.</div>`
    : "";
  // Flags = problems the grader caught in the SUMMARY's text (not in
  // the circuit) that the sub-scores don't already express.
  let flags = "";
  if (g.flags && g.flags.length) {
    const items = g.flags.map((f) => {
      if (typeof f === "string") return `<li>${escapeHtml(f)}</li>`;
      const para = f.paragraph ? `<span class="flag-para">P${f.paragraph}</span> ` : "";
      const quote = f.quote ? `<span class="flag-quote">"${escapeHtml(f.quote)}"</span> — ` : "";
      return `<li>${para}${quote}${escapeHtml(f.issue || "")}</li>`;
    }).join("");
    flags =
      `<div class="grade-flags" id="grade-flags">` +
      `<div class="grade-flags-title">Grader feedback: ${g.flags.length} issue${g.flags.length === 1 ? "" : "s"} ` +
      `in this summary's wording</div>` +
      `<ul>${items}</ul></div>`;
  }

  gradeBody.innerHTML =
    `<div class="grade-card">${svg}<div class="grade-info">${legend}` +
    `<div class="grade-detail muted">Hover a slice or row for how it is graded.</div>${note}</div></div>${flags}`;

  const detail = gradeBody.querySelector(".grade-detail");
  const card = gradeBody.querySelector(".grade-card");
  const valArcs = gradeBody.querySelectorAll(".grade-val");
  const legRows = gradeBody.querySelectorAll(".grade-legend-row");
  const show = (i) => {
    const s = subs[i];
    if (!s) return;
    detail.classList.remove("muted");
    detail.innerHTML =
      `<div class="gd-title">${escapeHtml(s.label)} - ${s.score}/${s.max} <span class="lg-src">${escapeHtml(s.source)}</span></div>` +
      `<div class="gd-how">${escapeHtml(s.description || "")}</div>` +
      (s.rationale ? `<div class="gd-why">"${escapeHtml(s.rationale)}"</div>` : "");
  };
  // Highlight + "pop" the slice WITHOUT moving the hit geometry, so the
  // pointer never bounces in/out near a slice edge. Hovering a slice OR its
  // legend row highlights the same slice; cleared only on leaving the block.
  const highlight = (i) => {
    valArcs.forEach((p) => {
      const on = parseInt(p.dataset.i, 10) === i;
      p.setAttribute("stroke-width", on ? (sw + 6) : sw);
      p.style.opacity = on ? "1" : "0.5";
    });
    legRows.forEach((row) => row.classList.toggle("active", parseInt(row.dataset.i, 10) === i));
    show(i);
  };
  const clearHi = () => {
    valArcs.forEach((p) => { p.setAttribute("stroke-width", sw); p.style.opacity = "1"; });
    legRows.forEach((row) => row.classList.remove("active"));
    detail.classList.add("muted");
    detail.textContent = "Hover a slice or row for how it is graded.";
  };
  gradeBody.querySelectorAll(".grade-seg-hit").forEach((el) =>
    el.addEventListener("mouseenter", () => highlight(parseInt(el.dataset.i, 10))));
  legRows.forEach((row) =>
    row.addEventListener("mouseenter", () => highlight(parseInt(row.dataset.i, 10))));
  if (card) card.addEventListener("mouseleave", clearHi);
}

const L2_CARD_TYPES = [
  { key: "purpose", name: "Overall purpose",     hint: "What this circuit does." },
  { key: "subs",    name: "Subcircuits",         hint: "Role of each child .dig." },
  { key: "flow",    name: "Signal flow example", hint: "One row traced end to end." },
  { key: "goal",    name: "Goal comparison",     hint: "Versus what you asked for." },
  { key: "topo",    name: "Topology",            hint: "Architectural pattern." },
  { key: "lect",    name: "Course concepts",     hint: "Most relevant lectures." },
];

function _splitL2Paragraphs(text) {
  const chunks = text
    .split(/\n\s*\n+/)
    .map((s) => s.trim())
    .filter(Boolean);
  const cleaned = chunks.map((c) =>
    c.replace(/^\s*[\(\[]?\s*\d+\s*[\)\]\.\:]\s*/, "").trim()
  );
  if (cleaned.length > 6) {
    const head = cleaned.slice(0, 5);
    const tail = cleaned.slice(5).join("\n\n");
    return [...head, tail];
  }
  return cleaned;
}

function renderL2ParagraphCards(rawText) {
  const paras = _splitL2Paragraphs(rawText);
  if (paras.length === 0) {
    return `<div class="muted">(empty response)</div>`;
  }
  const cards = L2_CARD_TYPES.map((type, idx) => {
    const body = paras[idx] || "";
    const empty = body.length === 0;
    return `
      <div class="l2-card l2-card-${type.key} ${empty ? "l2-card-empty" : ""}" data-card-idx="${idx}">
        <div class="l2-card-head" role="button" tabindex="0">
          <span class="l2-card-num">${idx + 1}</span>
          <span class="l2-card-name">${type.name}</span>
          <span class="l2-card-hint">${type.hint}</span>
          <span class="l2-card-toggle">+</span>
        </div>
        <div class="l2-card-body">${empty ? `<span class="muted">(no content for this section)</span>` : escapeHtml(body)}</div>
      </div>
    `;
  }).join("");
  return `<div class="l2-card-grid">${cards}</div>`;
}

function wireL2CardEvents() {
  const cards = l2LlmOutput.querySelectorAll(".l2-card");
  cards.forEach((card) => {
    const head = card.querySelector(".l2-card-head");
    const toggle = () => card.classList.toggle("expanded");
    head.addEventListener("click", toggle);
    head.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        toggle();
      }
    });
  });
}

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
  const l2page = document.querySelector('.page[data-page="l2"]');
  if (!l2page || l2page.hasAttribute("hidden")) return;
  const n = parseInt(e.key, 10);
  if (!Number.isFinite(n) || n < 1 || n > 6) return;
  const card = l2LlmOutput.querySelector(`.l2-card[data-card-idx="${n - 1}"]`);
  if (card) {
    card.classList.toggle("expanded");
    e.preventDefault();
  }
});

// Tab switching

const tabButtons = document.querySelectorAll(".tabs .tab");
const pages = document.querySelectorAll(".page");

function showTab(name) {
  tabButtons.forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === name);
  });
  pages.forEach((p) => {
    if (p.dataset.page === name) {
      p.removeAttribute("hidden");
    } else {
      p.setAttribute("hidden", "");
    }
  });
  if (name === "main" && cy) {
    setTimeout(() => { try { cy.resize(); cy.fit(undefined, 60); } catch {} }, 0);
  }
  if (name === "l2") {
    refreshLibrary();
  }
  logEvent("tab_switch", { tab: name });
}

tabButtons.forEach((b) => {
  b.addEventListener("click", () => showTab(b.dataset.tab));
});

function returnToMain() { showTab("main"); }

fileInput.addEventListener("change", () => returnToMain());
fileSelect.addEventListener("change", () => returnToMain());
prevBtn.addEventListener("click", () => returnToMain());
nextBtn.addEventListener("click", () => returnToMain());
clearBtn.addEventListener("click", () => returnToMain());