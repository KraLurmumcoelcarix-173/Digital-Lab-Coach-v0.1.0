# Layer 1 Test Notes

Manual test snippets for Layer 1 deterministic checkers (F5-F9).
Run from repo root.

Last updated: 2026/5/25

---

## Quick reference

```bash
uv run pytest tests/test_layer_1.py                                
```

---

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

```bash
uv run python -c "
from dlc.parser.dig_parser import parse_dig_file
from dlc.analyzer.wire_completeness import check_wire_completeness
TARGET = 'data/sample_circuits/30_buggy_benchmark/bug2_wrong_floating_pipeline_output/Wrong_floating_pipeline_output.dig'  # your .dig
issues = check_wire_completeness(parse_dig_file(TARGET))
print(issues.summary())
for i in issues.issues:
    print(f'  [{i.severity.value}] {i.title}')
    print(f'    {i.message}')
    if i.suggested_fix:
        print(f'    fix: {i.suggested_fix}')
    if i.location:
        print(f'    @ {i.location}')
    print(f'    components: {i.component_indices}  net_id: {i.net_id}')
"
```

JSON dump (the shape the L3 prompt builder will consume):

```bash
uv run python -c "
from dlc.parser.dig_parser import parse_dig_file
from dlc.analyzer.wire_completeness import check_wire_completeness
print(check_wire_completeness(parse_dig_file(
    'data/sample_circuits/tier1_buggy/dangling_input.dig'
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


### Clean-sweep sanity check

Every sample in `tier1_minimal`, `tier2_structured`, `tier3_realistic`,
and the 30-bug benchmark's semantic-only bugs (bug1, bug3, bug4, bug5) must
produce **0 issues** from F5. 

```bash
uv run python -c "
import glob
from dlc.parser.dig_parser import parse_dig_file
from dlc.analyzer.wire_completeness import check_wire_completeness
for tier in ('tier1_minimal', 'tier2_structured', 'tier3_realistic'):
    for f in sorted(glob.glob(f'data/sample_circuits/{tier}/*.dig')):
        n = len(check_wire_completeness(parse_dig_file(f)).issues)
        marker = '  <-- UNEXPECTED' if n else ' clean'
        print(f'  {f}: {n}{marker}')
"
```

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

*(TBD )*

## Function 7 — Combinational loop checker

*(TBD)*

## Function 8 — Interface conformance checker

*(TBD)*

## Function 9 — Sequential/timing checker

*(TBD)*