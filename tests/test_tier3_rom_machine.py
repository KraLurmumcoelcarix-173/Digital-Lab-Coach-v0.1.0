import os

from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.netlist import build_netlist
from dlc.parser.graph import build_signal_graph
from dlc.analyzer import check_all_l1_deep
from dlc.testing.spec import extract_test_specs
from dlc.sim.simulator import simulate_sequential

FIXTURE = os.path.join(
    os.path.dirname(__file__), "..", "data", "sample_circuits",
    "tier3_realistic", "tier3_rom_machine.dig",
)

_TRACE = [
    (0x00, 0), (0x25, 1), (0x3C, 2), (0x01, 3), (0x80, 4), (0x01, 5),
    (0x10, 6), (0x00, 7), (0xC3, 8), (0x00, 9), (0x33, 10),
]


def test_l1_clean():
    c = parse_dig_file(FIXTURE)
    issues = check_all_l1_deep(c)
    assert not issues.issues, [
        (i.kind, i.severity.value, i.title) for i in issues.issues
    ]


def test_rom_has_ten_program_words():
    c = parse_dig_file(FIXTURE)
    roms = [x for x in c.components if x.element_name == "ROM"]
    assert len(roms) == 1
    words = [w for w in roms[0].attributes.get("Data", "").split(",") if w.strip()]
    assert len(words) == 10


def test_simulator_matches_digital_trace():
    c = parse_dig_file(FIXTURE)
    nl = build_netlist(c)
    g = build_signal_graph(c, nl)
    spec = extract_test_specs(c)[0]
    rows = [r for r in spec.rows if not r.is_malformed]
    assert len(rows) == len(_TRACE)

    out_idx = {comp.label: i for i, comp in enumerate(c.components)
               if comp.element_name == "Out"}
    for k, row in enumerate(rows):
        res = simulate_sequential(c, nl, g, spec, row.line_index)
        got = {}
        for lbl, idx in out_idx.items():
            comp = c.components[idx]
            net = nl.net_at(comp.position.x, comp.position.y)
            got[lbl] = res.net_values.get(net.net_id) if net else None
        assert (got["ACC"], got["PCv"]) == _TRACE[k], (
            f"row {k}: sim {got} != expected {_TRACE[k]}"
        )
