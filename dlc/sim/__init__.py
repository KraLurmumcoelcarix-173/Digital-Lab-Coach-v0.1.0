"""Deterministic circuit value evaluator (Layer-1 UI signal-flow).

Digital's `CLI test -verbose` only reports a testcase's declared I/O columns,
never interior net values. To color interior wires with the value they carry
when a test row is clicked, we compute those values ourselves.

`simulate()` runs a worklist fixpoint for ONE input assignment: it seeds the
top-level inputs (and constants) with a row's values and propagates through
every modeled component — gates (with inverter bubbles), mux, decoder,
priority encoder, splitter (bit ranges), adder (carry), comparator, ROM,
barrel shifter, bit extender — recursing into resolved subcircuits.
`simulate_sequential()` replays a testcase's rows from reset, latching
registers on clock-edge rows with HIERARCHICAL (path-keyed) state, so
registers nested inside subcircuits (a register file's 32 registers) persist
across rows. Anything genuinely unmodeled — and, by design, the clock net
itself — stays *unresolved*: the UI shows no value there rather than a wrong
one.

This module is additive: it never touches the Layer-1 checkers.
"""

from dlc.sim.simulator import (
    SimResult,
    simulate,
    simulate_sequential,
    inputs_for_row,
)


__all__ = ["SimResult", "simulate", "simulate_sequential", "inputs_for_row"]
