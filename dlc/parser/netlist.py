"""
Net construction: turn a parsed Circuit's components + wires into nets.

A NET is a maximal set of points that are connected via:
  - wires whose endpoints share coordinates (transitively), and
  - tunnels that share a NetName.

A pin belongs to a net if its actual coordinate coincides with one of the
net's coordinates.

Endpoint-primary design:
  Predicted pin geometry from pin_geometry.py is used only to assign each endpoint to a
  named pin on the right component (name + direction). The algorithm:

    1. Union every wire's two endpoints.
    2. Union tunnel coordinates that share a NetName.
    3. Materialize nets from union-find groups.
    4. For each wire endpoint, find its single closest predicted pin within
       PIN_SNAP_TOLERANCE (Manhattan Distance). This prevents one component from 
       "stealing" an endpoint that actually belongs to a neighbor.
    5. For each predicted pin, the endpoint nearest to that pin becomes the 
       pin's actual coord; pins with no claimed endpoint are recorded as dangling 
       at predicted coord.
    6. Components with no predicted geometry (subcircuit references, unknown
       elements) get implicit pins from any still-unclaimed wire endpoints
       within IMPLICIT_PIN_RADIUS, one pin per (component, net) pair.
"""

from dataclasses import dataclass, field

from dlc.parser.models import Circuit, Component, Wire
from dlc.parser.pin_geometry import absolute_pin_positions


PIN_SNAP_TOLERANCE = 30 # Large enough to cover wide subcircuit instances
IMPLICIT_PIN_RADIUS = 500
_SUBCIRCUIT_DEFAULT_WIDTH_GRID = 10  # 200 units when child has no Width
NO_SIGNAL_ELEMENTS = {"Testcase", "Rectangle"}

class _UnionFind:
    """
    find(x): returns the canonical representative of x's group.
    union(a,b): merges the groups containing a and b.
    """

    def __init__(self):
        self._parent: dict = {}

    def _ensure(self, x):
        if x not in self._parent:
            self._parent[x] = x

    def find(self, x):
        self._ensure(x)
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self._parent[ra] = rb

    def groups(self) -> dict:
        out: dict = {}
        for key in self._parent:
            root = self.find(key)
            out.setdefault(root, []).append(key)
        return out


@dataclass
class Pin:
    component_index: int
    element_name: str
    pin_name: str
    x: int
    y: int
    direction: str


@dataclass
class Net:
    """
    One electrical net.

    Fields:
      net_id:     stable integer id within a NetList.
      coords:     set of (x, y) coordinates that belong to this net.
      pins:       Pin objects attached to this net.
      tunnel_names: set of tunnel NetNames merged into this net (if any).
    """
    net_id: int
    coords: set = field(default_factory=set)
    pins: list = field(default_factory=list)
    tunnel_names: set = field(default_factory=set)

    def drivers(self) -> list:
        return [p for p in self.pins if p.direction == "out"]

    def sinks(self) -> list:
        return [p for p in self.pins if p.direction == "in"]


@dataclass
class NetList:
    """
    All nets of a circuit, plus a coord -> net_id lookup.
    """
    nets: list = field(default_factory=list)
    by_coord: dict = field(default_factory=dict)

    def net_at(self, x: int, y: int):
        nid = self.by_coord.get((x, y))
        if nid is None:
            return None
        return self.nets[nid]

    def summary(self) -> str:
        n_with_driver = sum(1 for net in self.nets if net.drivers())
        n_no_driver = sum(
            1 for net in self.nets if net.pins and not net.drivers()
        )
        n_multi_driver = sum(1 for net in self.nets if len(net.drivers()) > 1)
        return (
            f"NetList: {len(self.nets)} nets, "
            f"{n_with_driver} driven, "
            f"{n_no_driver} undriven-with-pins, "
            f"{n_multi_driver} multi-driver"
        )
    
# Helpers

def _collect_wire_endpoints(circuit: Circuit) -> set[tuple[int, int]]:
    eps: set = set()
    for w in circuit.wires:
        eps.add(w.p1.as_tuple())
        eps.add(w.p2.as_tuple())
    return eps

def _midpoint_branches(circuit: Circuit) -> list[tuple[tuple, tuple]]:
    """
    Detect T-junctions where one wire's endpoint lands on the interior
    (strictly between p1 and p2) of another wire.
    """
    endpoints = _collect_wire_endpoints(circuit)
    pairs: list[tuple[tuple, tuple]] = []
    for w in circuit.wires:
        a = w.p1.as_tuple()
        b = w.p2.as_tuple()
        if a == b:
            continue
        if a[1] == b[1]:
            y = a[1]
            x_lo, x_hi = (a[0], b[0]) if a[0] < b[0] else (b[0], a[0])
            for ep in endpoints:
                if ep == a or ep == b:
                    continue
                if ep[1] == y and x_lo < ep[0] < x_hi:
                    pairs.append((a, ep))
        elif a[0] == b[0]:
            x = a[0]
            y_lo, y_hi = (a[1], b[1]) if a[1] < b[1] else (b[1], a[1])
            for ep in endpoints:
                if ep == a or ep == b:
                    continue
                if ep[0] == x and y_lo < ep[1] < y_hi:
                    pairs.append((a, ep))
    return pairs

def _subcircuit_pin_specs(child) -> list:

    from dlc.parser.pin_geometry import PinSpec
    width_grid = int(child.attributes.get("Width", _SUBCIRCUIT_DEFAULT_WIDTH_GRID))
    body_width = width_grid * 20
    child_inputs = [
        c for c in child.components
        if c.is_input() or c.element_name == "Clock"
    ]
    child_outs = list(child.outputs())
    pins = []
    for i, child_in in enumerate(child_inputs):
        pins.append(PinSpec(
            name=child_in.label or f"in_{i}",
            offset_x=0,
            offset_y=i * 20,
            direction="in",
        ))
    for i, child_out in enumerate(child_outs):
        pins.append(PinSpec(
            name=child_out.label or f"out_{i}",
            offset_x=body_width,
            offset_y=i * 20,
            direction="out",
        ))
    return pins

def _all_predicted_pins(circuit: Circuit) -> list:
    sub_predicted = {}
    for sub_ref in circuit.subcircuits:
        if sub_ref.child_circuit is None:
            continue
        if "Width" not in sub_ref.child_circuit.attributes:
            continue
        sub_predicted[id(sub_ref.parent_component)] = sub_ref.child_circuit
    out = []
    for c_idx, comp in enumerate(circuit.components):
        if comp.element_name.endswith(".dig") and id(comp) in sub_predicted:
            child = sub_predicted[id(comp)]
            for spec in _subcircuit_pin_specs(child):
                out.append((
                    c_idx,
                    (comp.position.x + spec.offset_x, comp.position.y + spec.offset_y),
                    spec,
                ))
        else:
            for abs_pos, spec in absolute_pin_positions(comp):
                out.append((c_idx, (abs_pos.x, abs_pos.y), spec))
    return out


def _assign_endpoints_to_pins(
    predicted: list,
    endpoints: set,
    tolerance: int,
) -> dict:

    """
    Build every (pin, endpoint, distance) triple where distance <=
    tolerance. Sort by (distance, component_index, pin_name, endpoint).
    Walk the sorted list and claim each (pin, endpoint) pair only if
    NEITHER side has been claimed yet. Returns pin_to_coord.

     Direction-aware tolerance:

    - INPUT (and bidir) pins: require **exact x-match** (gap_x = 0).
    
    - OUTPUT pins: keep the loose Manhattan tolerance.
    """
    triples = []
    for ep in endpoints:
        for c_idx, (px, py), spec in predicted:
            dx = abs(px - ep[0])
            dy = abs(py - ep[1])
            d = dx + dy
            if spec.direction in ("in", "bidir"):
                if dx > 0 or dy > 20:
                    continue
            else:
                if d > tolerance:
                    continue
            triples.append((d, c_idx, spec.name, ep))
    triples.sort()

    exact_eps: set = set()
    nonexact_eps: set = set()

    pin_endpoint: dict = {}
    for dist, c_idx, spec_name, ep in triples:
        pin_key = (c_idx, spec_name)
        if pin_key in pin_endpoint:
            continue
        if ep in nonexact_eps:
            continue
        if dist > 0 and ep in exact_eps:
            continue
        pin_endpoint[pin_key] = ep
        if dist == 0:
            exact_eps.add(ep)
        else:
            nonexact_eps.add(ep)
    return pin_endpoint


def _make_pin(
    component_index: int,
    component: Component,
    spec,
    coord: tuple[int, int],
) -> Pin:
    return Pin(
        component_index=component_index,
        element_name=component.element_name,
        pin_name=spec.name,
        x=coord[0],
        y=coord[1],
        direction=spec.direction,
    )


def _attach_pin(
    netlist: NetList,
    pin: Pin,
    coord: tuple[int, int],
) -> None:
    nid = netlist.by_coord.get(coord)
    if nid is not None:
        netlist.nets[nid].pins.append(pin)
        return
    new_id = len(netlist.nets)
    new_net = Net(net_id=new_id, coords={coord}, pins=[pin])
    netlist.nets.append(new_net)
    netlist.by_coord[coord] = new_id


def _attach_pins_endpoint_first(
    circuit: Circuit,
    netlist: NetList,
    endpoints: set,
    tunnel_coords: set,
) -> set:

    predicted = _all_predicted_pins(circuit)
    pin_to_coord = _assign_endpoints_to_pins(
        predicted, endpoints, PIN_SNAP_TOLERANCE
    )
    snapped_endpoints: set = set(pin_to_coord.values())

    claimed_endpoints: set = set()
    for c_idx, (px, py), spec in predicted:
        comp = circuit.components[c_idx]
        snapped = pin_to_coord.get((c_idx, spec.name))
        if snapped is not None:
            pin = _make_pin(c_idx, comp, spec, snapped)
            _attach_pin(netlist, pin, snapped)
            claimed_endpoints.add(snapped)
            continue

        if spec.direction == "out":
            target = (px, py) if (px, py) in netlist.by_coord else None
            if target is None:
                has_unclaimed_endpoint_nearby = any(
                    ep not in snapped_endpoints
                    and abs(ep[0] - px) + abs(ep[1] - py) <= PIN_SNAP_TOLERANCE
                    for ep in endpoints
                )
                if not has_unclaimed_endpoint_nearby:
                    best = None
                    best_d = PIN_SNAP_TOLERANCE + 1
                    for tc in tunnel_coords:
                        if tc in snapped_endpoints:
                            continue
                        d = abs(tc[0] - px) + abs(tc[1] - py)
                        if d < best_d or (
                            d == best_d and (best is None or tc < best)
                        ):
                            best, best_d = tc, d
                    if best is not None and best_d <= PIN_SNAP_TOLERANCE:
                        target = best
            if target is None:
                target = (px, py)
            pin = _make_pin(c_idx, comp, spec, target)
            _attach_pin(netlist, pin, target)
        else:
            pin = _make_pin(c_idx, comp, spec, (px, py))
            _attach_pin(netlist, pin, (px, py))
    return claimed_endpoints


def _wire_endpoint_degree(circuit: Circuit) -> dict:
    """
    Return a dict {(x, y): N} where N is the number of wire endpoints
    landing on that coord. Degree 1 = exactly one wire terminates here. 
    Degree >= 2 = a wire L-bend or branching junction.
    """
    deg: dict = {}
    for w in circuit.wires:
        for ep in (w.p1.as_tuple(), w.p2.as_tuple()):
            deg[ep] = deg.get(ep, 0) + 1
    return deg


def _attach_implicit_pins(
    circuit: Circuit,
    netlist: NetList,
    endpoints: set,
    claimed_endpoints: set,
) -> None:
    
    """
    For components with no predicted geometry (subcircuit references and
    unknown element types), claim still-unattached wire endpoints as
    implicit pins. Each endpoint is assigned to the nearest such component
    within IMPLICIT_PIN_RADIUS, then deduped so each (component, net) pair
    gets at most one implicit pin.

    Critical filter: only degree-1 wire endpoints are eligible. A pin
    location is where exactly one wire terminates; degree-2 (L-bend) and
    degree-3+ (junction) coords are routing, not pins. 

    Only degree-1 wire endpoints are eligible.
    """
    sub_with_predicted = set()
    for sub_ref in circuit.subcircuits:
        if sub_ref.child_circuit is None:
            continue
        if "Width" not in sub_ref.child_circuit.attributes:
            continue
        for idx, comp in enumerate(circuit.components):
            if comp is sub_ref.parent_component:
                sub_with_predicted.add(idx)
                break

    candidates = [
        (idx, comp) for idx, comp in enumerate(circuit.components)
        if not absolute_pin_positions(comp)
        and comp.element_name not in NO_SIGNAL_ELEMENTS
        and idx not in sub_with_predicted
    ]
    if not candidates:
        return

    deg = _wire_endpoint_degree(circuit)

    group_best: dict = {}
    for ep in endpoints:
        if ep in claimed_endpoints:
            continue
        if deg.get(ep, 0) != 1:
            continue
        best_idx = None
        best_dist = IMPLICIT_PIN_RADIUS + 1
        for idx, comp in candidates:
            d = abs(comp.position.x - ep[0]) + abs(comp.position.y - ep[1])
            if d < best_dist:
                best_dist = d
                best_idx = idx
            elif d == best_dist and best_idx is not None and idx < best_idx:
                best_idx = idx
        if best_idx is None:
            continue
        nid = netlist.by_coord.get(ep)
        if nid is None:
            continue
        key = (best_idx, nid)
        prev = group_best.get(key)
        if prev is None:
            group_best[key] = (best_dist, ep)
        else:
            prev_dist, prev_ep = prev
            if best_dist < prev_dist or (
                best_dist == prev_dist and ep < prev_ep
            ):
                group_best[key] = (best_dist, ep)

    for (c_idx, nid), (_dist, ep) in group_best.items():
        comp = circuit.components[c_idx]
        netlist.nets[nid].pins.append(
            Pin(
                component_index=c_idx,
                element_name=comp.element_name,
                pin_name=f"wire@{ep[0]},{ep[1]}",
                x=ep[0],
                y=ep[1],
                direction="unknown",
            )
        )
    _cap_subcircuit_implicit_pins(circuit, netlist)

def _cap_subcircuit_implicit_pins(circuit: Circuit, netlist: NetList) -> None:
    for sub_ref in circuit.subcircuits:
        child = sub_ref.child_circuit
        if child is None:
            continue
        inst_idx = None
        for idx, comp in enumerate(circuit.components):
            if comp is sub_ref.parent_component:
                inst_idx = idx
                break
        if inst_idx is None:
            continue

        n_clock_children = sum(
            1 for c in child.components if c.element_name == "Clock"
        )
        cap = len(child.inputs()) + len(child.outputs()) + n_clock_children
        anchor = circuit.components[inst_idx].position
        pins_with_net: list = []
        for net in netlist.nets:
            for pin in net.pins:
                if pin.component_index == inst_idx and pin.direction == "unknown":
                    d = abs(pin.x - anchor.x) + abs(pin.y - anchor.y)
                    pins_with_net.append((d, pin, net))

        if len(pins_with_net) <= cap:
            continue
        pins_with_net.sort(key=lambda t: (t[0], t[1].x, t[1].y))
        for _, pin, net in pins_with_net[cap:]:
            net.pins.remove(pin)

def _resolve_subcircuit_directions(
    circuit: Circuit, netlist: NetList
) -> None:
    """
    Assign direction and Label-based name to each subcircuit
    instance's implicit pins, using the child Circuit's In/Out elements as
    the port spec.

    Limitation: Works for the common Digital rendering convention 
    (Ins on the left edge, Outs on the right). 
    Edge cases (a child with bidirectional ports, or a layout
    Digital somehow renders differently) fall through unresolved and
    surface as warnings.
    """
    for sub_ref in circuit.subcircuits:
        child = sub_ref.child_circuit
        if child is None:
            continue

        inst_idx = None
        for idx, comp in enumerate(circuit.components):
            if comp is sub_ref.parent_component:
                inst_idx = idx
                break
        if inst_idx is None:
            continue

        implicit_pins = [
            pin for net in netlist.nets for pin in net.pins
            if pin.component_index == inst_idx and pin.direction == "unknown"
        ]
        if not implicit_pins:
            continue

        xs = [p.x for p in implicit_pins]
        if len(set(xs)) >= 2:
            midpoint = (min(xs) + max(xs)) / 2
            left = [p for p in implicit_pins if p.x < midpoint]
            right = [p for p in implicit_pins if p.x > midpoint]
        else:
            n_ins = len(child.inputs())
            implicit_sorted = sorted(implicit_pins, key=lambda p: p.y)
            left = implicit_sorted[:n_ins]
            right = implicit_sorted[n_ins:]

        left.sort(key=lambda p: p.y)
        right.sort(key=lambda p: p.y)

        child_ins = sorted(child.inputs(), key=lambda c: c.position.y)
        child_outs = sorted(child.outputs(), key=lambda c: c.position.y)

        for pin, child_in in zip(left, child_ins):
            pin.direction = "in"
            if child_in.label:
                pin.pin_name = child_in.label

        for pin, child_out in zip(right, child_outs):
            pin.direction = "out"
            if child_out.label:
                pin.pin_name = child_out.label


# Public API

def build_netlist(circuit: Circuit) -> NetList:
    """
    Build the NetList for a circuit.
    """
    uf = _UnionFind()

    # Step 1: unify wire-endpoint coords transitively.
    for wire in circuit.wires:
        a = wire.p1.as_tuple()
        b = wire.p2.as_tuple()
        uf.union(a, b)

    for a, x in _midpoint_branches(circuit):
        uf.union(a, x)

    # Step 2: unify tunnel coords that share a NetName.
    tunnel_coords: set[tuple[int, int]] = set()
    tunnels_by_name: dict[str, list[tuple[int, int]]] = {}
    for comp in circuit.components:
        if comp.element_name != "Tunnel":
            continue
        net_name = comp.attributes.get("NetName")
        coord = comp.position.as_tuple()
        uf._ensure(coord)
        tunnel_coords.add(coord)
        if net_name is not None:
            tunnels_by_name.setdefault(net_name, []).append(coord)

    for net_name, coords in tunnels_by_name.items():
        first = coords[0]
        for other in coords[1:]:
            uf.union(first, other)

    # Step 3: materialize nets from union-find groups.
    groups = uf.groups()
    netlist = NetList()
    for rep, members in groups.items():
        net_id = len(netlist.nets)
        net = Net(net_id=net_id, coords=set(members))
        for comp in circuit.components:
            if comp.element_name == "Tunnel":
                if comp.position.as_tuple() in net.coords:
                    nm = comp.attributes.get("NetName")
                    if nm is not None:
                        net.tunnel_names.add(nm)
        netlist.nets.append(net)
        for m in members:
            netlist.by_coord[m] = net_id

    # Steps 4: endpoint-primary pin attachment (Voronoi).
    endpoints = _collect_wire_endpoints(circuit)
    claimed = _attach_pins_endpoint_first(circuit, netlist, endpoints)

    # Step 5: implicit pins for no-geometry components, deduped per net.
    _attach_implicit_pins(circuit, netlist, endpoints, claimed)

    # Step 6: resolve subcircuit-instance pin directions
    # against the child circuit's In/Out elements.
    _resolve_subcircuit_directions(circuit, netlist)

    return netlist