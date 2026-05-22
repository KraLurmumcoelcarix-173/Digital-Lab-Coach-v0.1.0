# Testing Notes

Last updated: 2026/5/21

---

## Quick reference

From repo root using bash (These are all sample circuit tests, if you want to manually test a new .dig refer to 
" How to test manually" in each function specified below):

```bash
uv run pytest                              # Run all tests
uv run pytest -v                           # Show each test name
uv run pytest tests/test_netlist.py        # Run the test in one test file
uv run pytest -k subcircuit                # Name-match filter
uv run pytest tests/test_netlist.py::test_buggy_multi_driver_flags_one_net_with_two_drivers
                                           # Run one specific test
```
---

## Function 1 — Parser (`test_parser.py`)

### What F1 produces

A `Circuit` object containing:
- `components`: list of `Component` (element_name, position, attributes dict, label)
- `wires`: list of `Wire` (p1, p2 Position pairs)
- `subcircuits`: list of resolved `SubcircuitReference` (recursively loaded child Circuits)
- `format_version`, `source_path`

### How to test manually

Baisc info:

```python
uv run python -c "
from dlc.parser.dig_parser import parse_dig_file
c = parse_dig_file('data/sample_circuits/tier3_realistic/tier3_calculator.dig') # your .dig
print(f'Counts: components={len(c.components)} wires={len(c.wires)} subcircuits={len(c.subcircuits)}')
print()
print('Inputs:')
for inp in c.inputs():
    print(f'  {inp.label} ({inp.bit_width()}-bit)')
print('Outputs:')
for out in c.outputs():
    print(f'  {out.label} ({out.bit_width()}-bit)')
print()
print('All components (use the index for c.components[i] in the pin-geometry test):')
for i, comp in enumerate(c.components):
    attrs = {k: v for k, v in comp.attributes.items() if k != 'Label'}
    print(f'  [{i}] {comp.element_name} @ ({comp.position.x},{comp.position.y}) label={comp.label} attrs={attrs}')
"
```

---

## Function 2 — Netlist + signal-flow graph

Split across three test files: `test_pin_geometry.py`, `test_netlist.py`, `test_graph.py`.

### F2: Pin geometry (`test_pin_geometry.py`)

#### What it produces

For each Component, the absolute pin positions and directions (after applying rotation). This is the table that lets F2 know "this AND gate's `in1` is at coord (X, Y) on the canvas."

#### How to test manually

```python
uv run python -c "
from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.pin_geometry import absolute_pin_positions
c = parse_dig_file('data/sample_circuits/tier3_realistic/tier3_calculator.dig') # your .dig
for pos, spec in absolute_pin_positions(c.components[7]):  # change i in c.components[i] to view geometry for different components
    print(spec.name, (pos.x, pos.y), spec.direction)
"
```

### F2: Netlist construction (`test_netlist.py`)

#### What it produces

A `NetList` where each `Net` has:
- `coords`: every (x, y) point that's electrically the same signal
- `pins`: every pin (component_index, pin_name, direction) attached to this net
- `tunnel_names`: any Tunnel NetNames merging into this net
- `drivers()` / `sinks()` helpers

Plus `summary()`: `"NetList: N nets, M driven, K undriven-with-pins, J multi-driver"`.

#### How to test manually

```python
uv run python -c "
from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.netlist import build_netlist
c = parse_dig_file('data/sample_circuits/tier3_realistic/tier3_calculator.dig') # your .dig
nl = build_netlist(c)
print(nl.summary())
print()
print('All nets:')
for net in nl.nets:
    flags = []
    if net.pins and not net.drivers():
        flags.append('DANGLING')
    if len(net.drivers()) > 1:
        flags.append('MULTI-DRIVER')
    flag_str = ' [' + ','.join(flags) + ']' if flags else ''
    pins = [(c.components[p.component_index].element_name, p.pin_name, p.direction) for p in net.pins]
    tnames = sorted(net.tunnel_names) if net.tunnel_names else None
    print(f'  net {net.net_id}{flag_str}: coords={len(net.coords)}, pins={pins}, tunnels={tnames}')
"
```

### F2: Signal-flow graph + reachability (`test_graph.py`)

#### What it produces

A `networkx.MultiDiGraph`:
- Nodes = component indices (with `element_name` and `component` attrs).
- Edges = directed driver→sink within each net (with `driver_pin`, `sink_pin`, `net_id` attrs).

Plus helpers: `input_component_indices`, `output_component_indices`, `reachable_outputs_from_inputs`.

#### How to test manually

```python
uv run python -c "
from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.netlist import build_netlist
from dlc.parser.graph import build_signal_graph, reachable_outputs_from_inputs
c = parse_dig_file('data/sample_circuits/tier3_realistic/tier3_calculator.dig') # your .dig
nl = build_netlist(c)
g = build_signal_graph(c, nl)
reach = reachable_outputs_from_inputs(c, g)
for in_idx, outs in reach.items():
    print(c.components[in_idx].label, '->',
          [c.components[i].label for i in outs])
"
```


Text dumb visulization: 

```python
uv run python -c "
from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.netlist import build_netlist
from dlc.parser.graph import build_signal_graph
c = parse_dig_file('data/sample_circuits/tier3_realistic/tier3_calculator.dig') # your .dig
nl = build_netlist(c)
g = build_signal_graph(c, nl)
print(f'Nodes: {g.number_of_nodes()}, Edges: {g.number_of_edges()}')
print()
print('Edges (driver -> sink, with pin names and net id):')
for u, v, data in g.edges(data=True):
    src = c.components[u]
    dst = c.components[v]
    src_lbl = src.label or src.element_name
    dst_lbl = dst.label or dst.element_name
    driver_pin = data['driver_pin']
    sink_pin = data['sink_pin']
    net_id = data['net_id']
    print(f'  [{u}] {src_lbl}.{driver_pin} -> [{v}] {dst_lbl}.{sink_pin}  (net {net_id})')
"
```


Visulization: 

```python
uv run --with matplotlib python -c "
from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.netlist import build_netlist
from dlc.parser.graph import build_signal_graph
import networkx as nx, matplotlib.pyplot as plt

c = parse_dig_file('data/sample_circuits/tier3_realistic/tier3_calculator.dig') # your .dig
nl = build_netlist(c)
g = build_signal_graph(c, nl)

try:
    topo = list(nx.topological_sort(g))
except nx.NetworkXUnfeasible:
    topo = list(g.nodes())
in_idxs = [i for i, comp in enumerate(c.components) if comp.element_name == 'In']
out_idxs = [i for i, comp in enumerate(c.components) if comp.element_name == 'Out']
layer = {i: 0 for i in in_idxs}
for node in topo:
    if node in layer: continue
    preds = list(g.predecessors(node))
    layer[node] = max((layer.get(p, 0) for p in preds), default=0) + 1
max_l = max(layer.values()) if layer else 0
for i in out_idxs: layer[i] = max_l + 1

# Hide isolated nodes (Testcase, unused tunnels) 
keep = [n for n in g.nodes() if g.degree(n) > 0]
gs = g.subgraph(keep).copy()
for n in gs.nodes(): gs.nodes[n]['subset'] = layer.get(n, 0)

def col(comp):
    e = comp.element_name
    if e == 'In': return '#90caf9'
    if e == 'Out': return '#ffcc80'
    if e in ('And', 'Or', 'XOr', 'Not', 'NAnd', 'NOr', 'XNOr'): return '#a5d6a7'
    if e == 'Multiplexer': return '#ce93d8'
    if e == 'Splitter': return '#fff59d'
    if e.endswith('.dig'): return '#ef9a9a'
    if e == 'Comparator': return '#80cbc4'
    if e == 'Add': return '#ffab91'
    if e in ('Tunnel', 'Const', 'Ground', 'VDD', 'Clock'): return '#e0e0e0'
    return '#bdbdbd'

pos = nx.multipartite_layout(gs, subset_key='subset')
colors = [col(c.components[n]) for n in gs.nodes()]
labels = {n: (c.components[n].label or c.components[n].element_name) + f'\n[{n}]' for n in gs.nodes()}

plt.figure(figsize=(20, 12))
nx.draw(gs, pos, labels=labels, node_color=colors, node_size=2200,
        font_size=9, font_weight='bold', arrows=True, arrowsize=18,
        edge_color='#555555', width=1.3,
        connectionstyle='arc3,rad=0.08')
plt.title('tier3_calculator.dig — signal-flow graph', fontsize=15)    # Feel free to modify here
plt.axis('off')             
plt.tight_layout()
plt.savefig('graph.png', dpi=200, bbox_inches='tight', facecolor='white')  # Feel free to modify here
print('Saved to graph.png')
"
```

---

## Function 3 — Structural fact extractor

### How to test manually

Per net width:

```python
uv run python -c "
from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.netlist import build_netlist
from dlc.facts.net_width import infer_net_widths
c = parse_dig_file('data/sample_circuits/tier3_realistic/tier3_calculator.dig')  # your .dig
nl = build_netlist(c)
per_net, conflicts = infer_net_widths(c, nl)
for nid, info in per_net.items():
    print(f'  net {nid}: width={info.width} source={info.source}')
print(f'conflicts: {conflicts}') "
```


Fact extractor:

```python
uv run python -c "
from dlc.parser.dig_parser import parse_dig_file
from dlc.facts.extractor import extract_facts
c = parse_dig_file('data/sample_circuits/tier3_realistic/pipelined_adder_correct.dig') # your .dig
f = extract_facts(c)
print(f.header); print()
print('I/O:'); [print(f'  {io.direction.upper()} {io.label} {io.bit_width}-bit @ {io.position}') for io in f.inputs + f.outputs]
print('\nSubcircuits:'); [print(f'  {s.reference}: in={s.child_inputs} out={s.child_outputs}') for s in f.subcircuits]
print('\nNets:'); [print(f'  net {n.net_id}: {n.pin_count}p ({n.driver_count}d/{n.sink_count}s) {n.bit_width}-bit anomalies={n.anomalies}') for n in f.nets]
print('\nComponents (graph view):'); [print(f'  [{c.index}] {c.element_name}({c.label}) preds={c.predecessors} succs={c.successors}') for c in f.components]
print('\nBugs:'); [print(f'  [{b.kind}] {b.description}') for b in f.bugs]
"
```

Json Bundle:

```python
uv run python -c "
from dlc.parser.dig_parser import parse_dig_file
from dlc.facts.extractor import extract_facts
c = parse_dig_file('data/sample_circuits/tier3_realistic/tier3_calculator.dig')  # your .dig
print(extract_facts(c).to_json(indent=2))
" | less
```

## Function 4 — Test result parser

F4 turns each `.dig`'s embedded Testcase data into structured test specs,
parses Digital's CLI text output, and (optionally) re-runs Digital
one row at a time to identify which specific rows fail. Tests across
four files:

- `tests/test_testing_spec.py` — testData → `TestSpec`.
- `tests/test_testing_results.py` — CLI text → `TestRunResults`.
- `tests/test_testing_run.py` — `TestSpec × TestRunResults` join.
- `tests/test_testing_runner.py` — Digital subprocess per-row runner.
- `tests/test_testing_config.py` — jar location config + file picker.

#### How to test manually

Set up the jar path:

```python
uv run python -c "
from dlc.testing.config import (
    set_digital_jar_path, get_configured_jar, prompt_for_jar_path,
)
TARGET_JAR = r'C:\Users\...\Digital.jar'  # your jar
set_digital_jar_path(TARGET_JAR)
print(f'Saved: {get_configured_jar()}')
"
```

Rough overview:

```python
uv run python -c "
from dlc.parser.dig_parser import parse_dig_file
from dlc.testing.spec import extract_test_specs, match_variables_to_io

TARGET_DIG = 'data/sample_circuits/tier3_realistic/tier3_calculator.dig'  # your .dig

circuit = parse_dig_file(TARGET_DIG)
for spec in extract_test_specs(circuit):
    print(f'Testcase \"{spec.name}\"')
    print(f'  headers: {spec.headers}')
    print(f'  rows: {spec.well_formed_row_count()}/{spec.row_count()} well-formed')
    print(f'  has_unexpanded_loops: {spec.has_unexpanded_loops}')
    bindings = match_variables_to_io(spec.headers, circuit)
    print(f'  bindings:')
    for var, b in bindings.items():
        bw = f'{b.bit_width}-bit' if b.bit_width else 'n/a'
        print(f'    {var:14s} → {b.role:7s} {bw}')
    if spec.rows:
        print(f'  first 3 rows:')
        for row in spec.rows[:3]:
            tokens = [f'{t.raw}={t.value if t.kind == \"int\" else t.kind}' for t in row.values]
            print(f'    [{row.line_index}] {\" \".join(tokens)}')
"
```

### Function 4: per-row vs overall — two manual test modes

DLC offers two ways to check a circuit's testcases. Use whichever
the situation calls for.

#### Mode A — overall pass/fail (fast)

One Digital invocation on the unmodified .dig. Returns the testcase's
overall pass/fail and (on failure) a percentage.

```python
uv run python -c "
from dlc.testing.runner import find_digital_jar, run_digital_cli
from dlc.testing.results import parse_cli_output

TARGET_DIG = 'data/sample_circuits/tier3_realistic/tier3_calculator.dig'  # your .dig

jar = find_digital_jar()
code, out = run_digital_cli(TARGET_DIG, jar)
run = parse_cli_output(out)
for tc in run.testcases:
    pct = f' ({tc.fail_pct}%)' if tc.fail_pct is not None else ''
    print(f'  [{tc.status}] {tc.name}{pct}')
"
```

Wallclock: 1–3 seconds total.

#### Mode B — cumulative per-row (precise, N subprocesses)

One Digital invocation per row, with each prefix containing rows 0..K
so state accumulates correctly. Returns each row's individual pass/fail.

```python
uv run python -c "
from dlc.parser.dig_parser import parse_dig_file
from dlc.testing.spec import extract_test_specs
from dlc.testing.runner import per_row_run, find_digital_jar

TARGET_DIG = 'data/sample_circuits/tier3_realistic/alu.dig'  # your .dig

circuit = parse_dig_file(TARGET_DIG)
spec = extract_test_specs(circuit)[0]
jar = find_digital_jar()
print(f'Running {spec.row_count()} rows of testcase \"{spec.name}\" cumulatively...')
per_row = per_row_run(spec, TARGET_DIG, jar_path=jar)
print()
print(f'{\"row\":>3}  {\"status\":<6}  row text')
print('-' * 60)
for r in per_row:
    raw = spec.rows[r.row_index].raw
    marker = {'passed':'PASS', 'failed':'FAIL', 'no_run':'SKIP', 'error':'ERR'}[r.status]
    print(f'{r.row_index:>3}  {marker:<6}  {raw}')
    if r.error_message:
        print(f'      ↳ {r.error_message}')
"
```

Wallclock: roughly N × JVM-startup, roughly N seconds. Takes minutes for files with more than 100 tests.

#### When to use which

| Need | Use | Wallclock |
|---|---|:-:|
| Combinational circuit, quick check | Mode A | fast |
| Stateful circuit, debugging a regression | Mode B | slower but correct and feeds per row result to LLM |

TBD: Eventually the Layer 3 UI will surface this as a checkbox: "Get per-row
diagnostics (slower)". For now, call the right function directly.
---

## When you add a new test

