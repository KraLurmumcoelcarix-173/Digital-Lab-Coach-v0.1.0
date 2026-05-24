# Function Plan (F1 – F21)

## Core analyzer (no LLM)

| # | Name | Status |
|---|---|:-:|
| F1 | `.dig` parser | Done |
| F2 | Circuit netlist + signal-flow graph | Done |
| F3 | Structural fact extractor | Done |
| F4 | Test-result parser | Done |

## Layer 1 deterministic checkers

| # | Name | Status |
|---|---|:-:|
| F5 | Wire completeness checker | Done |
| F6 | Bit-width consistency checker | TBD |
| F7 | Combinational-loop checker | TBD |
| F8 | Interface conformance checker | TBD |
| F9 | Timing / sequential checker (register-clock-Q) | TBD |
| F10 | K-map / Boolean simplification PRO | TBD |

## Layer 2 LLM conceptual explanation

| # | Name | Status |
|---|---|:-:|
| F11 | LLM client wrapper (SDK, prompt versioning, cost tracking etc.) | TBD |
| F12 | Conceptual explanation generator | TBD |
| F13 | Prompt-leakage guard | TBD |

## Layer 3 LLM strategic debugging

| # | Name | Status |
|---|---|:-:|
| F14 | Failed-test interpreter | TBD |
| F15 | Test-writing coach | TBD |
| F16 | Signal-flow narrator | TBD |

## Research infrastructure

| # | Name | Status |
|---|---|:-:|
| F17 | UI design | TBD |
| F18 | Ablation condition controller | TBD |
| F19 | Telemetry logger | TBD |
| F20 | Digital source-code dig (Path-3 plugin viability) | TBD |
| F21 | Evaluation harness (30-bug benchmark, rubric scoring) | TBD |
| F22 | CLI interface | TBD |
