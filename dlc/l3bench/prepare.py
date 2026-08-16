"""Generate the destroy-test variants from the official control-unit
answer file (DESTROY_BASE) into PREPARED/.

Deterministic, position-addressed edits on the STANDARD control unit:
the three instruction-match gates that the 3-gate exam circuit modified
sit at fixed coordinates in the answer file. Every edit is verified
(element kind checked before swapping; jar-independent) and the script
prints exactly what it found, so a layout drift fails loudly instead of
producing a wrong benchmark row.

    uv run python -m dlc.l3bench.prepare          # generate
    uv run python -m dlc.l3bench.prepare --check  # verify only
"""

from __future__ import annotations

import shutil
import sys

from dlc.l3.patch import apply_patch
from dlc.l3bench import config as C
from dlc.parser.dig_parser import parse_dig_file

# (x, y) of the match gates in the STANDARD answer control-unit — the
# same three positions the instructor's 3-gate exam circuit modified.
ADDI_GATE = (1180, 800)   # And -> Or   (the priority thief)
AND_GATE = (900, 500)     # And -> XOr  (false-fires via odd parity)
SUB_GATE = (600, 800)     # And -> XOr  (test-invisible under official rows)

# Each variant is written as <PREPARED>/<variant>/control-unit.dig —
# the FILENAME must stay "control-unit.dig" so the production rules
# (control-unit lazy-gate exemption, manifest/official-test matching)
# apply exactly as they do for real students. The empty-ROM variant
# would otherwise be lazy-gated before Mode A ever saw it.
VARIANTS = {
    "cu_clean": [],
    "cu_emptyrom": [("rom_data", "")],
    "cu_1gate": [("swap", ADDI_GATE, "Or")],
    "cu_2gates": [("swap", ADDI_GATE, "Or"), ("swap", AND_GATE, "XOr")],
    "cu_3gates": [("swap", ADDI_GATE, "Or"), ("swap", AND_GATE, "XOr"),
                  ("swap", SUB_GATE, "XOr")],
}


def _index_at(circuit, pos: tuple[int, int], expect_kind: str) -> int:
    for i, comp in enumerate(circuit.components):
        p = getattr(comp, "position", None)
        if p is not None and (p.x, p.y) == pos:
            if comp.element_name != expect_kind:
                raise SystemExit(
                    f"expected {expect_kind} at {pos}, found "
                    f"{comp.element_name} — answer-file layout drifted; "
                    f"update prepare.py positions.")
            return i
    raise SystemExit(f"no component at {pos} — answer-file layout "
                     f"drifted; update prepare.py positions.")


def _rom_index(circuit) -> int:
    roms = [i for i, c in enumerate(circuit.components)
            if c.element_name == "ROM"]
    if len(roms) != 1:
        raise SystemExit(f"expected exactly 1 ROM, found {len(roms)}")
    return roms[0]


def _ops_for(circuit, recipe) -> list[dict]:
    ops = []
    for step in recipe:
        if step[0] == "swap":
            _, pos, new_kind = step
            ops.append({"op": "replace_element",
                        "component_index": _index_at(circuit, pos, "And"),
                        "new_element": new_kind})
        elif step[0] == "rom_data":
            ops.append({"op": "change_attribute",
                        "component_index": _rom_index(circuit),
                        "name": "Data", "value": step[1]})
    return ops


def prepare(check_only: bool = False) -> bool:
    base = C.DESTROY_BASE
    if not base.exists():
        print(f"[MISSING] destroy base: {base}")
        return False
    circuit = parse_dig_file(str(base))
    ok = True
    for name, recipe in VARIANTS.items():
        try:
            ops = _ops_for(circuit, recipe)
        except SystemExit as exc:
            print(f"[FAIL] {name}: {exc}")
            ok = False
            continue
        pretty = "; ".join(
            f"{o['op']}[{o['component_index']}]"
            + (f"->{o.get('new_element')}" if o.get("new_element") else "")
            for o in ops) or "verbatim copy"
        print(f"[plan] {name}: {pretty}")
        if check_only:
            continue
        (C.PREPARED / name).mkdir(parents=True, exist_ok=True)
        dest = C.PREPARED / name / "control-unit.dig"
        if not ops:
            shutil.copy2(base, dest)
        else:
            temp, report = apply_patch(str(base), ops)
            if temp is None:
                print(f"[FAIL] {name}: {report.warning}")
                ok = False
                continue
            shutil.move(temp, dest)
        print(f"[done] {dest}")
    return ok


if __name__ == "__main__":
    sys.exit(0 if prepare(check_only="--check" in sys.argv) else 1)
