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

let sessionId = null;

const eventLog = [];
function logEvent(kind, details = {}) {
  eventLog.push({ ts: Date.now(), kind, ...details });
}
window.dlcEventLog = eventLog;

// Mute-if-passing state
const MUTE_THRESHOLD = 3;
let testsPassed = null;        
let mutedByUser = new Set();  
let activeIssueIdx = null;     

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
  testsPassed = null;
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

function updateTestsStatus() {
  if (testsPassed === true) {
    testsStatusEl.textContent = "Tests: all passed";
    testsStatusEl.className = "tests-status passed";
  } else if (testsPassed === false) {
    testsStatusEl.textContent = "Tests: failures detected";
    testsStatusEl.className = "tests-status failed";
  } else {
    testsStatusEl.textContent = "Tests: not yet run (test runner integration pending)";
    testsStatusEl.className = "tests-status muted";
  }
}
updateTestsStatus();

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
  const shouldMute =
    muteToggle.checked
    && testsPassed === true
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
    return;
  }
  runTestsBtn.disabled = false;
  runTestsBtn.classList.remove("running");
  runTestsBtn.textContent = "Run tests";
  testsStatusEl.textContent =
    `Ready: ${file.summary.testcase_count} testcase${file.summary.testcase_count === 1 ? "" : "s"} found. Click "Run tests" to execute.`;
  testsStatusEl.className = "tests-status muted";
}

runTestsBtn.addEventListener("click", async () => {
  if (!sessionId || loaded.length === 0) return;
  const file = loaded[currentIdx];
  if (!file || !file.summary || !file.summary.has_testcases) return;

  runTestsBtn.disabled = true;
  runTestsBtn.classList.add("running");
  runTestsBtn.textContent = "Running...";
  testsStatusEl.textContent = "Running Digital.jar per-row...";
  testsStatusEl.className = "tests-status muted";
  testsResultsEl.innerHTML = "";
  testsResultsEl.classList.add("empty");
  logEvent("tests_run_started", { filename: file.filename });

  let res;
  try {
    res = await fetch("/api/tests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        session_id: sessionId,
        filename: file.filename,
      }),
    });
  } catch (err) {
    setTestsWarning(`Network error: ${err}`);
    runTestsBtn.disabled = false;
    runTestsBtn.classList.remove("running");
    runTestsBtn.textContent = "Run tests";
    return;
  }
  runTestsBtn.disabled = false;
  runTestsBtn.classList.remove("running");
  runTestsBtn.textContent = "Run tests";

  if (!res.ok) {
    const text = await res.text();
    setTestsWarning(`Server error ${res.status}: ${text}`);
    return;
  }

  const payload = await res.json();
  logEvent("tests_run_complete", {
    filename: file.filename,
    ok: payload.ok,
    all_passed: payload.all_passed,
  });

  if (!payload.ok) {
    setTestsWarning(payload.warning || "Test runner reported an error.");
    return;
  }
  if (payload.warning) {
    setTestsWarning(payload.warning);
  }

  if ((payload.specs || []).length === 0) {
    testsStatusEl.textContent = "No Testcase elements were found.";
    testsStatusEl.className = "tests-status muted";
    return;
  }

  renderTestResults(payload);
  testsPassed = payload.all_passed === true;
  updateTestsStatus();
  if (loaded[currentIdx]) renderIssues(loaded[currentIdx]);
});

function setTestsWarning(msg) {
  testsStatusEl.textContent = `Warning: ${msg}`;
  testsStatusEl.className = "tests-status warning";
  testsResultsEl.innerHTML = "";
  testsResultsEl.classList.add("empty");
}

function renderTestResults(payload) {
  testsResultsEl.classList.remove("empty");
  const allPassed = payload.all_passed === true;
  testsStatusEl.textContent = allPassed ? "All rows passed." : "Some rows did not pass.";
  testsStatusEl.className = allPassed ? "tests-status passed" : "tests-status failed";

  const html = payload.specs.map((spec) => {
    const rowsHtml = spec.rows.map((row) => {
      const errCell = row.error_message
        ? `<td class="row-err" colspan="2">${escapeHtml(row.error_message)}</td>`
        : `<td class="row-raw">${escapeHtml(row.raw)}</td><td class="row-status">${escapeHtml(row.status)}</td>`;
      return `<tr class="${escapeHtml(row.status)}">
        <td class="row-idx">${row.index}</td>
        ${errCell}
      </tr>`;
    }).join("");
    return `
      <div class="spec-title">${escapeHtml(spec.name)} &middot; ${spec.rows.length} row${spec.rows.length === 1 ? "" : "s"}</div>
      <table>${rowsHtml}</table>
    `;
  }).join("");
  testsResultsEl.innerHTML = html;
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
  } else {
    jarModalMsg.textContent =
      `Browse unavailable (${info.reason || "no reason"}). Paste the path into the field above instead.`;
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