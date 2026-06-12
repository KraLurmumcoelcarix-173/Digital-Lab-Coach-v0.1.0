"""
Layer 2 benchmark configuration.

This file *is* the benchmark design — edit it, then run
``python -m dlc.evaluator.benchmark`` (main run) or
``python -m dlc.evaluator.grader_selection`` (pick the grader).

IRB: all outputs are written OUTSIDE the repo (see OUTPUT_DIR). 
"""

import os
from pathlib import Path

BENCH_MODELS = [
    "claude-haiku-4-5-20251001",
    "claude-sonnet-4-6",
    "claude-opus-4-8",
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-5",
]

BENCH_GRADER = "claude-opus-4-8"

MODEL_PRICES = {
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-sonnet-4-6":         (3.0, 15.0),
    "claude-opus-4-8":           (5.0, 25.0),
    "gpt-4o-mini":               (0.15, 0.60),
    "gpt-4o":                    (2.50, 10.0),
    "gpt-5":                     (1.25, 10.0),
}


# Each entry is a 3-tuple: (path, good_goal, wrong_goal).
#   good_goal  = correct one-sentence description (the "goal" condition).
#   wrong_goal = deliberately MISMATCHED goal (the "wrong_goal"
#                condition — measures whether the model catches it
#                instead of rubber-stamping).
# Paths may point outside the repo (course/lab circuits stay out of
# git); absolute paths are fine.
BENCH_CIRCUITS = [
    ("data/sample_circuits/tier3_realistic/bool_unit.dig",
     "A boolean logic unit: outputs the bitwise AND, OR, or XOR of A and B, chosen by an op-select input.",
     "A 4-bit ripple-carry adder: sums A and B and raises a carry-out flag on overflow.",),
    ("data/sample_circuits/tier3_realistic/pipelined_adder_correct.dig",
     "A two-stage pipelined adder: registers A and B on one clock edge and presents their sum on the next.",
     "A combinational multiplier: presents the product of A and B in the same cycle with no clocking.",),
    ("data/sample_circuits/tier3_realistic/tier3_calculator.dig",
     "A calculator: computes A op B (add/subtract/and/or) selected by a control input, driving Result.",
     "A barrel shifter: shifts A left or right by B bit positions selected by a direction input.",),
    ("data/sample_circuits/tier3_realistic/tier3_latched_display.dig",
     "Latches an input value on a load signal and drives the latched value to the display output.",
     "A free-running counter: increments the displayed value on every clock edge regardless of inputs.",),
    # ("C:/path/outside/repo/your_circuit_5.dig", "good goal ...", "wrong goal ...",),
    # ("C:/path/outside/repo/your_circuit_6.dig", "good goal ...", "wrong goal ...",),
]


REF_CIRCUITS = BENCH_CIRCUITS[:3]

GOAL_CONDITIONS = ["goal", "wrong_goal", "no_goal"]  
RUNS_PER_CELL = 3                     


TEST_SUMMARY = None


OUTPUT_DIR = Path(os.environ.get("DLC_BENCH_OUT", str(Path.home() / "dlc_benchmark_out")))


def total_cells() -> int:
    return len(BENCH_MODELS) * len(BENCH_CIRCUITS) * len(GOAL_CONDITIONS) * RUNS_PER_CELL
