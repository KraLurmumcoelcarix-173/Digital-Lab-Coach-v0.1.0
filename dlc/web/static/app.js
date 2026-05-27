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
];

const fileInput   = document.getElementById("file-input");
const fileSelect  = document.getElementById("file-select");
const prevBtn     = document.getElementById("prev-btn");
const nextBtn     = document.getElementById("next-btn");
const placeholder = document.getElementById("placeholder");
const summaryEl   = document.getElementById("summary");
const popupEl     = document.getElementById("hover-popup");
const popupTitle  = document.getElementById("hover-popup-title");
const popupBody   = document.getElementById("hover-popup-body");

let fileObjects = [];
let loaded      = [];
let currentIdx  = 0;
let cy          = null;

fileInput.addEventListener("change", async () => {
  if (!fileInput.files || fileInput.files.length === 0) return;
  for (const f of fileInput.files) {
    fileObjects = fileObjects.filter((existing) => existing.name !== f.name);
    fileObjects.push(f);
  }
  fileInput.value = "";
  await postAll();
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

  renderCurrent();
}

function renderCurrent() {
  if (loaded.length === 0) return;
  const f = loaded[currentIdx];
  fileSelect.value = String(currentIdx);

  if (f.error) {
    placeholder.classList.remove("hidden");
    placeholder.innerHTML = `<span style="color:#991b1b">${escapeHtml(f.filename)}: ${escapeHtml(f.error)}</span>`;
    if (cy) { cy.destroy(); cy = null; }
    summaryEl.innerHTML = `<span style="color:#991b1b">Parse error.</span>`;
    return;
  }

  renderGraph(f.graph);
  renderSummary(f.summary);
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
}

function showNodePopup(node) {
  const d = node.data();
  const title = d.comp_label ? `${d.element_name} - ${d.comp_label}` : d.element_name;
  popupTitle.textContent = title;

  const attrRows = Object.entries(d.attributes || {})
    .filter(([k]) => k !== "Label")
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
      <tr><td class="k">from</td><td class="v">${escapeHtml(sourceLabel)} [${escapeHtml(d.source)}] . ${escapeHtml(d.driver_pin || "?")}</td></tr>
      <tr><td class="k">to</td><td class="v">${escapeHtml(targetLabel)} [${escapeHtml(d.target)}] . ${escapeHtml(d.sink_pin || "?")}</td></tr>
    </table>
  `;
  popupEl.classList.remove("hidden");
}

function hidePopup() {
  popupEl.classList.add("hidden");
}

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

function renderSummary(s) {
  const stats = s.net_stats || {};
  const undrivenBadge = stats.undriven_with_pins
    ? `<span class="badge warn">${stats.undriven_with_pins} undriven</span>`
    : "";
  const multiBadge = stats.multi_driver
    ? `<span class="badge err">${stats.multi_driver} multi-driver</span>`
    : "";

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
      <tr><td class="k">issues</td><td class="v">${undrivenBadge}${multiBadge}${(!undrivenBadge && !multiBadge) ? '<span class="muted">none</span>' : ""}</td></tr>
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