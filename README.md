# Digital Lab Coach (DLC)

A hybrid deterministic-checker + LLM feedback tool for student debugging
in Digital circuit simulator labs. Path 1 (external
companion tool) prototype.

## Status

Early development. Not ready for use.

## Digital.jar for per-row test verification

DLC's structural analysis (F1–F3) works on any `.dig` file without
needing Digital itself. **For per-row pass/fail diagnostics**,
the tool invokes Digital's CLI test mode as a subprocess, so you need
Digital.jar locally if you want that feature.

### Installing Digital

1. Download the latest release zip from
   <https://github.com/hneemann/Digital/releases>.
2. Extract it; the JAR lives at `Digital/Digital.jar`.
3. Tell DLC where it is via the `DIGITAL_JAR` environment variable:

   ```bash
   # macOS / Linux
   export DIGITAL_JAR=/path_to_Digital/Digital.jar
   
   # Windows (PowerShell)
   $env:DIGITAL_JAR = "C:\path_to_Digital\Digital.jar"

## License

GPL-3.0. See LICENSE.

## Upstream

Built to read .dig files produced by [Digital](https://github.com/hneemann/Digital),
an open-source educational circuit simulator (GPL-3.0).