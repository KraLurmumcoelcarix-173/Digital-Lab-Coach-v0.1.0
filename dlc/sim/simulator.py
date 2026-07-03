"""Combinational value evaluator.

Public entry point: `simulate(circuit, netlist, graph, inputs)` where `inputs`
maps a top-level In label -> integer value. Returns a `SimResult` whose
`net_values` maps net_id -> integer for every net the evaluator could resolve.

Design notes
------------
* We work off the *netlist* pin objects (already named in0/sel/out/Y/… by the
  pin-geometry pass), so a pin's semantic name is authoritative.
* Evaluation is a worklist fixpoint, not a topo sort, so combinational loops
  and register feedback terminate gracefully (they simply stay unresolved)
  instead of raising.
* A component is evaluated only when *all* of its wired input pins already have
  a value. Pins that are not wired at all default inside each component's rule
  (e.g. an adder's carry-in). Anything we cannot resolve is reported in
  `unresolved_nets` so the UI can leave those wires blank instead of guessing.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from dlc.parser.models import Circuit
from dlc.parser.netlist import NetList, build_netlist
from dlc.facts.net_width import infer_net_widths
from dlc.facts.splitter import parse_splitting
from dlc.parser.pin_geometry import inverted_input_names


# Components whose exact behaviour we do not model yet. Their outputs stay
# unresolved so the UI shows no value rather than a wrong one.
_UNMODELED = frozenset({
    "Register", "Counter", "Memory",
    "RAMDualPort", "RAMSinglePort", "D_FF", "T_FF", "JK_FF", "FlipflopD",
})


@dataclass
class SimResult:
    net_values: dict[int, int] = field(default_factory=dict)
    net_bits: dict[int, int] = field(default_factory=dict)
    unresolved_nets: set[int] = field(default_factory=set)
    output_values: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    # register *path* -> next Q (value latched on the coming clock edge), for the
    # sequential replay in simulate_sequential(). A path is the tuple of
    # component indices from the top circuit down to the register, so registers
    # nested inside subcircuits (e.g. a register file) get distinct keys and
    # their state survives across rows.
    reg_next: dict[tuple[int, ...], int] = field(default_factory=dict)


def _mask(bits: int) -> int:
    return (1 << bits) - 1 if bits and bits > 0 else 0


def _as_int(v, default: int = 0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Row -> input assignment
# ---------------------------------------------------------------------------

def inputs_for_row(circuit: Circuit, headers: list[str], row) -> dict[str, int]:
    """Map a parsed TestRow to {In label -> value} using the header columns.

    Only integer-valued input (and clock) columns are used; clock/don't-care
    tokens are skipped. Values are masked to the port's bit width.
    """
    from dlc.testing.spec import match_variables_to_io

    bindings = match_variables_to_io(headers, circuit)
    assignment: dict[str, int] = {}
    values = getattr(row, "values", None) or []
    for col, header in enumerate(headers):
        if col >= len(values):
            break
        binding = bindings.get(header)
        if binding is None:
            continue
        tok = values[col]
        if getattr(tok, "kind", None) != "int" or tok.value is None:
            continue
        if binding.role in ("input", "clock"):
            width = binding.bit_width or 1
            assignment[header] = tok.value & _mask(width)
    return assignment


# ---------------------------------------------------------------------------
# Per-component combinational rules
# ---------------------------------------------------------------------------

def _gate_bits(comp) -> int:
    return max(1, _as_int(comp.attributes.get("Bits", 1), 1))


def _eval_gate(comp, in_vals: dict[str, int]) -> dict[str, int] | None:
    """And/Or/XOr/NAnd/NOr/XNOr with N inputs, multi-bit, inverter bubbles."""
    bits = _gate_bits(comp)
    mask = _mask(bits)
    n = _as_int(comp.attributes.get("Inputs", 2), 2)
    inverted = set(inverted_input_names(comp))  # {"in0", ...}

    operands: list[int] = []
    for i in range(n):
        name = f"in{i}"
        if name not in in_vals:
            return None  # a wired input is still unresolved
        v = in_vals[name] & mask
        if name in inverted:
            v = (~v) & mask
        operands.append(v)
    if not operands:
        return None

    base = comp.element_name
    acc = operands[0]
    if base in ("And", "NAnd"):
        for v in operands[1:]:
            acc &= v
    elif base in ("Or", "NOr"):
        for v in operands[1:]:
            acc |= v
    elif base in ("XOr", "XNOr"):
        for v in operands[1:]:
            acc ^= v
    else:
        return None
    if base in ("NAnd", "NOr", "XNOr"):
        acc = (~acc) & mask
    return {"Y": acc & mask}


def _eval_not(comp, in_vals):
    if "A" not in in_vals:
        return None
    bits = _gate_bits(comp)
    return {"Y": (~in_vals["A"]) & _mask(bits)}


def _eval_const(comp, _in_vals):
    # Digital's Const defaults to 1 when the Value attribute is omitted
    # (matches dlc.facts.extractor); an explicit 0 is stored as Value=0.
    bits = _gate_bits(comp)
    return {"out": _as_int(comp.attributes.get("Value", 1), 1) & _mask(bits)}


def _eval_ground(comp, _in_vals):
    return {"out": 0}


def _eval_vdd(comp, _in_vals):
    return {"out": _mask(_gate_bits(comp))}


def _eval_mux(comp, in_vals):
    sel_bits = _as_int(comp.attributes.get("Selector Bits", 1), 1)
    if "sel" not in in_vals:
        return None
    sel = in_vals["sel"] & _mask(sel_bits)
    chosen = f"in{sel}"
    if chosen not in in_vals:
        return None
    return {"out": in_vals[chosen]}


def _eval_decoder(comp, in_vals):
    sel_bits = _as_int(comp.attributes.get("Selector Bits", 1), 1)
    n_out = 2 ** sel_bits
    if "sel" not in in_vals:
        return None
    sel = in_vals["sel"] & _mask(sel_bits)
    return {f"out_{i}": (1 if i == sel else 0) for i in range(n_out)}


def _eval_priority_encoder(comp, in_vals):
    sel_bits = _as_int(comp.attributes.get("Selector Bits", 1), 1)
    n_in = 2 ** sel_bits
    highest = None
    for i in range(n_in):
        name = f"in_{i}"
        if name in in_vals and in_vals[name]:
            highest = i
    # `num` is defined only when at least one input is set; Digital pairs it
    # with an `any`/valid flag. We report num when known, else 0.
    return {"num": highest if highest is not None else 0}


def _eval_splitter(comp, in_vals):
    in_split = str(comp.attributes.get("Input Splitting", "1"))
    out_split = str(comp.attributes.get("Output Splitting", "1"))
    try:
        in_groups = parse_splitting(in_split)
        out_groups = parse_splitting(out_split)
    except ValueError:
        return None
    # Merge every wired input group into a single bus by absolute bit position.
    bus = 0
    for i, grp in enumerate(in_groups):
        name = f"in{i}"
        if name not in in_vals:
            return None
        v = in_vals[name] & _mask(grp.width)
        bus |= v << grp.bit_lo
    # Slice the bus into the output groups.
    out: dict[str, int] = {}
    for i, grp in enumerate(out_groups):
        out[f"out{i}"] = (bus >> grp.bit_lo) & _mask(grp.width)
    return out


def _eval_add(comp, in_vals):
    bits = _gate_bits(comp)
    mask = _mask(bits)
    a = in_vals.get("a", 0) & mask
    b = in_vals.get("b", 0) & mask
    c_i = in_vals.get("c_i", 0) & 1
    # Both operands must be present if they are wired; the driver loop only
    # calls us once every *wired* input is known, so absent means unconnected.
    total = a + b + c_i
    return {"s": total & mask, "c_o": (total >> bits) & 1}


def _eval_comparator(comp, in_vals):
    if "A" not in in_vals or "B" not in in_vals:
        return None
    a, b = in_vals["A"], in_vals["B"]
    return {
        "gr": 1 if a > b else 0,
        "eq": 1 if a == b else 0,
        "le": 1 if a < b else 0,
    }

def _rom_words(comp) -> list[int]:
    """Parse a ROM's Data field into the word stored at each address.

    Digital keeps Data as a comma/space/newline-separated token string;
    `intFormat` (default hex) selects the base. Matches dlc.facts.extractor.
    """
    raw = comp.attributes.get("Data", "")
    if not isinstance(raw, str):
        raw = "" if raw is None else str(raw)
    tokens = [t for t in raw.replace(",", " ").split() if t]
    fmt = str(comp.attributes.get("intFormat", "hex") or "hex").lower()
    base = {"hex": 16, "bin": 2, "oct": 8, "dec": 10, "def": 10}.get(fmt, 16)
    words: list[int] = []
    for t in tokens:
        try:
            words.append(int(t, base))
        except ValueError:
            try:
                words.append(int(t, 16))   # last-ditch: treat as hex
            except ValueError:
                words.append(0)
    return words


def _eval_rom(comp, in_vals):
    """ROM read: D = Data[A]. `sel` (chip-select), when wired low, disables the
    output (0). Out-of-range addresses read 0."""
    if "A" not in in_vals:
        return None
    if in_vals.get("sel", 1) == 0:      # present-and-low -> output disabled
        return {"D": 0}
    words = _rom_words(comp)
    addr = in_vals["A"]
    val = words[addr] if 0 <= addr < len(words) else 0
    return {"D": val & _mask(_gate_bits(comp))}

def _eval_barrel_shifter(comp, in_vals):
    """Shift `in` by `sh`. `direction` = left (default) / right;
    `barrelShifterMode` = logical (default) / arithmetic / rotate."""
    if "in" not in in_vals or "sh" not in in_vals:
        return None
    bits = _gate_bits(comp)
    mask = _mask(bits)
    x = in_vals["in"] & mask
    sh = in_vals["sh"]
    direction = str(comp.attributes.get("direction", "left") or "left").lower()
    mode = str(comp.attributes.get("barrelShifterMode", "logical") or "logical").lower()
    if mode == "rotate" and bits:
        s = sh % bits
        if direction == "right":
            out = ((x >> s) | (x << (bits - s))) & mask
        else:
            out = ((x << s) | (x >> (bits - s))) & mask
    elif direction == "right":
        if mode == "arithmetic":
            sx = x - (1 << bits) if (x >> (bits - 1)) & 1 else x   # to signed
            out = (sx >> min(sh, bits)) & mask
        else:
            out = (x >> sh) & mask if sh < bits else 0
    else:  # left, logical
        out = (x << sh) & mask if sh < bits else 0
    return {"out": out}


def _eval_bitextender(comp, in_vals):
    """Sign-extend `in` (inputBits) to `out` (outputBits) — Digital replicates
    the input's MSB across the added width."""
    if "in" not in in_vals:
        return None
    in_bits = max(1, _as_int(comp.attributes.get("inputBits", 1), 1))
    out_bits = max(in_bits, _as_int(comp.attributes.get("outputBits", in_bits), in_bits))
    x = in_vals["in"] & _mask(in_bits)
    if (x >> (in_bits - 1)) & 1:                # sign bit set -> extend ones
        x |= (~_mask(in_bits)) & _mask(out_bits)
    return {"out": x & _mask(out_bits)}



_RULES = {
    "And": _eval_gate, "Or": _eval_gate, "XOr": _eval_gate,
    "NAnd": _eval_gate, "NOr": _eval_gate, "XNOr": _eval_gate,
    "Not": _eval_not,
    "Const": _eval_const, "Ground": _eval_ground, "VDD": _eval_vdd,
    "Multiplexer": _eval_mux,
    "Decoder": _eval_decoder,
    "PriorityEncoder": _eval_priority_encoder,
    "Splitter": _eval_splitter,
    "Add": _eval_add,
    "Comparator": _eval_comparator,
    "ROM": _eval_rom,
    "BarrelShifter": _eval_barrel_shifter,
    "BitExtender": _eval_bitextender,
}


# ---------------------------------------------------------------------------
# Core fixpoint
# ---------------------------------------------------------------------------

def _build_child_by_index(circuit: Circuit) -> dict[int, Circuit]:
    out: dict[int, Circuit] = {}
    for sub_ref in circuit.subcircuits:
        if sub_ref.child_circuit is None:
            continue
        for idx, comp in enumerate(circuit.components):
            if comp is sub_ref.parent_component:
                out[idx] = sub_ref.child_circuit
                break
    return out


def simulate(
    circuit: Circuit,
    netlist: NetList,
    graph,
    inputs: dict[str, int],
    *,
    state: dict[int, int] | None = None,
    state_store: dict[tuple[int, ...], int] | None = None,
    path: tuple[int, ...] = (),
    capture_path: tuple[int, ...] | None = None,
    capture_box: dict | None = None,
    _depth: int = 0,
    _max_depth: int = 16,
) -> SimResult:
    """Evaluate `circuit` combinationally for one input assignment.

    `state_store` maps a register *path* (top-to-register component indices) to
    its current Q, seeding sequential elements at every nesting level so a single
    combinational pass sees stored values even for registers buried inside
    subcircuits (a register file). `path` is this circuit's own prefix within
    that store. The caller (`simulate_sequential`) advances the store across
    clock edges. `state` is the legacy top-level-only form ({comp_index -> Q});
    it is folded into `state_store` for backward compatibility.

    `capture_path` (a full path of component indices from the top circuit down
    to a subcircuit instance) requests the internal `SimResult` of that nested
    subcircuit as evaluated for this same row; when reached it is stored in
    `capture_box["result"]`. This powers the drill-in view, which colors a
    subcircuit's own wires for the master circuit's selected row.
    """
    result = SimResult()
    if state_store is None:
        state_store = {}
    if state:
        for k, v in state.items():
            key = k if isinstance(k, tuple) else path + (k,)
            state_store.setdefault(key, v)

    def reg_state(idx: int) -> int:
        return state_store.get(path + (idx,), 0)

    # Bit width per net — single source of truth, matches the edge widths.
    per_net, _conflicts = infer_net_widths(circuit, netlist)
    for nid, info in per_net.items():
        if info.width is not None:
            result.net_bits[nid] = info.width

    # (component_index, pin_name) -> net_id, and per-component wired pins.
    comp_pins: dict[int, list[tuple[str, str, int]]] = defaultdict(list)
    for net in netlist.nets:
        for p in net.pins:
            comp_pins[p.component_index].append((p.pin_name, p.direction, net.net_id))

    net_values = result.net_values
    child_by_index = _build_child_by_index(circuit)
    have_unmodeled = set()

    def set_net(nid: int, val: int) -> bool:
        if nid in net_values:
            return False
        bits = result.net_bits.get(nid)
        net_values[nid] = (val & _mask(bits)) if bits else val
        return True

    def pin_net(idx: int, pin_name: str) -> int | None:
        for name, _d, nid in comp_pins.get(idx, []):
            if name == pin_name:
                return nid
        return None

    # Seed sources: top-level inputs + constants + register Q (from state).
    for idx, comp in enumerate(circuit.components):
        pins = comp_pins.get(idx, [])
        if comp.is_input():
            label = comp.label
            if label in inputs:
                for name, direction, nid in pins:
                    if direction == "out":
                        set_net(nid, inputs[label])
        elif comp.element_name in ("Const", "Ground", "VDD"):
            rule = _RULES[comp.element_name]
            outs = rule(comp, {})
            for name, direction, nid in pins:
                if direction == "out" and outs and name in outs:
                    set_net(nid, outs[name])
        elif comp.element_name == "Register":
            q_net = pin_net(idx, "Q")
            if q_net is not None:
                set_net(q_net, reg_state(idx))

    # Worklist fixpoint. A hard iteration cap (component count + slack) bounds
    # runtime and guarantees termination on cyclic / stateful topologies.
    max_iters = len(circuit.components) + 4
    sub_cache: dict[int, tuple] = {}   # subcircuit idx -> (input-sig, outs)
    for _ in range(max_iters):
        changed = False
        for idx, comp in enumerate(circuit.components):
            pins = comp_pins.get(idx, [])
            if not pins:
                continue
            out_nets = [nid for name, d, nid in pins if d == "out"]
            # Plain components are done once their outputs are set. Subcircuits
            # keep re-evaluating even then: a register file's ReadData resolves
            # early (from read address), but its internal registers' next-state
            # depends on write-side inputs that arrive later, and that reg_next
            # must reach the final value before the clock edge latches it. The
            # sub_cache keeps this cheap when the resolved inputs are unchanged.
            if (out_nets and all(nid in net_values for nid in out_nets)
                    and not comp.element_name.endswith(".dig")):
                continue  # already fully resolved

            in_vals: dict[str, int] = {}
            unresolved_input = False
            for name, direction, nid in pins:
                if direction == "in":
                    if nid in net_values:
                        in_vals[name] = net_values[nid]
                    else:
                        unresolved_input = True
            # A subcircuit may resolve some outputs from a subset of its inputs
            # (e.g. a register file's ReadData doesn't depend on WriteData), so
            # evaluate it with whatever inputs are ready — the outer fixpoint
            # re-runs it as more resolve. Plain gates still need every input.
            is_subcircuit = comp.element_name.endswith(".dig")
            if unresolved_input and not is_subcircuit:
                continue

            if is_subcircuit:
                # Skip the (expensive) recursion when this subcircuit's resolved
                # inputs haven't changed since we last evaluated it.
                sig = frozenset(in_vals.items())
                cached = sub_cache.get(idx)
                # When this subcircuit (or one it contains) is the drill-in
                # target, always re-evaluate so the captured internal result
                # reflects the fully-resolved inputs; otherwise use the cache.
                child_path = path + (idx,)
                on_capture_route = (
                    capture_path is not None
                    and capture_path[:len(child_path)] == child_path)
                if (cached is not None and cached[0] == sig
                        and not on_capture_route):
                    outs, child_rn = cached[1], cached[2]
                else:
                    outs, child_rn = _eval_subcircuit(
                        comp, idx, in_vals, child_by_index, _depth, _max_depth,
                        state_store, path, capture_path, capture_box)
                    sub_cache[idx] = (sig, outs, child_rn)
                # Bubble the subcircuit's (path-keyed) register next-states up so
                # simulate_sequential can latch them on the shared clock edge.
                if child_rn:
                    result.reg_next.update(child_rn)
            else:
                outs = _eval_node(comp, idx, in_vals, child_by_index,
                                  _depth, _max_depth)
            if outs is None:
                if comp.element_name in _UNMODELED or comp.element_name.endswith(".dig"):
                    have_unmodeled.add(comp.element_name)
                continue
            for name, direction, nid in pins:
                if direction == "out" and name in outs:
                    if set_net(nid, outs[name]):
                        changed = True
        if not changed:
            break
    # Compute each register's next-Q (its resolved D input, gated by `en`).
    has_register = False
    for idx, comp in enumerate(circuit.components):
        if comp.element_name != "Register":
            continue
        has_register = True
        d_net = pin_net(idx, "D")
        en_net = pin_net(idx, "en")
        if d_net is not None and d_net in net_values:
            enabled = True
            if en_net is not None and en_net in net_values:
                enabled = bool(net_values[en_net])
            result.reg_next[path + (idx,)] = (
                net_values[d_net] if enabled else reg_state(idx))

    # Collect top-level output values + record unresolved signal-carrying nets.
    for idx, comp in enumerate(circuit.components):
        if comp.is_output():
            for name, direction, nid in comp_pins.get(idx, []):
                if direction == "in" and nid in net_values:
                    result.output_values[comp.label or f"out_{idx}"] = net_values[nid]

    for net in netlist.nets:
        if net.net_id in net_values:
            continue
        if net.drivers() and net.sinks():
            result.unresolved_nets.add(net.net_id)

    if has_register and result.unresolved_nets and not state:
        result.notes.append(
            "Registers/clocked state not evaluated (combinational-only); "
            "click through rows in order for register values to fill in."
        )
    return result


def simulate_sequential(
    circuit: Circuit,
    netlist: NetList,
    graph,
    spec,
    row_index: int,
    *,
    capture_path: tuple[int, ...] | None = None,
    capture_box: dict | None = None,
) -> SimResult:
    """Evaluate a clocked circuit *as of* one test row.

    Replays the testcase from a zeroed register state up to and including
    `row_index`, latching all registers on every row that carries a clock
    pulse (a `C` token in a clock column — the common single-clock-domain lab
    case). The returned SimResult is the settled, post-edge view for that row,
    so its net values match what Digital displays when you step there.

    
    `capture_path`/`capture_box` request a nested subcircuit's internal
    SimResult for `row_index` (see `simulate`), captured on that row's final
    settled pass so it reflects the same state the top-level view shows.
    """
    rows = [r for r in spec.rows if not r.is_malformed]
    target = None
    for r in rows:
        if r.line_index == row_index:
            target = r
            break
    if target is None:
        return SimResult()

    # Path-keyed register state (see SimResult.reg_next); covers registers
    # nested inside subcircuits so a register file's contents persist row to row.
    reg_state: dict[tuple[int, ...], int] = {}

    def apply_row(row, cap_path=None, cap_box=None) -> SimResult:
        nonlocal reg_state
        inp = inputs_for_row(circuit, spec.headers, row)
        clocked = _row_has_clock_edge(circuit, spec.headers, row)
        # Capture only on the final (settled) pass so the drill-in view matches
        # the post-edge state the top-level graph shows for this row.
        res = simulate(circuit, netlist, graph, inp, state_store=dict(reg_state),
                       capture_path=None if clocked else cap_path,
                       capture_box=None if clocked else cap_box)
        if clocked:
            new_state = dict(reg_state)
            new_state.update(res.reg_next)
            reg_state = new_state
            # Re-settle so downstream-of-register nets show the post-edge value.
            res = simulate(circuit, netlist, graph, inp,
                           state_store=dict(reg_state),
                           capture_path=cap_path, capture_box=cap_box)
        return res

    result = SimResult()
    for row in rows:
        is_target = row.line_index == row_index
        result = apply_row(row,
                           capture_path if is_target else None,
                           capture_box if is_target else None)
        if is_target:
            break
    return result


def _row_has_clock_edge(circuit, headers, row) -> bool:
    from dlc.testing.spec import match_variables_to_io

    bindings = match_variables_to_io(headers, circuit)
    values = getattr(row, "values", None) or []
    for col, header in enumerate(headers):
        if col >= len(values):
            break
        binding = bindings.get(header)
        if binding is not None and binding.role == "clock":
            if getattr(values[col], "kind", None) == "clock":
                return True
    return False

def _eval_node(comp, idx, in_vals, child_by_index, depth, max_depth):
    """Return output-pin values for a plain (non-subcircuit) component, or None.

    Subcircuits are handled inline by `simulate()` so their nested register
    next-states can bubble up; this only dispatches the per-component rules.
    """
    rule = _RULES.get(comp.element_name)
    if rule is not None:
        return rule(comp, in_vals)
    return None


def _eval_subcircuit(comp, idx, in_vals, child_by_index, depth, max_depth,
                     state_store, path, capture_path=None, capture_box=None):
    """Recurse into a resolved subcircuit.

    Parent input pins are named for the child's In labels (see
    netlist._subcircuit_pin_specs), so we can feed them straight in and read
    the child's Out labels back out onto the parent's output pins.

    
    Returns ``(output_values | None, reg_next)`` where ``reg_next`` is the
    child's path-keyed register next-states (already prefixed with this
    subcircuit's path) so the caller can latch them on the shared clock edge.
    When `capture_path` points at (or through) this instance, the child's full
    `SimResult` for the matching path is stored in `capture_box["result"]`.
    """
    child = child_by_index.get(idx)
    if child is None or depth >= max_depth:
        return None, {}
    child_path = path + (idx,)
    # Only carry the capture request down the branch that leads to the target.
    want = (capture_path is not None
            and capture_path[:len(child_path)] == child_path)
    try:
        # Memoize the child's netlist/graph on the circuit object: it never
        # changes, and rebuilding a 200+ component subcircuit on every fixpoint
        # iteration is what makes a full CPU intractable.
        child_nl = getattr(child, "_sim_nl", None)
        if child_nl is None:
            from dlc.parser.graph import build_signal_graph
            child_nl = build_netlist(child)
            child._sim_nl = child_nl
            child._sim_g = build_signal_graph(child, child_nl)
        child_g = child._sim_g
        sub = simulate(
            child, child_nl, child_g, dict(in_vals),
            state_store=state_store, path=child_path,
            capture_path=capture_path if want else None,
            capture_box=capture_box if want else None,
            _depth=depth + 1, _max_depth=max_depth,
        )
    except Exception:
        return None, {}
    if want and capture_box is not None and child_path == capture_path:
        capture_box["result"] = sub          # settled internal view for drill-in
    outs = dict(sub.output_values) if sub.output_values else None
    return outs, dict(sub.reg_next)
