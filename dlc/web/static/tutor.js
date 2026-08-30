  /*# ───────────────────────────────────────────────────────────────────
  *# First-run guided tour.
  *# Auto-shows once per install (marker file in the app folder, so a fresh
  *# zip download shows it again). The ? button in the top bar replays it.
  *# The tour blocks page clicks while open
  *# Live detours: configure Digital.jar, run the demo's per-row tests,
  *# load a structural-bug circuit. L2/L3 show hard-coded SAMPLE pages
  *# that mirror the real result panels — the tour never spends an AI call.
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
        "<br/><br/>" +
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
      action: { label: "Configure Digital.jar now ▸", run: "jar" },
    },
    {
      title: "Load your lab",
      html: "Drag in your <b>whole lab</b> - every .dig file at once, " +
        "subcircuits included. Or try one right now:",
      target: 'label[for="file-input"]',
      action: { label: "Load a demo circuit ▸", run: "demo1" },
    },
    {
      title: "Tests, rows, signal flow",
      html: "<b>Run tests</b> grades every official test row in " +
        "seconds, offline. A <b>red row</b> is one failing case, " +
        "<b>click it</b> and the signal path involved lights up on the " +
        "circuit. Run this with per-row result before using any AI feature.",
      target: "#run-tests-btn",
      action: { label: "Run tests ▸", run: "runtests" },
      altTargetWhenDone: "#tests-results",
    },
    {
      title: "Layer-1 cards & official tests",
      html: "These cards are instant findings: <b>red</b> = structural " +
        "bugs (wiring, bit widths etc.), <b>purple</b> = advisories (unused " +
        "pins which is sometimes fine). And if your file's testcase drifts " +
        "from the official one, DLC <b>injects the official test</b>, " +
        "so that you always grade against the course standard.",
      target: "#issues-list",
      action: { label: "Load a bug circuit ▸", run: "demo2" },
    },
    {
      title: "Layer-2 understand your circuit (≈ 30 s - 1 min)",
      html: "Three kinds of cards live here:<br/>• <b>Library cards</b> " +
        "- every component <i>your</i> circuit uses, explained.<br/>" +
        "• <b>Big-picture summary</b> - narrates your signal flow. " +
        "<br/>• <b>Summary grade</b> - scores how believable " +
        "that summary is against your circuit's real facts, out of 100.",
      target: '[data-tab="l2"]',
      action: { label: "Show a sample result ▸", run: "sampleL2" },
    },
    {
      title: "Layer-3 the AI coach (≈ 30 s – 3 min)",
      html: "<b>Analyze failing rows</b> (Mode A) hypothesizes your " +
        "bug, tests the fix on a copy, and shows it <b>only if the " +
        "official tests pass</b>, 1 run/day.<br/>" +
        "<br/><br/>" +
        "If too much fails at once you get " +
        "the <b>wholesale-failure check</b> instead: design-level " +
        "advice, and it costs you <b>no run</b>.<br/>" +
        "<br/><br/>" +
        "<b>Coverage coach</b> (Mode B) checks your tests and proposes " +
        "verified new rows. If your test coverage is good enough, mode B " +
        "won't run. Use it if you are interested in understanding tests" +
        ", 2 run/day.<br/>",
      target: '[data-tab="l3"]',
      action: { label: "Show a sample result ▸", run: "sampleL3" },
    },
    {
      title: "Clear all",
      html: "Done with a lab or want a fresh start? <b>Clear all</b> " +
        "unloads every uploaded file - your originals on disk are " +
        "untouched. Try it:",
      target: "#clear-btn",
      action: { label: "Clear all ▸", run: "clearall" },
    },
    {
      title: "Extra practice — and you're set",
      html: "The <b>K-map</b> tab is a practice page for kmaps.<br/> That's it. The " +
        "<b>?</b> button replays it anytime. Enjoy 311! ✓",
      target: '[data-tab="kmap"]',
    },
  ];

  const SAMPLE_L2_PAGE =
    '<div class="tutor-sample-badge">SAMPLE</div>' +
    '<div class="ts-l2-layout">' +
    '<div class="ts-l2-main">' +
    '<div id="tutor-sample-library" class="ts-block">' +
    "<h2>Component library</h2>" +
    '<p class="ts-muted">Components present in this circuit. Hover a card to preview.</p>' +
    '<div class="library-grid" style="max-height:none;">' +
    '<div class="library-card"><img src="/static/images/components/register.png" alt="Register"/><div class="name">Register</div></div>' +
    '<div class="library-card"><img src="/static/images/components/adder.png" alt="Adder"/><div class="name">Adder</div></div>' +
    '<div class="library-card"><span class="count">30</span><img src="/static/images/components/tunnel.png" alt="Tunnel (named net)"/><div class="name">Tunnel (named net)</div></div>' +
    '<div class="library-card"><span class="count">4</span><img src="/static/images/components/const.png" alt="Constant"/><div class="name">Constant</div></div>' +
    '<div class="library-card"><img src="/static/images/components/clock.png" alt="Clock"/><div class="name">Clock</div></div>' +
    '<div class="library-card"><span class="count">2</span><img src="/static/images/components/out.png" alt="Output port"/><div class="name">Output port</div></div>' +
    '<div class="library-card"><img src="/static/images/components/rom.png" alt="ROM"/><div class="name">ROM</div></div>' +
    '<div class="library-card"><img src="/static/images/components/subcircuit.png" alt="Subcircuit reference"/><div class="name">Subcircuit reference</div></div>' +
    '<div class="library-card"><span class="count">5</span><img src="/static/images/components/splitter.png" alt="Splitter / merger"/><div class="name">Splitter / merger</div></div>' +
    '<div class="library-card"><img src="/static/images/components/bit_extender.png" alt="Bit extender"/><div class="name">Bit extender</div></div>' +
    '<div class="library-card"><img src="/static/images/components/mux.png" alt="Multiplexer (MUX)"/><div class="name">Multiplexer (MUX)</div></div>' +
    "</div></div>" +
    '<div id="tutor-sample-grade" class="ts-block">' +
    "<h2>Summary grade</h2>" +
    '<p class="ts-muted">How believable this summary is, graded against the circuit facts.</p>' +
    '<div class="ts-grade-row">' +
    '<div class="ts-donut"><div class="ts-donut-hole"><b>94</b><span>/ 100</span></div></div>' +
    '<div class="ts-grade-list">' +
    '<div><i style="background:#3b82f6"></i>Function description <em>LLM</em><b>19/20</b></div>' +
    '<div><i style="background:#22c55e"></i>Signal-flow accuracy <em>LLM</em><b>18/20</b></div>' +
    '<div><i style="background:#8b5cf6"></i>Signal-flow completeness <em>HYBRID</em><b>14/15</b></div>' +
    '<div><i style="background:#f59e0b"></i>Goal comparison <em>LLM</em><b>15/15</b></div>' +
    '<div><i style="background:#ec4899"></i>Key-component mention <em>DETERMINISTIC</em><b>8/10</b></div>' +
    '<div><i style="background:#14b8a6"></i>Topology accuracy <em>LLM</em><b>10/10</b></div>' +
    '<div><i style="background:#f97316"></i>Lecture-tag relevance <em>LLM</em><b>10/10</b></div>' +
    "</div></div></div></div>" +
    '<div class="ts-l2-side">' +
    '<div id="tutor-sample-summary" class="ts-block">' +
    '<h2>Big-picture coach <span class="ts-pill">LLM</span></h2>' +
    '<div class="ts-acc ts-acc-blue">' +
    '<div class="ts-acc-head"><span class="ts-acc-num">1</span>' +
    "<b>Overall purpose</b> <span class='ts-muted'><i>What this circuit does.</i></span></div>" +
    '<div class="ts-acc-body">Your circuit implements a single-cycle ' +
    "RISC-V processor that produces ReadData1 and ReadData2 as its " +
    "32-bit top-level outputs. The main components driving this " +
    "behavior are the ROM labeled Instruction Memory, a Register " +
    "acting as the program counter, and a Multiplexer that selects " +
    "between a register operand and a sign-extended immediate.</div>" +
    "</div>" +
    '<div class="ts-acc ts-acc-purple">' +
    '<div class="ts-acc-head"><span class="ts-acc-num">2</span>' +
    "<b>Subcircuits</b> <span class='ts-muted'><i>Role of each child " +
    ".dig.</i></span><span class='ts-acc-plus'>+</span></div>" +
    "</div>" +
    '<div class="ts-acc ts-acc-green">' +
    '<div class="ts-acc-head"><span class="ts-acc-num">3</span>' +
    "<b>Signal flow</b> <span class='ts-muted'><i>Input → output " +
    "paths.</i></span><span class='ts-acc-plus'>+</span></div>" +
    "</div>" +
    '<div class="ts-acc ts-acc-amber">' +
    '<div class="ts-acc-head"><span class="ts-acc-num">4</span>' +
    "<b>Topology</b> <span class='ts-muted'><i>Fan-in / fan-out and " +
    "layout hot spots.</i></span><span class='ts-acc-plus'>+</span></div>" +
    "</div>" +
    '<div class="ts-acc ts-acc-pink">' +
    '<div class="ts-acc-head"><span class="ts-acc-num">5</span>' +
    "<b>Course concepts</b> <span class='ts-muted'><i>Lecture-tag " +
    "mapping.</i></span><span class='ts-acc-plus'>+</span></div>" +
    "</div>" +
    "</div></div></div>";

  const SAMPLE_L3_PAGE =
    '<div class="tutor-sample-badge">SAMPLE</div>' +
    '<div class="ts-l3-layout">' +
    '<div id="tutor-sample-fixcard" class="ts-block ts-l3-panel">' +
    '<div class="ts-l3-head"><h2>Failed-test analysis</h2>' +
    '<span class="ts-mode-pill">MODE A</span></div>' +
    '<p class="ts-verified-line">1 verified hypothesis card — every fix ' +
    "below passed the full re-run before you saw it.</p>" +
    '<div class="ts-rowbanner">Row(s) 10 fail on Bit0, Result, Zero ' +
    "when Op=3.</div>" +
    '<div class="ts-hypo">' +
    '<div class="ts-chiprow">' +
    '<span class="ts-chip ts-chip-green">hypothesis #1</span>' +
    '<span class="ts-chip ts-chip-green">verified fix ✓</span>' +
    '<span class="ts-chip">confidence 88%</span>' +
    '<span class="ts-chip">row 10</span>' +
    "</div>" +
    "<p><b>Where to look:</b> Main result Multiplexer (Op selector) — " +
    "the in3 data input</p>" +
    '<div class="ts-chiprow">' +
    '<span class="ts-chip ts-chip-mono">Multiplexer[14].in3</span>' +
    '<span class="ts-chip ts-chip-mono">Multiplexer[14].out</span>' +
    '<span class="ts-chip ts-chip-mono">Op</span>' +
    '<span class="ts-chip ts-chip-mono">Ground[23].out</span>' +
    "</div>" +
    '<p class="ts-muted">When Op=3 (binary 11), the multiplexer selects ' +
    "its in3 input which is tied to Ground (constant 0), producing " +
    "Result=0 even though bool_unit is correctly computing 0xF on " +
    "<span class='ts-netlink'>net 4</span>.</p>" +
    '<div id="tutor-sample-opbox" class="ts-opbox">rewire [14] ' +
    "Multiplexer.in3 ← [9] bool_unit.dig.Result</div>" +
    "</div>" +
    '<button class="ts-ghost-btn" disabled>Analyze failing rows</button>' +
    "</div>" +
    '<div class="ts-l3-sidecol">' +
    '<div id="tutor-sample-modeb" class="ts-block ts-l3-panel">' +
    '<div class="ts-l3-head"><h2>Coverage coach</h2>' +
    '<span class="ts-mode-pill ts-mode-pill-b">MODE B</span></div>' +
    '<p class="ts-verified-line">2 proposed rows - each replayed and ' +
    "verified on a temp copy before display.</p>" +
    '<div class="ts-hypo">' +
    "<p><b>Gap:</b> your rows never exercise the carry-overflow case " +
    "(A=15, B=1).</p>" +
    '<div class="ts-opbox">15 1 C 0&nbsp;&nbsp;&nbsp;# proposed - ' +
    "verified ✓<br/>15 15 C 14&nbsp;# proposed - verified ✓</div>" +
    '<p class="ts-muted">One click adopts verified rows into your ' +
    "testcase. Writing good tests is the real lesson.</p>" +
    "</div></div></div></div>";

  const SAMPLES = {
    sampleL2: {
      page: SAMPLE_L2_PAGE,
      returnTo: 7,
      steps: [
        { title: "The Big-picture coach",
          html: "Numbered sections narrate <i>your</i> circuit - " +
            "purpose, subcircuit roles, signal flow. ",
          target: "#tutor-sample-summary" },
        { title: "Library cards",
          html: "Every component your circuit actually uses. ",
          target: "#tutor-sample-library" },
        { title: "The Summary grade",
          html: "The summary is then <b>graded for believability</b> " +
            "against your circuit's real facts. ",
          target: "#tutor-sample-grade" },
      ],
    },
    sampleL3: {
      page: SAMPLE_L3_PAGE,
      returnTo: 8,
      steps: [
        { title: "A verified hypothesis card",
          html: "Cards tell you the state at a glance: hypothesis, " +
            "<b>verified fix ✓</b>, confidence, the failing row. " +
            "Below: where to look, the pins involved, and why the " +
            "failure happens.",
          target: "#tutor-sample-fixcard" },
        { title: "The exact verified edit",
          html: "The green box is the fix as one operation. Mode A " +
            "applied it to a temp copy and re-ran the official tests " +
            "first - <b>unverified fixes are never shown</b>. You make " +
            "the edit yourself on your schematic.",
          target: "#tutor-sample-opbox" },
        { title: "Mode B coverage proposals",
          html: "Mode B finds what your tests never exercise and " +
            "proposes rows, each replayed and verified before you see " +
            "it. Adopt them with one click.",
          target: "#tutor-sample-modeb" },
      ],
    },
  };

  let idx = 0;
  let open = false;
  let root = null;
  let detour = null;
  let sample = null;
  let samplePage = null;
  let testsDone = false;
  let demo2Done = false;

  function $(sel) { return document.querySelector(sel); }

  function demo1Loaded() {
    try {
      return typeof loaded !== "undefined" &&
        loaded.some(function (f) { return f.filename === "tutor_demo.dig"; });
    } catch (e) { return false; }
  }

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
      '  <div id="tutor-act-row" class="hidden">' +
      '    <button id="tutor-act-btn"></button>' +
      '    <span id="tutor-act-msg"></span>' +
      "  </div>" +
      '  <div id="tutor-nav">' +
      '    <button id="tutor-back" class="btn-ghost">Back</button>' +
      '    <span id="tutor-count"></span>' +
      '    <button id="tutor-next" class="btn">Next</button>' +
      "  </div>" +
      "</div>";
    document.body.appendChild(root);
    root.querySelector("#tutor-x").addEventListener("click", exitTour);
    root.querySelector("#tutor-back").addEventListener("click", function () {
      if (sample || detour) return;
      if (idx > 0) { idx -= 1; render(); }
    });
    root.querySelector("#tutor-next").addEventListener("click", onNext);
    root.querySelector("#tutor-act-btn").addEventListener("click", onAction);
    window.addEventListener("resize", onMove);
    window.addEventListener("scroll", onMove, true);
  }

  function onMove() { if (open && !detour) place(); }

  function exitTour() {
    if (sample) return;               // sample mode: Next is the only exit
    open = false;
    detour = null;
    removeSamplePage();
    if (root) { root.remove(); root = null; }
    window.removeEventListener("resize", onMove);
    window.removeEventListener("scroll", onMove, true);
  }

  function removeSamplePage() {
    if (samplePage) { samplePage.remove(); samplePage = null; }
    sample = null;
    if (root) root.classList.remove("tutor-noexit");
  }

  function start(at) {
    if (open) return;
    idx = at || 0;
    testsDone = false;
    demo2Done = false;
    build();
    open = true;
    render();
  }

  function onNext() {
    if (detour) return;
    if (sample) {
      const s = SAMPLES[sample.key];
      if (sample.i >= s.steps.length - 1) {
        const back = s.returnTo;
        removeSamplePage();
        idx = back;
        render();
      } else {
        sample.i += 1;
        render();
      }
      return;
    }
    if (idx >= STEPS.length - 1) { exitTour(); return; }
    idx += 1;
    render();
  }

  function currentStep() {
    if (sample) return SAMPLES[sample.key].steps[sample.i];
    return STEPS[idx];
  }

  function render() {
    const step = currentStep();
    root.querySelector("#tutor-title").textContent = step.title;
    root.querySelector("#tutor-body").innerHTML = step.html;
    root.querySelector("#tutor-act-msg").textContent = "";
    root.classList.toggle("tutor-noexit", !!sample);
    root.classList.toggle("tutor-passthrough",
      !sample && !detour && !!(step.altTargetWhenDone && testsDone));
    root.querySelector("#tutor-back").style.display = sample ? "none" : "";

    const actRow = root.querySelector("#tutor-act-row");
    const act = !sample && step.action ? step.action : null;
    let showAct = !!act;
    if (act && act.run === "runtests") {
      showAct = demo1Loaded() && !testsDone &&
        $("#run-tests-btn") && !$("#run-tests-btn").disabled;
    }
    if (act && act.run === "demo2" && demo2Done) showAct = false;
    actRow.classList.toggle("hidden", !showAct);
    if (showAct) root.querySelector("#tutor-act-btn").textContent = act.label;

    if (sample) {
      const n = SAMPLES[sample.key].steps.length;
      root.querySelector("#tutor-count").textContent = "";
      root.querySelector("#tutor-dots").innerHTML = "";
      root.querySelector("#tutor-next").textContent =
        sample.i >= n - 1 ? "Back to the tour ▸" : "Next";
    } else {
      root.querySelector("#tutor-back").disabled = idx === 0;
      root.querySelector("#tutor-next").textContent =
        idx >= STEPS.length - 1 ? "Finish ✓" : "Next";
      root.querySelector("#tutor-count").textContent =
        (idx + 1) + " / " + STEPS.length;
      root.querySelector("#tutor-dots").innerHTML =
        STEPS.map(function (_, i) {
          return '<span class="tutor-dot' + (i === idx ? " on" : "") +
            '"></span>';
        }).join("");
    }
    place();
  }

  function place() {
    const step = currentStep();
    const spot = root.querySelector("#tutor-spot");
    const card = root.querySelector("#tutor-card");
    let sel = step.target;
    if (!sample && step.altTargetWhenDone && testsDone) {
      sel = step.altTargetWhenDone;
    }
    const el = sel ? document.querySelector(sel) : null;
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
    const left = Math.min(Math.max(12, r.left), window.innerWidth - cw - 12);
    card.style.left = left + "px";
    const below = r.bottom + 14;
    if (below + 280 < window.innerHeight) {
      card.style.top = below + "px";
    } else {
      card.style.top = Math.max(12, r.top - 14 - card.offsetHeight) + "px";
    }
  }

  /* ---- actions ---- */

  function onAction() {
    if (detour || sample) return;
    const act = STEPS[idx].action;
    if (!act) return;
    if (act.run === "demo1") loadDemoFile(1, "tutor_demo.dig", true);
    else if (act.run === "demo2") loadDemoFile(2, "tutor_demo2.dig", false);
    else if (act.run === "jar") jarDetour();
    else if (act.run === "runtests") runTestsDetour();
    else if (act.run === "sampleL2") enterSample("sampleL2");
    else if (act.run === "sampleL3") enterSample("sampleL3");
    else if (act.run === "clearall") clearAllAction();
  }

  function clearAllAction() {
    const btn = document.getElementById("clear-btn");
    if (!btn || btn.disabled) { msg("nothing to clear"); return; }
    // The app confirms before wiping; inside the tour the button IS the
    // confirmation, so auto-accept for this one click.
    const orig = window.confirm;
    window.confirm = function () { return true; };
    try { btn.click(); } finally { window.confirm = orig; }
    testsDone = false;
    demo2Done = false;
    msg("cleared ✓");
    setTimeout(place, 450);
  }

  function msg(t) { root.querySelector("#tutor-act-msg").textContent = t; }

  async function loadDemoFile(which, name, advance) {
    msg("loading…");
    try {
      const res = await fetch("/api/tutorial/demo?which=" + which);
      const d = await res.json();
      if (!d.ok) { msg(d.error || "demo unavailable"); return; }
      const file = new File([d.content], name, { type: "text/xml" });
      if (typeof fileObjects !== "undefined" && typeof postAll === "function") {
        fileObjects = fileObjects.filter(function (f) {
          return f.name !== name;
        });
        fileObjects.push(file);
        await postAll();
        // postAll keeps the current selection — switch the panel to the
        // freshly loaded demo so the tour is pointing at what it says.
        try {
          if (typeof loaded !== "undefined" &&
              typeof renderCurrent === "function") {
            const i = loaded.findIndex(function (f) {
              return f.filename === name;
            });
            if (i >= 0) {
              currentIdx = i;
              if (typeof returnToMain === "function") returnToMain();
              renderCurrent();
            }
          }
        } catch (e) {}
        msg("loaded ✓");
        if (which === 2) demo2Done = true;
        if (advance) {
          setTimeout(function () { idx += 1; render(); }, 700);
        } else {
          setTimeout(render, 600);
        }
      } else {
        msg("upload not ready — drag the file in instead");
      }
    } catch (err) {
      msg("could not load demo (" + err + ")");
    }
  }

  /* Jar detour: hide the tour, open the real jar modal, and come back
   * to the "Load your lab" step once Save or Cancel closes it. */
  function jarDetour() {
    const modal = document.getElementById("jar-modal");
    const save = document.getElementById("jar-save-btn");
    const cancel = document.getElementById("jar-cancel-btn");
    const chip = document.getElementById("jar-chip");
    if (!modal || !save || !cancel || !chip) {
      msg("jar dialog unavailable");
      return;
    }
    detour = "jar";
    root.style.display = "none";
    chip.click();
    setTimeout(function waitClose() {
      if (!open) return;
      if (modal.classList.contains("hidden")) {
        detour = null;
        root.style.display = "";
        idx = 3;
        render();
      } else {
        setTimeout(waitClose, 250);
      }
    }, 450);
  }

  /* Run-tests detour: the tour vanishes for the 1–2 s the per-row run
   * takes (page stays blocked), then returns framing the results. */
  function runTestsDetour() {
    const btn = document.getElementById("run-tests-btn");
    if (!btn || btn.disabled) { msg("load the demo circuit first"); return; }
    const perrow = document.getElementById("perrow-toggle");
    if (perrow && !perrow.checked) {
      perrow.checked = true;
      perrow.dispatchEvent(new Event("change"));
    }
    detour = "run";
    root.classList.add("tutor-ghost");
    btn.click();
    const t0 = Date.now();
    let sawRunning = false;
    (function poll() {
      const prog = document.getElementById("tests-progress");
      const res = document.getElementById("tests-results");
      const running = (prog && !prog.classList.contains("hidden")) ||
        (btn.disabled && Date.now() - t0 < 4000);
      if (running) sawRunning = true;
      const hasResults = res && res.textContent.trim().length > 0;
      const waitedEnough = sawRunning || Date.now() - t0 > 2500;
      if (waitedEnough && !running && hasResults) {
        detour = null;
        testsDone = true;
        root.classList.remove("tutor-ghost");
        const spotEl = root.querySelector("#tutor-spot");
        spotEl.style.transition = "none";
        render();
        setTimeout(place, 250);
        setTimeout(function () {
          place();
          spotEl.style.transition = "";
        }, 700);
      } else if (Date.now() - t0 > 60000) {
        detour = null;
        root.classList.remove("tutor-ghost");
        msg("still running — press Next when the rows appear");
        render();
      } else {
        setTimeout(poll, 300);
      }
    })();
  }

  function enterSample(key) {
    const s = SAMPLES[key];
    samplePage = document.createElement("div");
    samplePage.id = "tutor-sample-page";
    samplePage.innerHTML = s.page;
    document.body.appendChild(samplePage);
    sample = { key: key, i: 0 };
    render();
  }

  /* the ? help popup */
  function openHelp() {
    if (document.getElementById("tutor-help-pop") || open) return;
    const pop = document.createElement("div");
    pop.id = "tutor-help-pop";
    pop.innerHTML =
      '<div id="tutor-help-card">' +
      '  <button id="tutor-help-x" title="Close">✕</button>' +
      "  <h3>Need a refresher?</h3>" +
      "  <p>Replay the tour</p>" +
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

  /* boot */
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
