# Digital Lab Coach (DLC)

A hybrid deterministic-checker + LLM feedback tool for student debugging
in Digital circuit simulator labs. Path 1 (external
companion tool) prototype. 

## Status

Active development.

## File Layout

| Path | Role | Status |
|---|---|:-:|
| `dlc/parser/` | Reads `.dig` XML into structured Python objects: components, wires, nets, signal-flow graph. 
| `dlc/facts/` | Extracts a JSON-serializable bundle of facts the LLM and deterministic checkers consume — inventory, per-net widths, per-component topology, structural bug list.
| `dlc/testing/` | Reads each Testcase's embedded test rows out of the `.dig`, parses Digital's CLI output, and optionally runs Digital one row at a time to pinpoint which specific rows fail. 
| `dlc/analyzer/` | Deterministic checkers — wire completeness, bit widths, combinational loops, interface conformance, sequential timing. 
| `dlc/llm/` | LLM client wrapper and versioned prompts for conceptual explanation (Layer 2) and strategic debugging (Layer 3). 
| `dlc/evaluator/` | Benchmark harness that scores feedback quality against the 30-bug circuit set. 
| `dlc/telemetry/` | Per-interaction logging to a local SQLite database. 
| `dlc/cli/` | Command-line entrypoint that wires the layers together for student use. 
| `prompts/` | Versioned LLM prompt templates — one file per prompt variant, consumed by `dlc/llm/`. 
| `configs/` | Per-lab YAML configs (expected I/Os, handout context). 
| `data/sample_circuits/` | Test fixtures — public sample circuits created by author. 
| `docs/` | Architecture notes, design decisions, dev log, dev debug guide. 
| `tests/` | pytest unit tests, one file per source module. 

## Optional: Digital.jar for per-row test verification

DLC's structural analysis works on any `.dig` file with no extra setup.

**For per-row pass/fail diagnostics and failing test analysis**, the tool 
runs Digital's CLI as a subprocess, so it needs to know where your `Digital.jar` is.

### Setting it up
Download Digital from
<https://github.com/hneemann/Digital/releases>, extract anywhere
(no install needed), and let the first-run dialog catch your jar.

If you'd rather configure it manually:

```bash
# Option A 
uv run python -c "from dlc.testing.config import set_digital_jar_path; set_digital_jar_path(r'PATH_TO_YOUR_Digital.jar')"

# Option B 
# macOS / Linux
export DIGITAL_JAR=/path_to_Digital/Digital.jar
# Windows PowerShell
$env:DIGITAL_JAR = "C:\path_to_Digital\Digital.jar"
```

## License

GPL-3.0. See LICENSE.

## Upstream

Built to read .dig files produced by [Digital](https://github.com/hneemann/Digital),
an open-source educational circuit simulator (GPL-3.0).