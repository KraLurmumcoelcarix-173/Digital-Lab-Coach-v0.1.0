/* Settings page (gear tab). Loaded after app.js: reuses its globals
   (escapeHtml, logEvent) and the existing jar/key MODALS — the settings
   sections are the hub; Change/Configure buttons open the same dialogs the
   toolbar chips do. Official tests are the new machinery: local CRUD over
   /api/config/official_tests, matched by Mode B as instructor truth. */

async function renderSettings() {
  // Digital.jar summary (same endpoint the chip uses)
  try {
    const r = await fetch("/api/config/jar");
    const b = await r.json();
    const el = document.getElementById("set-jar-path");
    if (el) {
      el.textContent = b.path || "not configured";
      el.classList.toggle("settings-bad", !b.path);
    }
  } catch {}
  // API-key status (write-only: configured yes/no per provider)
  try {
    const r = await fetch("/api/config/api_key");
    const b = await r.json();
    const el = document.getElementById("set-key-state");
    if (el) {
      const p = b.providers || {};
      el.textContent = Object.keys(p).length
        ? Object.entries(p).map(([k, v]) => `${k}: ${v ? "set ✓" : "not set"}`).join("   ")
        : (b.configured ? "configured ✓" : "not configured");
    }
  } catch {}
  await renderOfficialTests();
}

async function renderOfficialTests() {
  const list = document.getElementById("ot-list");
  if (!list) return;
  let body = null;
  try {
    const r = await fetch("/api/config/official_tests");
    body = await r.json();
  } catch {}
  const tests = (body && body.tests) || [];
  if (!tests.length) {
    list.innerHTML = `<span class="muted">No official tests registered yet.</span>`;
    return;
  }
  list.classList.remove("muted");
  list.innerHTML = tests.map((t) => `
    <details class="ot-item" data-ot="${escapeHtml(t.filename)}">
      <summary class="ot-bar">
        <span class="ot-name">${escapeHtml(t.filename)}</span>
        <span class="ot-sha">fingerprint ${escapeHtml((t.sha1 || "").slice(0, 10))}</span>
        <span class="ot-open muted">view / edit</span>
      </summary>
      <textarea class="text-input ot-textarea" data-ot-edit="${escapeHtml(t.filename)}">${escapeHtml(t.content)}</textarea>
      <div class="settings-row">
        <button class="btn-ghost" data-ot-save="${escapeHtml(t.filename)}">Save changes</button>
        <button class="btn-ghost ot-delete" data-ot-del="${escapeHtml(t.filename)}">Delete</button>
      </div>
    </details>`).join("");
}

function otMsg(text, bad) {
  const el = document.getElementById("ot-msg");
  if (!el) return;
  el.textContent = text;
  el.style.color = bad ? "#991b1b" : "#166534";
  setTimeout(() => { if (el.textContent === text) el.textContent = ""; }, 4000);
}

async function otSave(filename, content) {
  try {
    const r = await fetch("/api/config/official_tests", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ filename, content }),
    });
    const b = await r.json();
    if (!r.ok) { otMsg(b.detail || "Save failed.", true); return false; }
    otMsg(`Saved '${filename}' ✓`);
    logEvent("settings_official_test_saved", { filename });
    return true;
  } catch (err) {
    otMsg(`Network error: ${err}`, true);
    return false;
  }
}

(function wireSettings() {
  const addBtn = document.getElementById("ot-add-btn");
  if (addBtn) {
    addBtn.addEventListener("click", async () => {
      const name = (document.getElementById("ot-filename").value || "").trim();
      const content = document.getElementById("ot-content").value || "";
      if (!name) { otMsg("Filename is required.", true); return; }
      if (!content.trim()) { otMsg("Testcase content is required.", true); return; }
      if (await otSave(name, content)) {
        document.getElementById("ot-filename").value = "";
        document.getElementById("ot-content").value = "";
        renderOfficialTests();
      }
    });
  }
  const list = document.getElementById("ot-list");
  if (list) {
    list.addEventListener("click", async (evt) => {
      const save = evt.target.closest("[data-ot-save]");
      if (save) {
        const name = save.dataset.otSave;
        const ta = list.querySelector(`textarea[data-ot-edit="${CSS.escape(name)}"]`);
        if (ta && await otSave(name, ta.value)) renderOfficialTests();
        return;
      }
      const del = evt.target.closest("[data-ot-del]");
      if (del) {
        const name = del.dataset.otDel;
        if (!confirm(`Delete the official test for '${name}'?`)) return;
        try {
          await fetch(`/api/config/official_tests?filename=${encodeURIComponent(name)}`,
                      { method: "DELETE" });
          logEvent("settings_official_test_deleted", { filename: name });
        } catch {}
        renderOfficialTests();
      }
    });
  }
  // the jar/key sections reuse the existing modals via their toolbar chips
  const jarBtn = document.getElementById("set-jar-btn");
  if (jarBtn) jarBtn.addEventListener("click", () => {
    const chip = document.getElementById("jar-chip");
    if (chip) chip.click();
  });
  const keyBtn = document.getElementById("set-key-btn");
  if (keyBtn) keyBtn.addEventListener("click", () => {
    const chip = document.getElementById("key-chip");
    if (chip) chip.click();
  });
})();
