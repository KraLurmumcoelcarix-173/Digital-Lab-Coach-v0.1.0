# Layer 1 Test Notes

Manual test snippets for Layer 1 deterministic checkers (F5-F9).
Run from repo root.

Last updated: 2026/5/25

---

## Quick reference

```bash
uv run pytest tests/test_L1_clean_sweep.py
uv run pytest tests/test_analyzer_wire_completeness.py  
uv run pytest tests/test_analyzer_bit_widths.py        
uv run pytest tests/test_analyzer_combinational_loops.py                        
```

---


### Recursive deep-check (whole subcircuit tree)

`check_all_l1` runs the L1 checkers only on the top circuit.
`check_all_l1_deep` recurses into every resolved subcircuit child and
prefixes each issue's title with the file breadcrumb. Use deep when you 
want a single report covering the entire .dig hierarchy.

```bash
uv run python -c "
from dlc.parser.dig_parser import parse_dig_file
from dlc.analyzer import check_all_l1_deep
TARGET = 'data/sample_circuits/tier2_buggy/L1_deep_check/L1_deep_top.dig' # your .dig file
for i in check_all_l1_deep(parse_dig_file(TARGET)).issues:
    print(f'  [{i.severity.value}] {i.title}')
    print(f'    {i.message}')
"
```

This runs F5 + F6 + F7 + F8 + F9 bug collectors in one pass with netlist/facts built
once. Useful as a single-shot health check.


## Function 5 — Wire-completeness checker

### What it produces

An `IssueCollection` of `Issue` records. Each `Issue` carries:

- `kind` — stable ID: `dangling_input`, `multi_driver`, `missing_subcircuit`,
  `unused_top_output`, `isolated_component`, `empty_tunnel`
- `severity` — `error` | `warning` | `info` (info reserved for F10+)
- `title` / `message` / `suggested_fix` — student-facing strings, no
  internal jargon like net IDs
- `component_indices` / `location` — for UI highlighting
- `net_id` — structured field for LLM / UI consumption; not surfaced
  in `message` text

### Severity meaning

| Severity | When to use | Examples |
|---|---|---|
| `error` | Circuit will not work | dangling_input, multi_driver, missing_subcircuit, unused_top_output |
| `warning` | Probably wrong; rest of circuit may still work | isolated_component, empty_tunnel |
| `info` | Stylistic / didactic | reserved for F10 simplification hints |

### How to test manually

JSON dump (the shape the L3 prompt builder will consume):

```bash
uv run python -c "
from dlc.parser.dig_parser import parse_dig_file
from dlc.analyzer.wire_completeness import check_wire_completeness
print(check_wire_completeness(parse_dig_file(
    'data/sample_circuits/tier1_buggy/dangling_input.dig'  # your .dig file
)).to_json(indent=2))
"
```

### Expected output on each tier1_buggy sample

| Sample | Expected Issue kind | Severity | Count |
|---|---|:-:|:-:|
| `dangling_input.dig` | `dangling_input` | error | 1 |
| `multi_driver.dig` | `multi_driver` | error | 1 |
| `unused_top_output.dig` | `unused_top_output` | error | 1 |
| `isolated_component.dig` | `isolated_component` | warning | 1 |
| `empty_tunnel.dig` | `empty_tunnel` | warning | 1 |

### Key tests — what each guarantees

| Test | If it fails... |
|---|---|
| `test_dangling_input_check_surfaces_one_issue_on_buggy_sample` | F5 stopped wrapping F3's dangling_input BugFact |
| `test_missing_subcircuit_check_uses_inmemory_circuit` | The missing-subcircuit path broke (A.3 will add a real fixture) |
| `test_unused_top_output_surfaces_one_issue_not_dangling` | Out-pin disambiguation regressed; Y_unused leaking as dangling_input |
| `test_isolated_component_surfaces_one_issue_not_dangling` | Orphan AND's singleton pins leaking as dangling_input |
| `test_empty_tunnel_surfaces_only_lonely_tunnel_not_wired_one` | Tunnels with a wire being over-flagged |
| `test_dangling_input_issue_carries_net_id_for_llm_consumption` | `net_id` structured field dropped from Issue |

---

## Function 6 — Bit-width consistency checker

### What it produces

Two Issue kinds:
- `width_conflict` 
- `width_mismatch`

### Expected output

- `tier1_buggy/width_mismatch.dig`: at least 1 `width_mismatch` issue (error).
- `tier1_buggy/width_conflict.dig`: at least 1 `width_conflict` issue (error).

## Function 7 — Combinational-loop checker

### What it produces

One Issue kind:
- `combinational_loop` — a cycle in the signal-flow graph that contains
  no clocked element and no subcircuit instance whose child contains
  one.

### Expected output

- `tier1_buggy/combinational_loop.dig`: at least 1 `combinational_loop` error.

## Function 8 — Subcircuit-interface conformance

### What it produces

One Issue kind:
- `dangling_subcircuit_input` — a parent circuit references a
  subcircuit but doesn't wire one of the subcircuit's declared inputs.

### Expected output

- `tier2_buggy/dangling_subcircuit_input.dig`: 1 `dangling_subcircuit_input` error

## Function 9 — Sequential/timing checker

*(TBD)*