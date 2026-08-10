# L3 `/api/llm/debug` — frozen sub-agent I/O contract (`l3.debug.v1.1`)

Status: FROZEN 2026-07-06; ops vocabulary ratified 2026-07-06. Revised to
v1.1 2026-07-31 with the ratified Layer 3 phase board: the two-level
progressive-disclosure ladder (hint → fix) replaces the flat hypothesis
output (§4/§6), the gross-checks and cluster signature are named
concretely (§2), and the telemetry list gains the ladder events (§8).
Changes bump the version string; agents and executor validate against it.

This contract carries the REAL deterministic shapes that already exist in the
codebase — `/api/simulate`'s payload, the localizer's `SuspectReport`, the
patch applier's 8 ops — plus the `animation_script[]` schema, which is an
AGENT OUTPUT, never an input.

---

## 1. Endpoint + scope

`POST /api/llm/debug` — Mode A coordinator. Explicit trigger only.

Request (client → server):

```json
{"session_id": "...", "filename": "cpu.dig", "spec_index": 0}
```

Scope (ratified): Mode A debugs the SELECTED file's own testcase; when Mode B
injected rows this session, the coordinator targets the CURRENT TEMP CIRCUIT
(original + finally-injected rows). The circuit cannot be switched inside L3.

## 2. Coordinator pipeline (deterministic, server-side)

1. Gate: any deep-L1 issue → both boards locked (never reaches here).
2. Per-row run (fast Mode C when Digital.jar is configured; the Python
   evaluator's expected-vs-found sweep otherwise) → failing rows. All pass
   → no-op response (`mode:"clear"`). A gross-check trips → `mode:"lazy"`
   (suggestion-only branch; consumes 0 daily uses). The gross-checks
   (v1.1, implemented in `dlc/l3/evidence.py`):
   - `too_many_failures` — failing rows > 10;
   - `unbound_columns` — testcase columns matching no In/Out/Clock label;
   - `missing_clocked_logic` — the testcase drives a clock but the tree
     holds no state element (register/flip-flop/RAM/counter).
3. CLUSTER failing rows by signature (Phase 0.5, ratified): the tuple of
   (mismatched output columns, exercised opcode/select values read from the
   row's inputs — plus the manifest-decoded program CATEGORY of the word on
   the program ROM's output net for program-driven labs (v1.1), overlap of
   top localizer suspects). Cap: 4 clusters; one sub-agent per cluster —
   never one per row; overflow clusters FOLD into their nearest neighbor,
   never dropped.
4. Evidence per cluster: `/api/simulate` result for ≤ 2 REPRESENTATIVE rows
   (full per-net values), compact expected-vs-found for the rest;
   `localize()` per row, `merge_reports()` per cluster.

## 3. Sub-agent INPUT (one call per cluster)

```json
{
  "contract": "l3.debug.v1.1",
  "circuit": { "compact CircuitFacts": "inventory, io, subcircuits, selectors" },
  "testcase": { "name": "...", "headers": ["A", "B", "..."] },
  "cluster": {
    "rows": [
      { "index": 6, "raw": "5 10 0 3 15 0 0 1",
        "mismatches": [ {"column": "Result", "expected": "15", "found": "0"} ] }
    ],
    "representative_evidence": [
      { "row_index": 6,
        "net_values": { "10": {"value": 0, "bits": 4, "hex": "0"} },
        "unresolved_nets": [9],
        "outputs": [ {"label": "Result", "expected": "15", "found": "0x0", "ok": false} ] }
    ]
  },
  "suspects": { "SuspectReport.to_dict()": "failing/passing outputs + ranked suspects with reasons" },
  "suspect_wiring": [
    { "component_index": 16, "element": "Const", "label": null,
      "pins": [ { "pin": "out", "direction": "out", "net_id": 7,
                  "connects_to": [ {"component_index": 5, "element": "Add", "pin": "c_i", "direction": "in"} ] } ] }
  ]
}
```

`suspect_wiring` (v1.1) is the pin-level connection truth for every ranked
suspect — the netlist's far ends per pin, tunnels resolved — so the agent
can tell WHICH of several identical components drives the suspicious pin
instead of guessing among look-alikes. Each pin also carries `values`
({row_index: value} on the representative rows): the wiring↔net_values
join done FOR the model, so same-scored suspects separate by behavior
(a gate whose output contradicts its element kind is the prime candidate).

The agent reasons ONLY over these verified facts and is SINGLE-SHOT
(v1.1, ratified): no tools, no iteration, no nested fetches — one format
re-prompt when the reply is not the strict JSON object, one refutation
retry when the verifier refutes the fix. It never invents nets, widths,
or values.

## 4. Sub-agent OUTPUT (frozen shape — v1.1: the two-level ladder)

ONE call returns BOTH ladder levels: disclosure: at hint_level 1 the UI shows only `hint`; hint_level 2
("show me more") reveals `fix`. The split IS the spoiler guard's structural half — the F13
wording rules bind `hint.*` (must not state the concrete repair) and `fix.explanation_for_student` (teaches, never taunts).

```json
{
  "contract": "l3.debug.v1.1",
  "confidence": 0.9,
  "hint": {
    "suspect_region": "the adder's carry-in constant",
    "suspect_signals": ["c_i"],
    "why": "every failing row's Sum is exactly one too high"
  },
  "fix": {
    "ops": [
      {"op": "change_attribute", "component_index": 16, "name": "Value", "value": 0}
    ],
    "explanation_for_student": "the Const driving c_i omits Value, which defaults to 1 — every sum gained +1",
    "animation_script": [
      {"act": "diagnose_line", "text": "Rows 1-3 fail: Sum is always 1 too high."},
      {"act": "focus", "component_index": 5, "path": []},
      {"act": "mark_fix", "target": {"component_index": 16, "path": []},
       "label": "fixed: carry-in constant 1 -> 0 (was adding +1 to every sum)"},
      {"act": "retest"}
    ]
  }
}
```

`fix.ops` uses EXACTLY the ratified 8-op vocabulary of `dlc/l3/patch.py`:
`change_attribute · replace_element · swap_pins · rewire_pin · add_wire ·
delete_wire · add_component · delete_component` (indices reference the
ORIGINAL circuit; deletes apply last; new components wire via add_wire).

### animation_script ops (v1)

| act | fields | plays as |
|---|---|---|
| `diagnose_line` | `text` | one line typed onto the red diagnosis board |
| `focus` | `component_index`, `path` | magical mouse moves to the component (`path` = component indices from the top circuit down to the enclosing subcircuit instance; `[]` = top level) |
| `drill` | `path` | opens the drill-in overlay at that subcircuit (reuses the L1 drill-in) |
| `drill_back` | — | one level up |
| `mark_fix` | `target` (`{component_index, path}` or `{net_id, path}`), `label` | yellow component / yellow wire + "what/why fixed" label; also seeds the 3.10 persistent hint badge when `path` is non-empty |
| `retest` | — | draws the green Retest box, clicks it, triggers the per-row rerun on the temp fixed circuit (incl. Mode-B rows). MUST be the final act |

Executor-side validation (deterministic): unknown acts are dropped; `retest`
is forced last (appended if missing); `focus`/`drill`/`mark_fix` targets that
don't exist in the graph are skipped with a console note. Playback never
mutates any circuit — the fix was already applied to the temp file by the
oracle before anything is shown.

## 5. Verify (the self-check oracle — nothing unverified is ever shown)

For each hypothesis: `apply_patch(fix.ops)` → L1-regression guard →
`rerun_with_patch` → **CONFIRMED** iff (a) every row of the agent's cluster
now passes, (b) no previously-passing row regresses, (c) the guard passed.
Refuted → one retry with the refutation evidence appended, then dropped.
When a whole run would deliver ZERO cards, each validly-answered cluster
gets ONE escalation attempt with every refuted op disclosed and the
gate-kind sanity check forced ([ESCALATION] block) — still verified, still
droppable; nothing is ever forced through unverified.
Merge/dedupe (by normalized op list) → rank by (confirmed, rows covered,
confidence) → top-K = 3 hypothesis cards. Cards carry ONLY confirmed
hypotheses; everything else lands in `dropped_ideas`.

v1.1: with no Digital.jar configured, the SAME Python evaluator that
produced the original per-row verdict re-judges the patched temp — the
judge never changes mid-flow. `verified.runner` says which ran
(`"digital"` | `"evaluator"`).

## 6. Response (server → client)

```json
{
  "ok": true,
  "mode": "analysis",
  "cards": [
    { "rank": 1,
      "confidence": 0.9,
      "cluster_rows": [0, 1, 2, 3],
      "hint": { "suspect_region": "...", "suspect_signals": ["..."], "why": "..." },
      "verified": { "confirmed": true, "runner": "digital", "regressions": [] },
      "fix": { "ops": [ "..." ], "explanation_for_student": "...",
               "animation_script": [ "...validated, retest forced last..." ] } }
  ],
  "diagnosis_lines": ["deterministic, per cluster"],
  "dropped_ideas": [ { "cluster_rows": [ "..." ], "reason": "refuted|invalid_response|llm_error|patch_failed|beyond_top_k", "why": "..." } ],
  "usage": {"input_tokens": 0, "output_tokens": 0},
  "llm_calls": 0,
  "model": "..."
}
```

The client renders each card at `hint_level` 1 (hint only) and reveals
`fix` + animation at level 2 on the student's explicit "show me more" —
the structural half of the F13 spoiler guard. `mode:"lazy"` responses
carry `suggestions[]` (questions + build hints, with L2-library terms
marked for the blue hover-cards) and NO cards, NO fix ops. An analysis
run that delivers zero cards does not consume a daily use (the endpoint
says so in `notes`).

## 7. Card lifetime (1.3, explicit)

Hypothesis cards are keyed by `(session_id, filename)` and EXPIRE the moment
that filename is re-uploaded (`/api/circuit` replacing it) or the session is
cleared. Navigating tabs never clears them; a page refresh does. This is what
makes the telemetry pair `l3_circuit_re_uploaded → l3_now_passing`
well-defined. (Store lands with P2.0's sticky per-circuit result store.)

## 8. Telemetry events emitted by this flow

`l3_modeA_started(row_count, cluster_count)` · `l3_hypothesis_shown(rank,
confidence, verified)` · `l3_hint_level(rank, level)` — fires on every
ladder step, the weak-vs-strong scaffolding metric · `l3_fix_animation_played`
· `l3_lazy_detected` · `l3_modeA_refunded` — an analysis run delivered zero
cards · `l3_circuit_re_uploaded(dt)` · `l3_now_passing(row)` — logged through
the Layer-1 sink (`dlc/telemetry/sink.py`, `POST /api/telemetry`) from day one.
