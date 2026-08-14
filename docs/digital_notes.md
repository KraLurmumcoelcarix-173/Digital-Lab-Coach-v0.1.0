# Digital Notes

Last updated: 2026/7/03

---

## .dig File Format

### Top-level structure

- Root element: `<circuit>`
- Two main children: `<visualElements>` (components), `<wires>` (connections)
- Wires are geometric (`p1`, `p2` coordinates), not pin-typed — must match endpoints to component pin positions
- Subcircuits referenced as `<elementName>filename.dig</elementName>`
- `<version>2</version>` is the current `.dig` format version
- `<measurementOrdering/>` appears (empty) at the end of every file

### Attribute parsing quirks

- Most `<entry>` values are `<int>`, `<long>`, `<boolean>`, or `<string>`.
- **`<rotation rotation="N"/>`** stores `N` as an XML *attribute*, not text content. Common parser mistake is to read `value.text` (returns `None`) and fall back to the tag name `"rotation"`. Must extract via `element.get("rotation")` and cast to `int`. Values are 0/1/2/3 for 0°/90°/180°/270°.
- Unrecognized `<elementAttributes>` value tags (`<testData>`, `<shape>`, etc.) should be preserved as raw text so nothing is silently lost.

### Element types encountered in 311 labs

| Element name | Purpose | Key attributes |
|---|---|---|
| `And`, `Or`, `XOr`, `NAnd`, `NOr`, `XNOr` | N-input gates | `Inputs` (int, default 2), `wideShape` (bool), `Bits` (default 1) |
| `Not` | Single-input inverter | `Bits` |
| `In`, `Out` | Circuit I/O pins | `Label`, `Bits` (default 1) |
| `Multiplexer` | Mux | `Selector Bits` (default 1 → 2-to-1), `Bits` |
| `Splitter` | Bus split/merge | `Input Splitting`, `Output Splitting`, `splitterSpreading` |
| `Tunnel` | Named net | `NetName`. Tunnels sharing a NetName are electrically connected. Can have `rotation` |
| `ROM` | Read-only memory | `Bits` (data width), `AddrBits`, `Data` (hex bytes), `isProgramMemory`, `bigEndian`. DLC flags an empty `Data` field as `empty_rom` (warning) |
| `Register` | Sequential register | `Bits`, optional `isProgramCounter` |
| `RegisterFile` | Built-in register bank (Memory category): 2 async read ports + 1 clocked write port | `Bits`, `AddrBits`; seen in real student CPUs (2 of 6 in the r28 batch) |
| `Const` | Constant value | `Value` (int), `Bits` |
| `Ground`, `VDD` | Power rails | Single output pin. Can have `rotation`, `Bits` |
| `Comparator` | A vs B (greater/equal/less) | `Bits`, `Signed` |
| `Add` | Adder | `Bits` |
| `BitExtender` | Width conversion | `inputBits`, `outputBits` |
| `BarrelShifter` | Variable shift | `Bits`, `direction`, `barrelShifterMode` |
| `Seven-Seg` | 7-segment LED display (non-hex). Lab 2. | `Color` (awt-color, ignored), `rotation`. **Eight 1-bit input pins**: `a, b, c, d` (top edge) + `e, f, g, dp` (bottom edge). |
| `Decoder` | One-of-N decoder | `Selector Bits` → 2^N outputs |
| `Demultiplexer` | Routes 1 input to one of 2^N outputs (others 0) | `Selector Bits`, `Bits` |
| `PriorityEncoder` | Priority → binary index | `Selector Bits` → 2^N inputs |
| `Clock` | Clock signal | No attributes in basic use |
| `Testcase` | Embedded simulator test cases | `testData/dataString`, default Label `"Testdata"`. **No signal pins.** |
| `Rectangle` | Annotation/grouping box | **No signal pins.** Pure visual |

Elements in scope but with no encountered samples yet: `RAM`, `D-FlipFlop`, `JK-FF`, `T-FF`, `Counter`, `Driver` (tri-state), `Display`, `LED`, `Switch`, `Button`.

### Pin geometry (offsets from anchor, verified empirically)

Digital's coordinate system: x increases rightward, y increases downward. Anchor is the `<pos>` of the visual element. Pin coords = anchor + offset, with rotation applied to the offset before adding the anchor.

| Element | Inputs (left edge) | Outputs (right edge) | Notes |
|---|---|---|---|
| `Not` | `A` (0, 0) | `Y` (40, 0) | Width 40 |
| `And`/`Or`/`XOr` (wideShape=True, even N) | Two halves with **40-unit gap** in the middle | `Y` (80, center_y) | Verified empirically. N=2 → (0,0),(0,40); N=4 → (0,0),(0,20),(0,60),(0,80); N=6 → (0,0),(0,20),(0,40),(0,80),(0,100),(0,120) |
| `And`/`Or`/`XOr` (wideShape=True, odd N) | `in_i` at (0, i*20) — uniform | `Y` (80, center_y) | Tested via three_inputand, five_inputand (tests pass with full I→O); offsets match wire endpoints exactly |
| `And`/`Or`/`XOr` (wideShape=False) | Assumed `in_i` at (0, i*20) | Assumed `Y` (80, center_y) | **Not yet observed in any sample.** Code uses the same uniform-20 path as wideShape+odd. Will verify when we encounter one in the field |
| `NAnd`/`NOr`/`XNOr` (any) | same as positive variants | Output bubble pushes visible pin ~20 right; absorbed by snap tolerance | Only wideShape=True observed (single_nand) |
| `In`/`Out`/`Const`/`Clock`/`Ground`/`VDD` | single pin at anchor (0, 0) | | |
| `Tunnel` | single bidir pin at anchor | | NetName unifies across the circuit |
| `Multiplexer` (sel_bits=1, n=2) | `in0` (0, 0), `in1` (0, 40), `sel` (20, 40) | `out` (40, 20) | **Different spacing for 2-input vs 4+** |
| `Multiplexer` (sel_bits≥2, n≥4) | `in_i` at (0, i*20), `sel` at (20, n*20) | `out` at (40, n*10) | |
| `Splitter` | `in_i` at (0, i*spacing) | `out_i` at (20, i*spacing) | spacing = 20 × `splitterSpreading` (default 1, can be 2+). **`mirror`=true negates the spacing** — pin i at −i*spacing, pin 0 stays on the anchor row (SVG-verified on a real add-sub "32 → 31,1" sign extractor, r34) |
| `Register` | `D` (0, 0), `C` (0, 20), `en` (0, 40) | `Q` (60, 20) | `en` always present even when tied to Const(1) |
| `Comparator` | `A` (0, 0), `B` (0, 20) | `gr` (60, 0), `eq` (60, 20), `le` (60, 40) | Width **60**, not 80 — common mistake |
| `Add` | `a` (0, 0), `b` (0, 20), `c_i` (0, 40) | `s` (60, 0), `c_o` (60, 20) | Width **60**. Input order top-to-bottom matches Digital's UI: a, b, c_i. c_o at y=20 not y=40 — earlier-assumed (80, 40) layout consistently snapped to wire L-bends and produced phantom multi-drivers. |
| `BitExtender` | `in` (0, 0) | `out` (80, 0) | Width varies with outputBits; snap tolerance absorbs ±20 |
| `BarrelShifter` | `in` (0, 0), `sh` (0, 40) | `out` (60, 20) | |
| `Seven-Seg` | `a/b/c/d` at `(0,0)/(20,0)/(40,0)/(60,0)`; `e/f/g/dp` at `(0,140)/(20,140)/(40,140)/(60,140)` | (no outputs — display sink only) | **Corrected r34** via SVG export of a real Lab-2 file: pins sit ON the anchor row and at +140, `dp` on the SAME row as e/f/g. The old −40/180/240 offsets only ever matched because students park tunnels exactly one wire-length past the pins. |
| `ROM` | `A` (0, 0), `sel` (0, 40) | `D` (60, 20) | Box is 60 wide (SVG-verified, r30) — the old (80, 20) survived only via loose endpoint snapping |
| `RegisterFile` | `Din` (0,0), `we` (0,20), `Rw` (0,40), `C` (0,60), `Ra` (0,80), `Rb` (0,100) | `Da` (80,0), `Db` (80,20) | Built-in register bank (Memory category); width **80**; reads combinational, write clocked; measured on a real student CPU (r28, re-landed r31) |
| `Decoder` | `sel` (20, (n_outputs − 1) * 20) | `out_i` at (60, i*20) | **sel sits at the LAST output's height, NOT one row below like the Mux** — measured on a rotation-2 sel_bits=5 Decoder whose sel feed lands exactly at (20, 620); the old n*20 table falsely flagged its sel undriven |
| `Demultiplexer` (sel_bits=1, n=2) | `in` (0, 20), `sel` (20, 40) | `out0` (40, 0), `out1` (40, 40) | mirror of the 2-input Mux |
| `Demultiplexer` (sel_bits≥2, n≥4) | `in` (0, n*10), `sel` (20, n*20) | `out_i` at (40, i*20) | measured on a sel_bits=5 register-file write-enable fan-out; non-selected outputs drive 0 |
| `PriorityEncoder` | `in_i` at (0, i*20) | `num` (80, 0), `f` (80, 20) | `f` = 1-bit "any input set" flag; students wire it as ROM chip select (r30) |

`flipSelPos` (Multiplexer / Demultiplexer / Decoder): Digital's "flip selector position"
attribute moves the `sel` pin to the TOP edge at (20, −20); everything else is unchanged.

### Rotation

- Rotation index N applies a 90°×N counter-clockwise rotation to every pin offset *before* adding the anchor.
- In screen coordinates (y growing down), CCW visual = math CW.
- Formula: `(dx, dy)` → `(dy, -dx)` for N=1, `(-dx, -dy)` for N=2, `(-dy, dx)` for N=3.
- Verified empirically against a rotated Multiplexer (rotation=1, sel_bits=1) in `register-file.dig` and a rotated Splitter (rotation=2) in `cpu.dig`.

### Gates

- Gate multi-input attribute is `Inputs` (`<int>`), absent = 2.
- Gate anchor = TOP input pin, not center.
- For `wideShape=True` with even `N≥4`, the input column has a 40-unit gap in the middle (so the output sits centered between the halves).
- **Negated inputs (`inverterConfig`)**: a gate may carry `<inverterConfig>`
  listing input pin names (`In_1`, `In_2`, …) that are inverted (a bubble on
  that specific input). It changes the gate's logic and its visual state — e.g.
  `add-sub.dig` uses an `And` with `In_1`/`In_2` negated. Parsed and kept
  in `attributes`; the Layer-1 value evaluator (`dlc/sim/simulator.py`)
  applies the per-input negation via the gate's inverter bubbles.

### Wires

- A `<wire>` has exactly two endpoints: `<p1>` and `<p2>`, each with x/y coordinates.
- Wires carry NO pin or signal-type information.
- Connectivity is INFERRED: wires sharing an endpoint coordinate form a net.
- Each `<wire>` is one straight segment between two points (may be horizontal, vertical, or diagonal).
- A visual corner is NOT one bent wire — it's two separate `<wire>` segments sharing an endpoint coordinate. An L-path = 2 wires, a path with 2 turns = 3 wires.
- **Diagonal wires**: Digital allows non-Manhattan wires. They connect their endpoints normally via union-find, but our T-junction detection currently skips them (no observed cases needing it).
- **Mid-wire branch points** (T-junctions): a wire endpoint may land on the *interior* of another wire, not just at its endpoint. Net-building must treat any shared coordinate — not just endpoints — as a potential connection. Implemented via `_midpoint_branches` scanning each horizontal/vertical wire for foreign endpoints landing strictly between p1 and p2.

### Real bug patterns the parser must surface

- **Dangling input** — input pin with no wire endpoint at its predicted coord. Detected as a singleton net containing only sink-direction pins.
- **Multi-driver** — two or more outputs feeding the same net. Detected by `len(net.drivers()) > 1`.
- **Combinational loop** — cycle of purely combinational gates without a clocked register breaking it. Detected via `networkx.simple_cycles(g)` (F8).
- **Bit-width mismatch** — N-bit signal feeding an M-bit pin. Requires splitter bit-range parsing and per-net width inference.
- **Miswire / wrong-pin / wrong-input-position** — connected to wrong pin, surfaces as a failed test vector. Layer 1 sees a valid topology; Layer 3 detects the semantic mismatch.

Digital does NOT flag multi-driver on load. The error only surfaces at simulation time, and only when a signal actually traverses the conflicted net.

### Wire endpoint degree as a pin-vs-routing classifier

A wire endpoint at coord X is **degree N** if N wires terminate there. Used by net builder:
- Degree 1 = a real pin location (exactly one wire ends there). Candidates for snapping or implicit-pin attachment.
- Degree ≥ 2 = L-bend or T-junction routing point. Excluded from implicit-pin assignment to prevent misclaim.

### Pin snap / implicit attachment

The net builder uses two-stage pin attachment:

1. **Predicted-pin snap** (for known-geometry elements): for each (pin, endpoint) pair within `PIN_SNAP_TOLERANCE` (Manhattan distance ≤ 30), build all triples sorted by distance. Walk in sorted order and claim each pair only if neither side already claimed. Multiple pins at the *exact same coord* (distance 0) can share an endpoint.
2. **Implicit-pin attach** (for no-geometry components, mostly subcircuit references): unclaimed degree-1 endpoints get assigned to the nearest no-geometry component within `IMPLICIT_PIN_RADIUS` (= 500). Per-instance cap = `child.inputs() + child.outputs()`; if more endpoints claim the instance than the cap allows, the farthest are dropped.
3. **Co-located output rescue**: if a predicted output-direction pin doesn't snap to a wire endpoint but its exact coord is already part of a known net (most commonly because a Tunnel was placed directly on the pin with no connecting wire), the pin joins that net as a driver. This is how students wire Clock-through-tunnel in pipelined circuits, and applies to any output pin not just Clock.

Dangling **outputs** are dropped from the netlist (they're not errors — just unused). Dangling **inputs** are kept as singleton nets so F5 can detect them as bugs.

## Layer-1 vs Layer-3 detection responsibility

| Bug category | Layer 1 (deterministic) | Layer 3 (LLM) |
|---|:-:|:-:|
| Dangling input pin | ✓ catches | ✓ explains |
| Multi-driver short | ✓ catches | ✓ explains routing intent |
| Combinational loop | ✓ catches | ✓ describes the cycle |
| Width mismatch | ✓ catches (with F6) | ✓ explains |
| Missing subcircuit file | ✓ catches | ✓ suggests fix |
| **Semantic miswire** | ✗ | ✓ (only Layer 3 can know intent) |
| **Wrong input-position** | ✗ | ✓ |
| **Wrong op-encoding**  | ✗ | ✓ |
| **Routing accident through unrelated pin coord** | ✓ catches (multi-driver) but cannot explain | ✓ explains |

The ablation contrast (Layer 1 alone vs Layer 1+3 vs Layer 3 alone) is the project's central evaluation. The 30 bug benchmark is split across all three columns.

## Digital UI Features Relevant to Students

### Debugging tools that exist natively
- Single-step simulation
- Test case runner with pass/fail output

### What students struggle with (from ULA experience)
- Wire routing accidents that look right visually but short signals through an unrelated component's pin coord (mazes).
- Forgetting to wire `en` on a Register.
- More components, more possible bits width mismatch, whereas Digital does not do an ideal job to instantly point the bug
- Multi-driver shorts that don't surface at load time and only become apparent through unexpected test failures.
- Subcircuit reference path issues when sharing labs across machines.

### Features we'd want DLC to add or enhance
- Inline highlighting of dangling pins / multi-driver nets at edit time (before simulation). [done]
- Component-level reachability annotation ("this output is unused", "this input is undriven"). [done]

## Parser scope policy

DLC's parser aims to **semantically understand** elements used in COMP 311 labs so far. Other elements (transistor primitives, FPGA-specific blocks, FSM editor outputs, etc.) are parsed structurally but treated as opaque `UnknownComponent` with named pins for now. This lets the analyzer skip unrecognized components and the LLM describe them generically, while keeping the parser future-proof for new labs.

**Known-and-semantically-supported**:
Wire (straight, L, diagonal), And, Or, XOr, NAnd, NOr, XNOr, Not, In, Out, Multiplexer, Demultiplexer, Splitter, Tunnel, ROM, Register, Const, Comparator, Add, BitExtender, Clock, Ground, VDD, BarrelShifter, Decoder, PriorityEncoder, Testcase, Rectangle, Seven-Seg

**Annotation-only** (parsed but explicitly carry no signal pins): Testcase, Rectangle. Excluded from implicit-pin candidate set.

**Out of initial scope** (parsed but opaque, may be added later):
all transistor-level elements, FSM elements, FPGA-board-specific blocks, Verilog wrappers, GAL/JEDEC-specific elements.

## CLI Mode (what the autograder uses)

- Command: `java -cp Digital.jar CLI test -circ FILE.dig [-verbose]`
- Output format: `Test: passed` or `Test: failed (N%)` per test case
- Exit codes:
  - `0` — every testcase passed
  - `1` — at least one testcase failed OR reported a testcase-level
    error (e.g. `name: Test signal Qx not found in the circuit!`)
  - `200` — execution error before testing (e.g. circuit file not found)
- A failing run ends with the line `Tests have failed.`

### `-verbose` value table (the fast per-row source)

With `-verbose`, every FAILED testcase's result line is followed by
Digital's own value table:

```
this_is_a_test: failed (20%)
A B C D load Clock Fa Fb Fc Fd Fe Ff Fg
0 0 0 0 0 0 1 1 1 1 1 1 0
1 1 1 1 0 0 1 0 1 1 0 1 E: 0 / F: 1
...
```

Facts the fast runner (`dlc/testing/runner.py`) relies on, all
verified empirically:

- First table line = the testcase's header names, space-separated.
- One table line per EXECUTED row, in execution order. Digital
  expands `loop(N, K) … end loop` blocks itself — the same expansion
  DLC's TestSpec performs — so table line *i* ↔ `spec.rows[i]`.
- A row with a `C` clock token still yields exactly ONE table line
  (the clock column echoes the post-pulse value, e.g. `0`).
- A failing row renders each mismatched output cell as
  `E: <expected> / F: <found>`; passing rows echo plain values
  (formats vary: `1E`, `0x19`, `FFFFFFE0`...). "Row failed" ==
  "row contains an E:/F: cell".
- Passed testcases print NO table (nothing to print): a passed
  result line means every row passed.
- Testcase labels with spaces print in full (`Register File Test:
  failed (1%)`); a missing label prints as `unnamed`.

## Subcircuit Resolution

- A circuit referencing `alu.dig` means Digital looks for `alu.dig` in the same directory or library path.
- For our parser: recursively load referenced subcircuits to fully analyze a top-level circuit.
- Subcircuit cache is per-parse-session — same `.dig` referenced N times is loaded once. Circular references raise.
- A referenced file with a bare name may live in any subfolder; we search recursively and pick the shallowest match (ambiguity is flagged but doesn't fail the parse).
- **Subcircuit instance pin prediction**: every RESOLVED child gets
  declared-pin geometry — inputs (In/Clock elements, child FILE order) at
  `(0, i*20)` from the instance pos, outputs (file order) at
  `(Width*20, i*20)`. A child with no `Width` attribute renders **3 grid
  units (60 px) wide — Digital's real default** (SVG-verified on real
  student trees; our old guess of 10 pushed outputs 140 px right and fed
  the implicit-pin fallback, which then mislabeled pins). The implicit-pin
  x-midpoint heuristic below now applies only to UNRESOLVED children
  (missing files).
- **Subcircuit instance pin direction resolution** (unresolved children only): the instance has no native geometry, so direction is inferred by splitting the instance's implicit pins at the x-midpoint (left = inputs, right = outputs), sorting each side by y, and zipping against the child circuit's `In`/`Out` elements sorted by y. Implicit-pin count is capped to the child's port count to prevent over-claim from neighboring routing.

## L1 regression ground truths (SVG-probed on real lab-5 trees, jar-verified)

- **PriorityEncoder has TWO outputs**: `num` at (80, 0) and `f` — the
  1-bit "any input set" flag — at (80, 20). Students wire `f` as the
  ROM's chip select (`PriorityEncoder.f -> ROM.sel`).
- **ROM box is 60 wide**: A (0,0), sel (0,40), D (60,20). D at (80,20)
  was wrong and survived only via loose endpoint snapping — and would
  have blessed a wire Digital refuses ("No output connected to a wire").
- **Endpoint snapping**: an OUTPUT pin may claim a nonzero-distance
  endpoint only if that endpoint has wire-degree 1 (a terminating end).
  Degree-2+ coords are routing (L-bends/junctions of other nets); letting
  an unwired `gr`/`le` grab a corner 20 px away fabricated multi-driver
  errors across whole comparator ladders.
- **Multi-driver is a RUNTIME error in Digital**, raised only when tied
  outputs actually disagree ("More than one output is active on a wire").
  A register Q shorted to Ground as an "x0 is always 0" hack passes the
  official register-file test (jar-verified, both v0.30 and v0.31). One
  constant (Ground/VDD/Const) + one real output => WARNING; two real
  outputs => still ERROR.
- **Custom-component pins follow the child's FILE order, not canvas
  order** (re-confirmed: the answer alu declares FlagZ before Result in
  the file but places Result above FlagZ on canvas; Digital renders
  FlagZ on the top row).
- **Multi-driver tolerances (r34, jar-probed)**: Digital's short-circuit
  check fires at RUN time on value conflict, so three same-net driver
  mixes run cleanly and are WARNINGS, not errors: (1) one real output +
  agreeing constants; (2) several SAME-valued constants tied by one
  tunnel name; (3) a top-level `In` the file's testcase does not drive —
  the test vector never powers it, and the jar lets the other driver
  win. An In that IS a testcase column, an In in a file with no
  testcase (interactive mode drives every In), two real outputs, or
  constants with different values all stay hard errors.
- **Mode A debugs the injected run (r34)**: when the file's testcase is
  missing/modified and an official set exists, /api/llm/debug builds the
  same sibling injected temp the Dashboard runs use and debugs THAT —
  otherwise a header-only testcase yields zero failing rows and the
  board wrongly says "every row passes". Accept-Fix temps built from the
  raw file get the official rows written in place; temps descending
  from Mode B keep their coach-added rows untouched.
- **Gradescope-style injection (r31 policy)**: when a filename has an
  official test set registered (data/official_tests_defaults.json or a
  Settings entry) and the file's own testcase does not MATCH it
  (missing, header-only, or modified — normalized-content hash), test
  runs replace the file's testcases with the official rows in a sibling
  temp copy (dlc/testing/inject); the panel says so from upload
  (`official_test_status` in the file summary). ROM contents are NEVER
  injected — a wrong/empty ROM is the student's own work and Layer 3's
  teaching material, and official ROM/program data must never ship in
  the tool. An empty ROM stays a Layer-1 WARNING that blocks nothing;
  Mode B remains the test-expansion teacher on top of always-official
  test runs.

- **Duplicated identical gates tied together demote to WARNING (r37)**:
  jar-probed — two And gates with the same inputs driving one tunnel
  net run fine (they always agree), while And+Or on the same inputs
  short-circuit at run time. `_check_multi_drivers` demotes only when
  every driver is a plain commutative gate (or Not) with the same
  element, Bits, input NETS and inverter bubbles
  (`_identical_gate_signature`); anything else stays a hard error.
  Field source: a real Lab-2 SOP decoder rebuilding product terms per
  segment block under one tunnel name.
- **PriorityEncoder drives `f` in the evaluator (r37)**: Digital's PE
  has `num` + a 1-bit `f` "any input set" flag. The evaluator only
  produced `num`, so a ROM whose chip-select hangs off `f` never
  evaluated and the whole output stage read undefined — while the jar
  ran it fine (empty ROM words read 0). Both fixed: `f` is emitted and
  empty ROMs read 0, so evaluator mismatch cells now match Digital's.
- **Mode A runaway firewalls (r37)**: (1) children failing their
  OFFICIAL tests (injected when missing/modified) gate the parent into
  the free suggestion branch — the s008 cpu routes straight to
  control-unit.dig, 0 model calls; (2) a jar per-row run where EVERY
  row errors is a REFUSAL (unconnected tunnel / renamed test signals)
  — returned as lazy `build_refused`, or `unbound_columns` with rename
  guidance when testcase columns bind to no port, 0 model calls;
  (3) `_MAX_REFUTED_IDEAS = 4` — after 4 verifier-refuted ideas the run
  stops spending (no more retries/escalations/clusters), sets
  `stopped_early`, and ships the best unverified idea (the benchmark's
  best-solution hard trigger); (4) `timings` in the analysis payload
  records per-call and per-verify seconds.
- **Frozen-trunk exception to the lazy bars (r37)**: when the failing
  rows are fully explained by "every output frozen at one constant"
  (constant found per column, never-mismatching outputs carry one
  constant expected, passing rows consistent), the scattered flag and
  pass-rate bars stand aside, and all failing rows form ONE cluster so
  a partial fix gets refuted instead of shipping as a per-row card.
  Convicted on s008's empty decode ROM (8/8 rows, stuck at 0). Rows
  failing in differing column sets keep every ratified bar.
- **ROM-data steer only fires on stored words (r37)**: the r27
  "do NOT propose another Data change" escalation steer presumes the
  stored words satisfy the passing rows; on an EMPTY ROM the missing
  words ARE the bug, so the steer is suppressed and suspect attrs
  carry the exact `change_attribute`/`Data` op shape instead
  (`_suspect_attrs`: AddrBits/Bits/splitting ranges/data_words_stored;
  stored words themselves are never listed — injected official
  programs must not leave the backend).
- **Splitter attribute key is `Output Splitting`** (not `Splitting`),
  and Digital rejects a `Splitting` entry silently — the box renders
  with its default 8-bit output. Bit-group syntax `1,1,1,1` verified;
  `1*4` also parses in Digital but our probe used the explicit form.
- **Mode A daily cap is 1 (r37)** — a booked use requires a delivered
  verified card, and the stop condition bounds one run's spend, so a
  single daily analysis is a full analysis.
- **Control-unit files skip the lazy gate (r37.1, TEMPORARY instructor
  ruling — revisit on request)**: any file whose real name normalizes
  to control-unit (`control-unit.dig`, `controlunit.dig`, injected
  temps included) bypasses gross_check entirely and goes straight to
  analysis when rows fail (`_lazy_exempt_name` /
  `assemble_evidence(lazy_exempt=True)`; the web layer keys on
  `req.filename` so coach temps qualify too). Refusal guards
  (build_refused / unbound_columns) and the failing-children gate
  still apply. All other filenames keep every ratified lazy bar.

- **Stored data is checked FIRST, not last**:
  the Mode A prompt's "ROM data is a last resort" bias is deleted.
  Mixed rom+logic circuits sort evidence into the data signature
  (frozen outputs, address/select resolving fine) vs the logic
  signature (outputs varying in gate-explainable ways) and fix the
  bucket the rows show. A student's OWN stored table (≤32 words) rides
  the suspect attrs as `stored_words` so a partially-wrong word can be
  convicted; empty ROMs keep the exact-op `data_note`.
- **Rom-injected runs**: Mode A already debugs the rom-filled
  injected temp (prepare_injected_run fills empty ROMs whenever a
  runtime payload is registered, testcase status independent). New:
  the run's prompts carry a [ROM NOTE] ("grader-loaded words are
  correct by definition — never propose Data changes there"), injected
  words are excluded from the payload (`hide_rom_words`), every
  verified card gains a "Check your ROM data" hint (fix.rom_hint + the
  student explanation) because the student's own file still has the
  ROM unprogrammed, and the accept-fix "show the green" rerun now runs
  through the same injection (rom-filled sibling, removed after; the
  registered coach temp never stores official rom words).
- **Premium max tokens 8000**: a live Opus full-decode-
  table derivation still truncated at 6000 and died as
  invalid_response; the single-cluster frozen-trunk shape needs the
  headroom.

## Known limitations to revisit (Keep updating during path 1 development)


## Open Questions under investigation

- Where exactly does Java plugin API expose hooks for adding analysis panels? (Path 3 question, defer investigation)