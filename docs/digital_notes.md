# Digital Notes

Last updated: 2026/5/23

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
| `Const` | Constant value | `Value` (int), `Bits` |
| `Ground`, `VDD` | Power rails | Single output pin. Can have `rotation`, `Bits` |
| `Comparator` | A vs B (greater/equal/less) | `Bits`, `Signed` |
| `Add` | Adder | `Bits` |
| `BitExtender` | Width conversion | `inputBits`, `outputBits` |
| `BarrelShifter` | Variable shift | `Bits`, `direction`, `barrelShifterMode` |
| `Seven-Seg` | 7-segment LED display (non-hex). Lab 2. | `Color` (awt-color, ignored), `rotation`. **Eight 1-bit input pins**: `a, b, c, d` (top edge) + `e, f, g, dp` (bottom edge). |
| `Decoder` | One-of-N decoder | `Selector Bits` → 2^N outputs |
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
| `Splitter` | `in_i` at (0, i*spacing) | `out_i` at (20, i*spacing) | spacing = 20 × `splitterSpreading` (default 1, can be 2+) |
| `Register` | `D` (0, 0), `C` (0, 20), `en` (0, 40) | `Q` (60, 20) | `en` always present even when tied to Const(1) |
| `Comparator` | `A` (0, 0), `B` (0, 20) | `gr` (60, 0), `eq` (60, 20), `le` (60, 40) | Width **60**, not 80 — common mistake |
| `Add` | `a` (0, 0), `b` (0, 20), `c_i` (0, 40) | `s` (60, 0), `c_o` (60, 20) | Width **60**. Input order top-to-bottom matches Digital's UI: a, b, c_i. c_o at y=20 not y=40 — earlier-assumed (80, 40) layout consistently snapped to wire L-bends and produced phantom multi-drivers. |
| `BitExtender` | `in` (0, 0) | `out` (80, 0) | Width varies with outputBits; snap tolerance absorbs ±20 |
| `BarrelShifter` | `in` (0, 0), `sh` (0, 40) | `out` (60, 20) | |
| `Seven-Seg` | `a/b/c/d` at `(0,-40)/(20,-40)/(40,-40)/(60,-40)`; `e/f/g` at `(0,180)/(20,180)/(40,180)`; `dp` at `(60, 240)` | (no outputs — display sink only) | Verified against Lab 2 (rotation=0) and tier3_latched_display (rotation=3). The `dp` pin sits one row below `e/f/g`, aligned x-wise with `d`. |
| `ROM` | `A` (0, 0), `sel` (0, 40) | `D` (80, 20) | Width varies with data width |
| `Decoder` | `sel` (20, n_outputs * 20) | `out_i` at (60, i*20) | sel sits bottom-middle (same pattern as Mux n≥4) |
| `PriorityEncoder` | `in_i` at (0, i*20) | `num` (80, 0) | |

### Rotation

- Rotation index N applies a 90°×N counter-clockwise rotation to every pin offset *before* adding the anchor.
- In screen coordinates (y growing down), CCW visual = math CW.
- Formula: `(dx, dy)` → `(dy, -dx)` for N=1, `(-dx, -dy)` for N=2, `(-dy, dx)` for N=3.
- Verified empirically against a rotated Multiplexer (rotation=1, sel_bits=1) in `register-file.dig` and a rotated Splitter (rotation=2) in `cpu.dig`.

### Gates

- Gate multi-input attribute is `Inputs` (`<int>`), absent = 2.
- Gate anchor = TOP input pin, not center.
- For `wideShape=True` with even `N≥4`, the input column has a 40-unit gap in the middle (so the output sits centered between the halves).

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
Wire (straight, L, diagonal), And, Or, XOr, NAnd, NOr, XNOr, Not, In, Out, Multiplexer, Splitter, Tunnel, ROM, Register, Const, Comparator, Add, BitExtender, Clock, Ground, VDD, BarrelShifter, Decoder, PriorityEncoder, Testcase, Rectangle, Seven-Seg

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
- **Subcircuit instance pin direction resolution**: the instance has no native geometry, so direction is inferred by splitting the instance's implicit pins at the x-midpoint (left = inputs, right = outputs), sorting each side by y, and zipping against the child circuit's `In`/`Out` elements sorted by y. Implicit-pin count is capped to the child's port count to prevent over-claim from neighboring routing.

## Known limitations to revisit (Keep updating during path 1 development)

1. **Splitter bit-range parsing** — `"25-31, 24-20, …"` strings are stored as raw text. F3/F6 prerequisite. [done]
2. **Cycle filtering for clocked feedback** — `simple_cycles(g)` finds all cycles, including legitimate Register feedback through Clock. F8 needs to subtract cycles passing through a clocked element.
3. **Stable component IDs across edits** — currently `enumerate(components)` index; will need content-hash or position-hash for diff reports.

## Open Questions under investigation

- How does Digital handle missing subcircuit files? (Investigating)
- Does Digital's CLI mode produce structured output (JSON?) or only human-readable text? [done]
- Where exactly does Java plugin API expose hooks for adding analysis panels? (Path 3 question, defer investigation)