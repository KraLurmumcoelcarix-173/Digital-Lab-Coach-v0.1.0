"""
Deterministic circuit value evaluator (Layer-1 UI signal-flow).
"""

from dlc.sim.simulator import (
    SimResult,
    simulate,
    simulate_sequential,
    inputs_for_row,
)


__all__ = ["SimResult", "simulate", "simulate_sequential", "inputs_for_row"]
