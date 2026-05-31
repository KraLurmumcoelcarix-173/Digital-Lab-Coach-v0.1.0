/* Digital Lab Coach front end. Loads Cytoscape, handles multi-file
   uploads, renders signal-flow graph, drives summary + hover popup.
*/

cytoscape.use(window.cytoscapeDagre);

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
const keyInput      = document.getElementById("key-input");
const keySaveBtn    = document.getElementById("key-save-btn");
const keyCancelBtn  = document.getElementById("key-cancel-btn");
const keyModalMsg   = document.getElementById("key-modal-msg");
const libraryGridEl = document.getElementById("library-grid");
const cardOverlay   = document.getElementById("card-overlay");
const cardDetail    = document.getElementById("card-detail");
const goalTextarea  = document.getElementById("goal-textarea");
const goalCountEl   = document.getElementById("goal-count");
const l2LlmBtn      = document.getElementById("l2-llm-btn");
const l2LlmStatus   = document.getElementById("l2-llm-status");
const l2LlmOutput   = document.getElementById("l2-llm-output");

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
  goalCountEl.textContent = "0 words";
  l2LlmStatus.textContent = "";
  l2LlmOutput.innerHTML = "";
  l2LlmOutput.classList.add("empty");
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
    summaryEl.textContent = "Upload failed: " + err;
    return;
  }
  if (!res.ok) {
    const text = await res.text();
    summaryEl.innerHTML = `<span style="color:#991b1b">${escapeHtml(text)}</span>`;
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
}

function renderGraph(graph) {
  placeholder.classList.add("hidden");

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
      return `<tr class="${escapeHtml(row.status)}">
        ${idxCell}
        ${tokenCells}
        <td class="row-status">${escapeHtml(row.status)}</td>
      </tr>`;
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

async function refreshKeyChip() {
  let info;
  try {
    const r = await fetch("/api/config/api_key");
    info = await r.json();
  } catch {
    keyStateEl.innerHTML = `<span class="jar-state-unknown">unknown</span>`;
    return;
  }
  if (info.configured) {
    keyStateEl.innerHTML = `<span class="jar-state-good">set</span>`;
    keyChipBtn.title = "Anthropic API key configured";
  } else {
    keyStateEl.innerHTML = `<span class="jar-state-missing">missing</span>`;
    keyChipBtn.title = "Click to set your Anthropic API key";
  }
}
refreshKeyChip();

keyChipBtn.addEventListener("click", () => {
  keyInput.value = "";
  keyModalMsg.textContent = "";
  keyModalMsg.className = "modal-msg";
  keyModal.classList.remove("hidden");
  setTimeout(() => keyInput.focus(), 50);
});
keyCancelBtn.addEventListener("click", () => keyModal.classList.add("hidden"));
keySaveBtn.addEventListener("click", async () => {
  const key = keyInput.value.trim();
  if (!key) {
    keyModalMsg.textContent = "Empty.";
    keyModalMsg.className = "modal-msg err";
    return;
  }
  let res;
  try {
    res = await fetch("/api/config/api_key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ key }),
    });
  } catch (err) {
    keyModalMsg.textContent = `Save failed: ${err}`;
    keyModalMsg.className = "modal-msg err";
    return;
  }
  if (!res.ok) {
    const t = await res.text();
    keyModalMsg.textContent = `Rejected: ${t}`;
    keyModalMsg.className = "modal-msg err";
    return;
  }
  keyModalMsg.textContent = "Saved.";
  keyModalMsg.className = "modal-msg ok";
  await refreshKeyChip();
  setTimeout(() => keyModal.classList.add("hidden"), 500);
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
    el.addEventListener("click", () => openCardDetail(cards[parseInt(el.dataset.cardIdx, 10)]));
    el.addEventListener("mouseenter", () => openCardDetail(cards[parseInt(el.dataset.cardIdx, 10)]));
  });
}

function openCardDetail(card) {
  if (!card) return;
  cardDetail.innerHTML = renderCardDetail(card);
  cardOverlay.classList.remove("hidden");
  cardDetail.focus({ preventScroll: true });
  cardDetail.scrollTop = 0;
}
function closeCardDetail() {
  cardOverlay.classList.add("hidden");
  cardDetail.innerHTML = "";
}
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
  const words = (goalTextarea.value.trim().match(/\S+/g) || []).length;
  goalCountEl.textContent = `${words} word${words === 1 ? "" : "s"}`;
  goalCountEl.style.color = words > 200 ? "#b91c1c" : "";
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
  const words = (goal.match(/\S+/g) || []).length;
  if (words > 200) {
    l2LlmStatus.textContent = "Goal too long (200 word max).";
    l2LlmStatus.className = "l2-llm-status error";
    return;
  }

  let testSummary = null;
  const slot = testState[file.filename];
  if (slot && slot.status === "done" && slot.payload) {
    if (slot.payload.all_passed === true) testSummary = "All rows passed.";
    else if (slot.payload.all_passed === false) testSummary = "Some rows failed.";
  }

  l2LlmBtn.disabled = true;
  l2LlmStatus.textContent = "Feeding LLM the .dig files...";
  l2LlmStatus.className = "l2-llm-status running";
  l2LlmOutput.innerHTML = "";
  l2LlmOutput.classList.add("empty");
  logEvent("l2_llm_started", { filename: file.filename, has_goal: goal.length > 0 });

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
  const usage = payload.usage
    ? `\n\n[${payload.model} - in:${payload.usage.input_tokens} out:${payload.usage.output_tokens} tokens]`
    : "";
  l2LlmOutput.innerHTML = `${escapeHtml(payload.text || "(empty response)")}<div class="usage">${escapeHtml(usage.trim())}</div>`;
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