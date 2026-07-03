# Layer 1 Test Notes

Manual test snippets for Layer 1 deterministic checkers (F5-F9).
Run from repo root. The Layer-1 value evaluator (`dlc/sim/simulator.py`,
exposed via `/api/simulate`) is a newer Layer-1 surface not yet covered
here — add snippets once its manual-test flow stabilizes.

Last updated: 2026/7/03

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

Nested issues additionally carry:
- `scope` — the breadcrumb string (`"alu.dig > add-sub.dig"`); `None`
  for top-level issues.
- `component_indices` — remapped to the TOP-level subcircuit-instance
  component, so the web overlay highlights the right node on the top
  graph.
- `child_component_indices` — the original indices, valid inside the
  child circuit (for L3 / drill-down use).

The web UI (`/api/circuit`, `/api/llm/explain`) runs the DEEP variant,
so subcircuit bugs alarm and gate Layer 2 like top-level ones.

```bash
uv run python -c "
from dlc.parser.dig_parser import parse_dig_file
from dlc.analyzer import check_all_l1_deep
TARGET = 'data/sample_circuits/tier3_realistic/tier3_calculator.dig' # your .dig file
for i in check_all_l1_deep(parse_dig_file(TARGET)).issues:
    print(f'  [{i.severity.value}] {i.title}')
    print(f'    {i.message}')
"
```

This runs F5 + F6 + F7 + F8 + F9 bug collectors in one pass for a single .dig file 
with netlist/facts built once. Useful as a single-shot health check.


### tier1 + tier 2 bug collector check

```bash
uv run python -c "
import glob, os
from dlc.parser.dig_parser import parse_dig_file
from dlc.analyzer import check_all_l1_deep

for tier in ('tier1_bug', 'tier2_bug'):
    print(f'=== {tier} ===')
    for f in sorted(glob.glob(f'data/sample_circuits/{tier}/**/*.dig', recursive=True)):
        issues = check_all_l1_deep(parse_dig_file(f))
        name = os.path.relpath(f, f'data/sample_circuits/{tier}')
        marker = 'clean' if not issues.issues else f'{len(issues.issues)} issue(s)'
        print(f'  {name:45s} {marker}')
        for i in issues.issues:
            print(f'      [{i.severity.value}] {i.kind}: {i.title}')
            print(f'        {i.message}')
            if i.suggested_fix:
                print(f'        fix: {i.suggested_fix}')
    print()
"
```

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
    'data/sample_circuits/tier1_bug/dangling_input.dig'  # your .dig file
)).to_json(indent=2))
"
```

### Expected output on each tier1_bug sample

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
| `test_dangling_input_check_surfaces_one_issue_on_bug_sample` | F5 stopped wrapping F3's dangling_input BugFact |
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

- `tier1_bug/width_mismatch.dig`: at least 1 `width_mismatch` issue (error).
- `tier1_bug/width_conflict.dig`: at least 1 `width_conflict` issue (error).

## Function 7 — Combinational-loop checker

### What it produces

One Issue kind:
- `combinational_loop` — a cycle in the signal-flow graph that contains
  no clocked element and no subcircuit instance whose child contains
  one.

### Expected output

- `tier1_bug/combinational_loop.dig`: at least 1 `combinational_loop` error.

## Function 8 — Subcircuit-interface conformance

### What it produces

One Issue kind:
- `dangling_subcircuit_input` — a parent circuit references a
  subcircuit but doesn't wire one of the subcircuit's declared inputs.

### Expected output

- `tier2_bug/dangling_subcircuit_input.dig`: 1 `dangling_subcircuit_input` error

## Function 9 — Sequential/timing checker

### What it produces

Four Issue kinds:
- `register_no_clock` (error) — a Register whose `C` pin is undriven.
- `floating_register_en` (error) — a Register whose `en` pin is undriven.
- `orphan_clock` (warning) — a Clock component driving nothing.
- `empty_rom` (warning) — a ROM whose `Data` attribute is empty.

### Expected output

- `tier1_minimal/register_test.dig`: clean.
- A Register with unwired `C` or `en`: 1 error of the matching kind.

### How to test manually

```bash
uv run pytest tests/test_analyzer_sequential.py
```