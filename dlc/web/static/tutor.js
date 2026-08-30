  /*# ───────────────────────────────────────────────────────────────────
  *# First-run guided tour.
  *# Auto-shows once per install (marker file in the app folder, so a fresh
  *# zip download shows it again). The ? button in the top bar replays it.
  *# The tour blocks page clicks while open
  *# ──────────────────────────────────────────────────────────────────#*/

(function () {
  "use strict";

  const STEPS = [
    {
      title: "Welcome to Digital Lab Coach",
      html: "Your Digital coach: <b>instant local grading</b>, clear " +
        "explanations, and AI help that is <b>machine-verified before " +
        "shown</b>.<br/>This tour takes about 90 seconds, " +
        "reopen it anytime with the <b>?</b> button up top.",
      target: null,
    },
    {
      title: "Connect to your course",
      html: "Open <b>Settings ⚙ → Course server</b>. Paste the " +
        "<b>URL + course token</b> from your instructor and press " +
        "<b>Save &amp; verify</b> until you see <b>accepted ✓</b>. " +
        "That is your whole AI setup: <b>no API key needed</b>.<br/>" +
        "Settings also holds Digital.jar and the official tests, you " +
        "need to locate your <b>Digital.jar</b> once to get DLC working",
      target: "#settings-gear",
    },
    {
      title: "The real grader",
      html: "This chip shows <b>Digital.jar</b> - the same grader Digital " +
        "uses, so DLC's verdict always matches your grade. " +
        "If it says missing, set the path once in Settings.",
      target: "#jar-chip",
    },
    {
      title: "Load your lab",
      html: "Drag in your <b>whole lab</b> - every .dig file at once, " +
        "subcircuits included. Or try one right now:",
      target: 'label[for="file-input"]',
      demoBtn: true,
    },
    {
      title: "Tests, rows, signal flow",
      html: "<b>Run tests</b> grades every official test row in " +
        "seconds, offline. A <b>red row</b> is one failing case, " +
        "<b>click it</b> and the signal path involved lights up on the " +
        "circuit. Run this with per-row result before using any AI feature.",
      target: "#run-tests-btn",
    },
    {
      title: "Layer-1 cards & official tests",
      html: "These cards are instant findings: <b>red</b> = structural " +
        "bugs (wiring, bit widths etc.), <b>purple</b> = advisories (unused " +
        "pins which is sometimes fine). And if your file's testcase drifts " +
        "from the official one, DLC <b>injects the official test</b>, " +
        "so that you always grade against the course standard.",
      target: "#issues-list",
    },
    {
      title: "Layer-2 understand your circuit (≈ 30 s - 1 min)",
      html: "Three kinds of cards live here:<br/>• <b>Library cards</b> " +
        "- every component <i>your</i> circuit uses, explained.<br/>" +
        "• <b>Big-picture summary</b> - narrates your signal flow. " +
        "<br/>• <b>Summary grade</b> - scores how believable " +
        "that summary is against your circuit's real facts, out of 100.",
      target: '[data-tab="l2"]',
    },
    {
      title: "Layer-3 the AI coach (≈ 30 s – 3 min)",
      html: "<b>Analyze failing rows</b> (Mode A) hypothesizes your " +
        "bug, tests the fix on a copy, and shows it <b>only if the " +
        "official tests pass</b>, 1 run/day.<br/> If too much fails at once you get " +
        "the <b>wholesale-failure check</b> instead: design-level " +
        "advice, and it costs you <b>no run</b>.<br/>" +
        "                                                              " +
        "<b>Coverage coach</b> (Mode B) checks your tests and proposes " +
        "verified new rows. If your test coverage is good enough, mode B" +
        "won't run. Use it if you are interested in understanding tests" +
        "</b>, 2 run/day.<br/>",
      target: '[data-tab="l3"]',
    },
    {
      title: "Extra practice — and you're set",
      html: "The <b>K-map</b> tab is a practice page for kmaps.<br/> That's it. The " +
        "<b>?</b> button replays it anytime. Enjoy 311! ✓",
      target: '[data-tab="kmap"]',
    },
  ];

  let idx = 0;
  let open = false;
  let root = null;

  function build() {
    root = document.createElement("div");
    root.id = "tutor-overlay";
    root.innerHTML =
      '<div id="tutor-spot"></div>' +
      '<div id="tutor-card">' +
      '  <button id="tutor-x" title="Exit the tour">✕</button>' +
      '  <div id="tutor-dots"></div>' +
      '  <h3 id="tutor-title"></h3>' +
      '  <div id="tutor-body"></div>' +
      '  <div id="tutor-demo-row" class="hidden">' +
      '    <button id="tutor-demo-btn">Load a demo circuit ▸</button>' +
      '    <span id="tutor-demo-msg"></span>' +
      "  </div>" +
      '  <div id="tutor-nav">' +
      '    <button id="tutor-back" class="btn-ghost">Back</button>' +
      '    <span id="tutor-count"></span>' +
      '    <button id="tutor-next" class="btn">Next</button>' +
      "  </div>" +
      "</div>";
    document.body.appendChild(root);
    root.querySelector("#tutor-x").addEventListener("click", close);
    root.querySelector("#tutor-back").addEventListener("click", function () {
      if (idx > 0) { idx -= 1; render(); }
    });
    root.querySelector("#tutor-next").addEventListener("click", function () {
      if (idx >= STEPS.length - 1) { close(); return; }
      idx += 1; render();
    });
    root.querySelector("#tutor-demo-btn").addEventListener("click", loadDemo);
    window.addEventListener("resize", onMove);
    window.addEventListener("scroll", onMove, true);
  }

  function onMove() { if (open) place(); }

  function close() {
    open = false;
    if (root) { root.remove(); root = null; }
    window.removeEventListener("resize", onMove);
    window.removeEventListener("scroll", onMove, true);
  }

  function start(at) {
    if (open) return;
    idx = at || 0;
    build();
    open = true;
    render();
  }

  function render() {
    const step = STEPS[idx];
    root.querySelector("#tutor-title").textContent = step.title;
    root.querySelector("#tutor-body").innerHTML = step.html;
    root.querySelector("#tutor-demo-row")
        .classList.toggle("hidden", !step.demoBtn);
    root.querySelector("#tutor-demo-msg").textContent = "";
    root.querySelector("#tutor-back").disabled = idx === 0;
    root.querySelector("#tutor-next").textContent =
      idx >= STEPS.length - 1 ? "Finish ✓" : "Next";
    root.querySelector("#tutor-count").textContent =
      (idx + 1) + " / " + STEPS.length;
    const dots = STEPS.map(function (_, i) {
      return '<span class="tutor-dot' + (i === idx ? " on" : "") + '"></span>';
    }).join("");
    root.querySelector("#tutor-dots").innerHTML = dots;
    place();
  }

  function place() {
    const step = STEPS[idx];
    const spot = root.querySelector("#tutor-spot");
    const card = root.querySelector("#tutor-card");
    const el = step.target ? document.querySelector(step.target) : null;
    if (!el) {
      spot.style.display = "none";
      root.classList.add("tutor-dim");
      card.style.left = "50%";
      card.style.top = "38%";
      card.style.transform = "translate(-50%, -50%)";
      return;
    }
    root.classList.remove("tutor-dim");
    const r = el.getBoundingClientRect();
    const pad = 6;
    spot.style.display = "block";
    spot.style.left = (r.left - pad) + "px";
    spot.style.top = (r.top - pad) + "px";
    spot.style.width = (r.width + 2 * pad) + "px";
    spot.style.height = (r.height + 2 * pad) + "px";
    card.style.transform = "none";
    const cw = 400;
    let left = Math.min(Math.max(12, r.left), window.innerWidth - cw - 12);
    card.style.left = left + "px";
    const below = r.bottom + 14;
    if (below + 260 < window.innerHeight) {
      card.style.top = below + "px";
    } else {
      card.style.top = Math.max(12, r.top - 14 - card.offsetHeight) + "px";
    }
  }

  async function loadDemo() {
    const msg = root.querySelector("#tutor-demo-msg");
    msg.textContent = "loading…";
    try {
      const res = await fetch("/api/tutorial/demo");
      const d = await res.json();
      if (!d.ok) { msg.textContent = d.error || "demo unavailable"; return; }
      const file = new File([d.content], d.filename, { type: "text/xml" });
      if (typeof fileObjects !== "undefined" && typeof postAll === "function") {
        fileObjects = fileObjects.filter(function (f) {
          return f.name !== d.filename;
        });
        fileObjects.push(file);
        await postAll();
        msg.textContent = "loaded ✓";
        setTimeout(function () { idx += 1; render(); }, 700);
      } else {
        msg.textContent = "upload not ready — drag the file in instead";
      }
    } catch (err) {
      msg.textContent = "could not load demo (" + err + ")";
    }
  }

  /* ---- the ? help popup ---- */
  function openHelp() {
    if (document.getElementById("tutor-help-pop")) return;
    const pop = document.createElement("div");
    pop.id = "tutor-help-pop";
    pop.innerHTML =
      '<div id="tutor-help-card">' +
      '  <button id="tutor-help-x" title="Close">✕</button>' +
      "  <h3>Need a refresher?</h3>" +
      "  <p>Replay tour.</p>" +
      '  <button id="tutor-help-go" class="btn">▶ Show the tour</button>' +
      "</div>";
    document.body.appendChild(pop);
    pop.querySelector("#tutor-help-x").addEventListener("click", function () {
      pop.remove();
    });
    pop.querySelector("#tutor-help-go").addEventListener("click", function () {
      pop.remove();
      start(0);
    });
  }

  /* ---- boot ---- */
  document.addEventListener("DOMContentLoaded", function () {
    const btn = document.getElementById("tutor-help-btn");
    if (btn) btn.addEventListener("click", openHelp);
    fetch("/api/tutorial/state")
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.seen === false) {
          fetch("/api/tutorial/seen", { method: "POST" }).catch(function () {});
          setTimeout(function () { start(0); }, 700);
        }
      })
      .catch(function () {});
  });
})();
