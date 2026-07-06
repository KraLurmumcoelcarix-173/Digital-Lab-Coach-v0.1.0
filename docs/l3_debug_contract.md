# L3 `/api/llm/debug` — frozen sub-agent I/O contract (`l3.debug.v1`)

Status: FROZEN 2026-07-06; ops vocabulary ratified 2026-07-06. Changes bump
the version string; agents and executor validate against it.

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
2. Per-row run (fast Mode C) → failing rows. All pass → no-op response
   (`mode:"clear"`). Failing rows > 10 OR detective gross-check trips →
   `mode:"lazy"` (suggestion-only branch; consumes 0 daily uses).
3. CLUSTER failing rows by signature (Phase 0.5, ratified): the tuple of
   (mismatched output columns, exercised opcode/select values read from the
   row's inputs, overlap of top localizer suspects). Cap: 4 clusters; one
   sub-agent per cluster — never one per row.
4. Evidence per cluster: `/api/simulate` result for ≤ 2 REPRESENTATIVE rows
   (full per-net values), compact expected-vs-found for the rest;
   `localize()` per row, `merge_reports()` per cluster.

## 3. Sub-agent INPUT (one call per cluster)

```json
{
  "contract": "l3.debug.v1",
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
  "suspects": { "SuspectReport.to_dict()": "failing/passing outputs + ranked suspects with reasons" }
}
```

The agent reasons ONLY over these verified facts. It may request one nested
view per suspect subcircuit (coordinator-mediated `/api/subcircuit` fetch);
it never invents nets, widths, or values.

## 4. Sub-agent OUTPUT (frozen shape)

```json
{
  "contract": "l3.debug.v1",
  "hypothesis": {
    "summary": "carry-in tied high: the adder's c_i is driven by a Const whose omitted Value defaults to 1",
    "suspect_component_indices": [5, 16],
    "confidence": 0.9,
    "explanation_for_student": "wording passes the F13 spoiler-guard rules"
  },
  "fix": { "ops": [
    {"op": "change_attribute", "component_index": 16, "name": "Value", "value": 0}
  ] },
  "animation_script": [
    {"act": "diagnose_line", "text": "Rows 1-3 fail: Sum is always 1 too high."},
    {"act": "focus", "component_index": 5, "path": []},
    {"act": "mark_fix", "target": {"component_index": 16, "path": []},
     "label": "fixed: carry-in constant 1 -> 0 (was adding +1 to every sum)"},
    {"act": "retest"}
  ]
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
Merge/dedupe (by normalized op list) → rank by (confirmed, rows covered,
confidence) → top-K = 3 hypothesis cards.

## 6. Response (server → client)

```json
{
  "ok": true,
  "mode": "analysis",
  "cards": [
    { "rank": 1,
      "hypothesis": { "...": "as emitted, spoiler-guard applied" },
      "fix_ops": [ "..." ],
      "animation_script": [ "..." ],
      "verified": { "all_passed": true, "specs": [ "...per-row payload..." ] } }
  ],
  "diagnosis_lines": ["..."],
  "usage": {"input_tokens": 0, "output_tokens": 0},
  "model": "..."
}
```

`mode:"lazy"` responses carry `suggestions[]` (questions + build hints, with
L2-library terms marked for the blue hover-cards) and NO cards, NO fix ops.

## 7. Card lifetime (1.3, explicit)

Hypothesis cards are keyed by `(session_id, filename)` and EXPIRE the moment
that filename is re-uploaded (`/api/circuit` replacing it) or the session is
cleared. Navigating tabs never clears them; a page refresh does. This is what
makes the telemetry pair `l3_circuit_re_uploaded → l3_now_passing`
well-defined. (Store lands with P2.0's sticky per-circuit result store.)

## 8. Telemetry events emitted by this flow

`l3_modeA_started(row_count, cluster_count)` · `l3_hypothesis_shown(rank,
confidence, verified)` · `l3_fix_animation_played` · `l3_lazy_detected` ·
`l3_circuit_re_uploaded(dt)` · `l3_now_passing(row)` — logged through the
Layer-1 sink (`dlc/telemetry/sink.py`, `POST /api/telemetry`) from day one.
