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
  list.innerHTML = tests.map((t) => {
    const src = t.source || "user";
    const chip = src === "default"
      ? `<span class="ot-src ot-src-default">built-in default</span>`
      : (src === "override"
        ? `<span class="ot-src ot-src-override">overrides a default</span>`
        : "");
    let editor;
    if (src === "user") {
      editor = `<textarea class="text-input ot-textarea" data-ot-edit="${escapeHtml(t.filename)}">${escapeHtml(t.content)}</textarea>
      <div class="settings-row">
        <button class="btn-ghost" data-ot-save="${escapeHtml(t.filename)}">Save changes</button>
        <button class="btn-ghost ot-delete" data-ot-del="${escapeHtml(t.filename)}">Delete</button>
      </div>`;
    } else if (src === "override") {
      editor = `<textarea class="text-input ot-textarea" readonly>${escapeHtml(t.content)}</textarea>
      <div class="settings-row">
        <button class="btn-ghost ot-delete" data-ot-del="${escapeHtml(t.filename)}">Delete override (revert to default)</button>
      </div>`;
    } else {
      editor = `<textarea class="text-input ot-textarea" readonly>${escapeHtml(t.content)}</textarea>`;
    }
    return `
    <details class="ot-item" data-ot="${escapeHtml(t.filename)}">
      <summary class="ot-bar">
        <span class="ot-name">${escapeHtml(t.filename)}</span>
        <span class="ot-sha">fingerprint ${escapeHtml((t.sha1 || "").slice(0, 10))}</span>
        ${chip}
        <span class="ot-open muted">${src === "user" ? "view / edit" : "view"}</span>
      </summary>
      ${editor}
    </details>`;
  }).join("");
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
  // settings floats OVER the current page (blurred behind); only the
  // X closes it — no backdrop click, no Escape.
  const gear = document.getElementById("settings-gear");
  const overlay = document.getElementById("settings-overlay");
  if (gear && overlay) {
    gear.addEventListener("click", () => {
      overlay.classList.remove("hidden");
      renderSettings();
      logEvent("settings_opened", {});
    });
  }
  const closeBtn = document.getElementById("settings-close");
  if (closeBtn && overlay) {
    closeBtn.addEventListener("click", () => overlay.classList.add("hidden"));
  }
})();

/* --- In-overlay manifest guide -------------------------------------------
   The guide renders INSIDE the settings panel (fetch the markdown, run it
   through the dependency-free mini renderer below, swap the views). No
   navigation, no new tab — the packaged build never leaves the app window;
   "Back to settings" restores the sections. */

function mdLiteInline(s) {
  let out = escapeHtml(s);
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  out = out.replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
  out = out.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_m, t, u) =>
    /^https?:/i.test(u)
      ? `<a href="${u}" target="_blank" rel="noopener">${t}</a>`
      : `<a href="${u}">${t}</a>`);
  return out;
}

function mdLiteRender(md) {
  const lines = md.split(/\r?\n/);
  const out = [];
  let i = 0;
  const isTableSep = (s) => /^\|[\s\-:|]+\|\s*$/.test(s);
  while (i < lines.length) {
    const line = lines[i];
    if (/^```/.test(line)) {                      // fenced code
      const buf = [];
      i += 1;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i += 1;                                     // closing fence
      out.push(`<pre><code>${escapeHtml(buf.join("\n"))}</code></pre>`);
      continue;
    }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) {
      const lvl = h[1].length;
      out.push(`<h${lvl}>${mdLiteInline(h[2])}</h${lvl}>`);
      i += 1;
      continue;
    }
    if (/^\|/.test(line) && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const cells = (s) => s.replace(/^\||\|\s*$/g, "").split("|")
        .map((c) => mdLiteInline(c.trim()));
      const head = cells(line);
      i += 2;
      const rows = [];
      while (i < lines.length && /^\|/.test(lines[i])) rows.push(cells(lines[i++]));
      out.push("<table><thead><tr>"
        + head.map((c) => `<th>${c}</th>`).join("")
        + "</tr></thead><tbody>"
        + rows.map((r) => "<tr>" + r.map((c) => `<td>${c}</td>`).join("") + "</tr>").join("")
        + "</tbody></table>");
      continue;
    }
    const list = line.match(/^(\s*)([-*]|\d+\.)\s+/);
    if (list) {
      const ordered = /\d/.test(list[2]);
      const tag = ordered ? "ol" : "ul";
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(/^(\s*)([-*]|\d+\.)\s+(.*)$/);
        if (!m || /\d/.test(m[2]) !== ordered) break;
        let item = m[3];
        // hanging continuation lines belong to the same bullet
        while (i + 1 < lines.length && /^\s{2,}\S/.test(lines[i + 1])
               && !lines[i + 1].match(/^(\s*)([-*]|\d+\.)\s+/)) {
          item += " " + lines[++i].trim();
        }
        items.push(`<li>${mdLiteInline(item)}</li>`);
        i += 1;
      }
      out.push(`<${tag}>${items.join("")}</${tag}>`);
      continue;
    }
    if (/^>\s?/.test(line)) {
      const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        buf.push(lines[i++].replace(/^>\s?/, ""));
      }
      out.push(`<blockquote>${mdLiteInline(buf.join(" "))}</blockquote>`);
      continue;
    }
    if (!line.trim()) { i += 1; continue; }
    const buf = [line];                            // paragraph: join soft wraps
    i += 1;
    while (i < lines.length && lines[i].trim()
           && !/^(#{1,4}\s|```|\||>\s?|(\s*)([-*]|\d+\.)\s)/.test(lines[i])) {
      buf.push(lines[i++]);
    }
    out.push(`<p>${mdLiteInline(buf.join(" "))}</p>`);
  }
  return out.join("\n");
}

(function wireGuideView() {
  const link = document.getElementById("set-guide-link");
  const view = document.getElementById("set-guide-view");
  const doc = document.getElementById("set-guide-doc");
  const back = document.getElementById("set-guide-back");
  const overlay = document.getElementById("settings-overlay");
  const mainWrap = overlay
    ? overlay.querySelector(".settings-panel > .settings-wrap:not(#set-guide-view)")
    : null;
  if (!link || !view || !doc || !back || !mainWrap) return;

  link.addEventListener("click", async (evt) => {
    evt.preventDefault();
    mainWrap.classList.add("hidden");
    view.classList.remove("hidden");
    logEvent("settings_guide_opened", {});
    if (!doc.dataset.loaded) {
      try {
        const r = await fetch("/api/docs/manifest_guide?raw=1");
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        doc.innerHTML = mdLiteRender(await r.text());
        doc.classList.remove("muted");
        doc.dataset.loaded = "1";
      } catch (err) {
        doc.textContent = `Could not load the guide: ${err}`;
      }
    }
  });
  back.addEventListener("click", () => {
    view.classList.add("hidden");
    mainWrap.classList.remove("hidden");
  });
})();
