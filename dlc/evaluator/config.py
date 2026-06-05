
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
    "claude-opus-4-8":           (15.0, 75.0),
    "gpt-4o-mini":               (0.15, 0.60),
    "gpt-4o":                    (2.50, 10.0),
    "gpt-5":                     (1.25, 10.0),
}


BENCH_CIRCUITS = [
    ("data/sample_circuits/tier3_realistic/bool_unit.dig",
     "A boolean logic unit: outputs the bitwise AND, OR, or XOR of A and B, chosen by an op-select input."),
    ("data/sample_circuits/tier3_realistic/pipelined_adder_correct.dig",
     "A two-stage pipelined adder: registers A and B on one clock edge and presents their sum on the next."),
    ("data/sample_circuits/tier3_realistic/tier3_calculator.dig",
     "A calculator: computes A op B (add/subtract/and/or) selected by a control input, driving Result."),
    ("data/sample_circuits/tier3_realistic/tier3_latched_display.dig",
     "Latches an input value on a load signal and drives the latched value to the display output."),
    # ("data/sample_circuits/.../your_circuit_5.dig", "goal ..."),
    # ("data/sample_circuits/.../your_circuit_6.dig", "goal ..."),
]

GOAL_CONDITIONS = ["goal", "no_goal"]   
RUNS_PER_CELL = 3                        

TEST_SUMMARY = None

OUTPUT_DIR = Path(os.environ.get("DLC_BENCH_OUT", str(Path.home() / "dlc_benchmark_out")))


def total_cells() -> int:
    return len(BENCH_MODELS) * len(BENCH_CIRCUITS) * len(GOAL_CONDITIONS) * RUNS_PER_CELL
