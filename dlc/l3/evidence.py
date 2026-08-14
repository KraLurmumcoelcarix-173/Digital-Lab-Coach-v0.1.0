"""L3 Mode A evidence core — the deterministic scan before any LLM.

Implements the zero-model steps of the frozen coordinator pipeline
(docs/l3_debug_contract.md §2, steps 2-4) for ONE testcase:

  mode decision   "clear" (nothing fails) / "lazy" (a gross-check trips —
                  the suggestion-only branch, no daily use burned) /
                  "analysis" (clustered evidence, ready for sub-agents).
  clustering      failing rows grouped by SIGNATURE: the tuple of
                  (mismatched output columns, exercised select values
                  read from the row's inputs — plus the decoded program
                  category when the lab is program-driven, overlap of
                  the top localizer suspects). Cap 4 clusters; overflow
                  FOLDS into the nearest cluster, never dropped.
  evidence        per cluster, the frozen §3 sub-agent INPUT payload,
                  built only from verified facts: the Python evaluator's
                  per-net values for ≤ 2 representative rows, compact
                  expected-vs-found for every row, localize() per row
                  merged via merge_reports().

Nothing here calls a model, so all of it works offline. The jar's
per-row verdict stays authoritative whenever the caller
has one — pass ``failing_indices`` (+ ``jar_mismatches``); without it
the evaluator's own expected-vs-found sweep decides, so the
deterministic half of Mode A needs no Digital.jar at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from dlc.analyzer.sequential import _CLOCKED_ELEMENTS as _STATE_ELEMENTS
from dlc.facts.extractor import extract_facts
from dlc.l3.localizer import SuspectReport, localize, merge_reports
from dlc.l3.manifest import decode_program_word, find_manifest
from dlc.llm.explain import _compact_facts
from dlc.parser.dig_parser import parse_dig_file
from dlc.parser.graph import build_signal_graph
from dlc.parser.netlist import build_netlist
from dlc.sim.simulator import SimResult, simulate_sequential
from dlc.testing.spec import TestSpec, extract_test_specs, match_variables_to_io

CONTRACT = "l3.debug.v1.1"

# More failing rows than this is a structural problem, not a localizable
# bug — Mode A answers with the suggestion branch instead of burning a
# hypothesis hunt (and a daily use) on a doomed circuit.
GROSS_MAX_FAILING = 20

# The tiered pass-rate bars only judge BIG circuits. A small circuit that
# is otherwise Layer-3-ready is exactly the "close to the answer"
# audience, whatever its pass rate — every row can fail on a 20-part
# adder and one wrong constant still explains all of it.
RATE_GATE_MIN_COMPONENTS = 30
# Focus requisite: failing rows wrong in >=4 output columns AT ONCE must
# stay UNDER this share of ALL well-formed testcase rows, or the run
# is lazy regardless of pass rate.
SCATTERED_ROW_MAX_SHARE = 0.25

_MAX_CLUSTERS = 4          # one sub-agent per cluster, never one per row
_MAX_REPRESENTATIVES = 2   # full per-net evidence for at most 2 rows/cluster
_TOP_SUSPECTS = 5          # signature part 3 compares this many top suspects
_MIN_OVERLAP = 0.5         # Jaccard threshold for "same suspects"

# Input columns whose NAME alone marks them as operation selectors, for
# circuits where the select path runs through splitters or subcircuits
# the net probe cannot follow. The net probe (a direct net into a `sel`
# pin) stays primary.
_SELECT_NAME_HINTS = frozenset({
    "op", "opcode", "sel", "select", "mode", "ctrl", "control",
    "aluop", "func", "funct", "operation",
})


@dataclass
class RowEvidence:
    """Everything the pipeline knows about ONE failing row."""

    row_index: int
    raw: str
    mismatches: list[dict] = field(default_factory=list)   # [{column, expected, found}]
    outputs: list[dict] = field(default_factory=list)      # /api/simulate `outputs` shape
    net_values: dict[str, dict] = field(default_factory=dict)
    unresolved_nets: list[int] = field(default_factory=list)
    selects: list[list[str]] = field(default_factory=list)  # [[column, raw token], ...]
    category: str | None = None            # manifest-decoded program category
    program_word: str | None = None        # hex word behind `category`
    suspect_report: SuspectReport = field(default_factory=SuspectReport)


@dataclass
class Cluster:
    signature: dict = field(default_factory=dict)
    rows: list[RowEvidence] = field(default_factory=list)
    merged: SuspectReport = field(default_factory=SuspectReport)
    folded_rows: int = 0


@dataclass
class EvidenceResult:
    mode: str = "clear"                    # "clear" | "lazy" | "analysis"
    gross_flags: list[dict] = field(default_factory=list)
    failing_count: int = 0
    spec_name: str | None = None
    headers: list[str] = field(default_factory=list)
    clusters: list[Cluster] = field(default_factory=list)
    payloads: list[dict] = field(default_factory=list)     # §3 INPUT per cluster
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "gross_flags": self.gross_flags,
            "failing_count": self.failing_count,
            "spec_name": self.spec_name,
            "headers": self.headers,
            "clusters": [
                {
                    "signature": c.signature,
                    "rows": [r.row_index for r in c.rows],
                    "folded_rows": c.folded_rows,
                }
                for c in self.clusters
            ],
            "payloads": self.payloads,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Row evaluation (evaluator-grounded expected-vs-found)
# ---------------------------------------------------------------------------

def _mask(bits: int | None) -> int:
    return (1 << bits) - 1 if bits and bits > 0 else 0


def _output_ok(found, exp_val, width) -> bool | None:
    """Bit-pattern comparison at the port's width, exactly like
    /api/simulate: a signed expected (-60) matches the evaluator's
    unsigned two's-complement value."""
    if found is None:
        return None
    if width:
        return (found & _mask(width)) == (exp_val & _mask(width))
    return found == exp_val


def _fmt_value(v, width, signed_hint) -> str | None:
    """Render a value the way /api/simulate does: signed decimal when the
    testcase wrote a negative expected value, hex for buses, plain for
    single bits."""
    if v is None:
        return None
    if not width or width <= 1:
        return str(v)
    u = v & _mask(width)
    if signed_hint and (u >> (width - 1)) & 1:
        return str(u - (1 << width))
    return f"0x{u:X}"


def _outputs_report(spec: TestSpec, bindings, row, sim: SimResult):
    """(/api/simulate-shaped outputs list, mismatch cells) for one row."""
    col = {h: i for i, h in enumerate(spec.headers)}
    outputs: list[dict] = []
    mismatches: list[dict] = []
    for h in spec.headers:
        b = bindings.get(h)
        if b is None or b.role != "output":
            continue
        tok = row.values[col[h]]
        if tok.kind != "int" or tok.value is None:
            continue
        found = sim.output_values.get(h)
        signed = tok.value < 0
        ok = _output_ok(found, tok.value, b.bit_width)
        entry = {
            "label": h,
            "expected": _fmt_value(tok.value, b.bit_width, signed),
            "found": _fmt_value(found, b.bit_width, signed),
            "ok": ok,
        }
        outputs.append(entry)
        if ok is not True:
            mismatches.append({
                "column": h,
                "expected": entry["expected"],
                "found": entry["found"],
            })
    return outputs, mismatches


# ---------------------------------------------------------------------------
# Signature ingredients
# ---------------------------------------------------------------------------

def select_columns(circuit, netlist, spec: TestSpec, bindings=None) -> list[str]:
    """Input columns that carry the row's "exercised operation": their In
    component's net feeds a `sel` pin somewhere, or their name alone says
    selector. Deterministic, header order."""
    if bindings is None:
        bindings = match_variables_to_io(spec.headers, circuit)
    sel_fed: set[int] = set()
    for net in netlist.nets:
        if any(p.pin_name == "sel" and p.direction == "in" for p in net.pins):
            for p in net.pins:
                if p.direction == "out":
                    sel_fed.add(p.component_index)
    out: list[str] = []
    for h in spec.headers:
        b = bindings.get(h)
        if b is None or b.role != "input":
            continue
        if b.component_index in sel_fed or h.lower() in _SELECT_NAME_HINTS:
            out.append(h)
    return out


def _program_rom_out_net(circuit, netlist) -> int | None:
    """Net id carrying the single program ROM's output, else None."""
    roms = [
        i for i, c in enumerate(circuit.components)
        if c.element_name == "ROM"
        and str(c.attributes.get("isProgramMemory", "")).lower() == "true"
    ]
    if len(roms) != 1:
        return None
    idx = roms[0]
    for net in netlist.nets:
        if any(p.component_index == idx and p.direction == "out"
               for p in net.pins):
            return net.net_id
    return None


def row_category(circuit, netlist, sim: SimResult, manifest) -> dict | None:
    """The program word on the ROM's output net this row, decoded through
    the manifest — the "exercised category" for program-driven labs.
    None whenever any link is missing (no manifest decode, no single
    program ROM, unresolved net)"""
    if not manifest:
        return None
    nid = _program_rom_out_net(circuit, netlist)
    if nid is None:
        return None
    word = sim.net_values.get(nid)
    if word is None:
        return None
    d = decode_program_word(manifest, word)
    if d is None:
        return None
    return {"word": f"{word:x}", "category": d.get("category"),
            "fields": d.get("fields")}


# ---------------------------------------------------------------------------
# Gross-checks (the "this is not one bug" gate)
# ---------------------------------------------------------------------------

def _holds_state(circuit) -> bool:
    """Does the tree contain any STATE-HOLDING element (register, flip-
    flop, RAM, counter)? A Clock source alone does not count — that is
    exactly the missing-pipeline shape."""
    for comp in circuit.components:
        if comp.element_name in _STATE_ELEMENTS:
            return True
    for sub in circuit.subcircuits:
        if sub.child_circuit is not None and _holds_state(sub.child_circuit):
            return True
    return False


def _cell_int(raw) -> int | None:
    """Parse a testcase/jar cell value ('1', '0b101', '0x1f', 12) or None."""
    if isinstance(raw, bool):
        return int(raw)
    if isinstance(raw, int):
        return raw
    s = str(raw).strip()
    try:
        return int(s, 0)
    except (TypeError, ValueError):
        return None


def _frozen_trunk(spec: TestSpec, bindings,
                  row_mismatch_cells: dict[int, list[dict]] | None) -> bool:
    """True when the failing rows are fully explained by every output
    being frozen at one constant value (see gross_check docstring)."""
    if not row_mismatch_cells or len(row_mismatch_cells) < 2:
        return False
    hdr_idx = {h: i for i, h in enumerate(spec.headers)}
    output_cols = {h for h, b in bindings.items() if b.role == "output"}
    if not output_cols:
        return False
    frozen: dict[str, int] = {}
    mismatched_rows: dict[str, set[int]] = {}
    for idx, cells in row_mismatch_cells.items():
        for c in cells or []:
            col = c.get("column")
            found = _cell_int(c.get("found"))
            if not col or col not in output_cols or found is None:
                return False
            if frozen.setdefault(col, found) != found:
                return False              # the column provably moves
            mismatched_rows.setdefault(col, set()).add(idx)
    if not frozen:
        return False
    rows_by_index = {r.line_index: r for r in spec.rows if not r.is_malformed}

    def _expected(idx: int, col: str) -> int | None:
        row = rows_by_index.get(idx)
        i = hdr_idx.get(col)
        if row is None or i is None or i >= len(row.values):
            return None
        tok = row.values[i]
        return tok.value if tok.kind == "int" else None
    # Every row the circuit PASSES — and every passing cell of a failing
    # row — must also be consistent with the frozen constants: a frozen
    # column passes a cell only when its expected equals the frozen
    # value, and a never-mismatching output must carry ONE constant
    # expected everywhere it is checked. Any row that passed with a
    # different expected proves that output moves — not a dead trunk.
    # (This is what lets an all-zero-expectation row pass under a
    # stuck-at-0 trunk without breaking the story.)
    all_rows = [r.line_index for r in spec.rows if not r.is_malformed]
    seen_const: dict[str, set[int]] = {}
    for idx in all_rows:
        for col in output_cols:
            if col in frozen and idx in mismatched_rows.get(col, ()):
                continue                       # the mismatch cell itself
            exp = _expected(idx, col)
            if exp is None:
                continue                       # dontcare / unparseable
            if col in frozen:
                if exp != frozen[col]:
                    return False
            else:
                seen_const.setdefault(col, set()).add(exp)
                if len(seen_const[col]) > 1:
                    return False
    return True


def gross_check(circuit, spec: TestSpec, failing_count: int, *,
                max_failing: int = GROSS_MAX_FAILING,
                rate_gate_min_components: int = RATE_GATE_MIN_COMPONENTS,
                row_mismatch_columns: list[set] | None = None,
                row_mismatch_cells: dict[int, list[dict]] | None = None,
                ) -> list[dict]:
    """Deterministic checks that mean the circuit needs fundamentals, not
    a per-bug hypothesis hunt. Any flag → mode "lazy" (suggestion-only
    branch). Returns [{kind, detail}] in a fixed order: focus requisite,
    structural checks, pass-rate bars last.

    v0.1.0 ratified gate (>30-component trees; smaller circuits skip the
    focus and rate checks entirely — a close-to-answer student gets help,
    not rejection):
      1. FOCUS REQUISITE — "≤3 mismatch columns is a REQUISITE, not an
         amnesty": a failing row wrong in 4+ output columns at once is a
         fundamentals symptom. When such rows reach
         SCATTERED_ROW_MAX_SHARE of ALL well-formed testcase rows, the
         run is lazy REGARDLESS of pass rate. A rare scattered row in a
         long suite passes (it usually shares the focused rows' root
         cause); rows with no column info can never count as scattered.
      2. STRUCTURAL — unbound columns; clock driven with no state element.
      3. PASS-RATE BARS, checked LAST (no focus amnesty anymore):
           > 10 rows:  lazy when failing > max_failing AND pass rate < 20%
           6-10 rows:  lazy when pass rate < 60%
           1-5  rows:  lazy when pass rate < 30%
    ``row_mismatch_columns`` carries one set of mismatched output columns
    per failing row (empty set = no column info for that row).

    FROZEN-TRUNK EXCEPTION: when the failing rows are fully
    explained by "every output is stuck at one constant value" — each
    mismatched column shows the SAME found value on every failing row,
    and every output column that never mismatches has a CONSTANT
    expected value across the failing rows (so nothing provably moves)
    — the cause sits upstream of all outputs at once (a dead output
    stage, an unprogrammed decode ROM reading 0s). That is one
    mechanism, not scatter, so the scattered flag and the pass-rate
    bars stand aside and Mode A analyzes it. Convicted on s008's
    control unit: the empty decode ROM freezes all outputs at 0 and
    fails all 8 official rows, and the fix is ONE attribute; the bars
    used to reject it into the suggestion branch. An output that
    demonstrably CHANGES across failing rows (a working segment next
    to a wrong SOP output) breaks the frozen story and keeps the
    ratified rules: the focus requisite and the pass-rate bars still
    get the last word. Structural flags (unbound columns, clock
    without state) always apply. Requires ``row_mismatch_cells``.
    """
    flags: list[dict] = []
    bars_on = len(circuit.components) > rate_gate_min_components
    bindings = match_variables_to_io(spec.headers, circuit)
    frozen_trunk = _frozen_trunk(spec, bindings, row_mismatch_cells)

    if bars_on and row_mismatch_columns and not frozen_trunk:
        total_rows = spec.well_formed_row_count()
        scattered = [cols for cols in row_mismatch_columns
                     if len(cols) >= 4]
        if scattered and total_rows and (len(scattered) / total_rows
                                         >= SCATTERED_ROW_MAX_SHARE):
            flags.append({
                "kind": "scattered_failures",
                "detail": (
                    f"{len(scattered)} of the testcase's {total_rows} rows "
                    f"are wrong in 4 or more output columns AT ONCE. "
                    f"That spread points at the design plan, not one "
                    f"localized bug — whatever the pass rate says."
                ),
            })

    unbound = [h for h in spec.headers
               if bindings[h].role == "unbound"]
    if unbound:
        flags.append({
            "kind": "unbound_columns",
            "detail": (
                "testcase column(s) " + ", ".join(repr(h) for h in unbound)
                + " match no input, output, or clock label in this circuit "
                "— ports are missing or renamed, so the tests cannot drive "
                "or observe what they were written for."
            ),
        })
    has_clock_col = any(b.role == "clock" for b in bindings.values()) or any(
        tok.kind == "clock"
        for row in spec.rows if not row.is_malformed
        for tok in row.values
    )
    if has_clock_col and not _holds_state(circuit):
        flags.append({
            "kind": "missing_clocked_logic",
            "detail": (
                "the testcase drives a clock, but the circuit contains no "
                "register or other clocked element — nothing can hold "
                "state between rows (is the pipeline stage missing?)."
            ),
        })
    if not bars_on or frozen_trunk:
        n_rows = 0    # bars off: small circuit or one frozen trunk
    else:
        n_rows = spec.well_formed_row_count()
    passing = max(0, n_rows - failing_count)
    rate = passing / n_rows if n_rows else 0.0
    if n_rows >= 11:
        if failing_count > max_failing and rate < 0.20:
            flags.append({
                "kind": "too_many_failures",
                "detail": (
                    f"{failing_count} of {n_rows} rows fail — more than "
                    f"{max_failing}, with under 20% passing. That is "
                    f"usually a structural problem (wrong wiring plan, "
                    f"missing block), not one localized bug; revisit the "
                    f"design before chasing single rows."
                ),
            })
    elif n_rows >= 6:
        if rate < 0.60:
            flags.append({
                "kind": "low_pass_rate",
                "detail": (
                    f"only {passing} of {n_rows} rows pass — below the 60% "
                    f"bar for a 6-10 row testcase. Rebuild the basics "
                    f"before hunting a single bug."
                ),
            })
    elif n_rows >= 1:
        if rate < 0.30:
            flags.append({
                "kind": "low_pass_rate",
                "detail": (
                    f"only {passing} of {n_rows} rows pass — below the 30% "
                    f"bar for a 1-5 row testcase. Rebuild the basics "
                    f"before hunting a single bug."
                ),
            })
    return flags


# ---------------------------------------------------------------------------
# Per-row evidence
# ---------------------------------------------------------------------------

def _row_evidence(circuit, netlist, graph, spec, bindings, row, *,
                  sel_cols, manifest, sim=None, jar_cells=None,
                  notes=None) -> RowEvidence:
    if sim is None:
        sim = simulate_sequential(circuit, netlist, graph, spec,
                                  row.line_index)
    outputs, mismatches = _outputs_report(spec, bindings, row, sim)
    if jar_cells and not mismatches:
        # Digital's verdict is authoritative; when the evaluator cannot
        # reproduce the failure, keep the jar's expected-vs-found cells.
        mismatches = [dict(c) for c in jar_cells]
        if notes is not None:
            notes.append(
                f"row {row.line_index}: Digital reports a failure the "
                f"evaluator cannot reproduce; using Digital's cells."
            )
    col = {h: i for i, h in enumerate(spec.headers)}
    selects = [[h, row.values[col[h]].raw] for h in sel_cols]
    cat = row_category(circuit, netlist, sim, manifest)
    report = localize(circuit, netlist, graph, sim, outputs)
    net_values = {
        str(nid): {
            "value": val,
            "bits": sim.net_bits.get(nid, 1),
            "hex": format(val, "X"),
        }
        for nid, val in sim.net_values.items()
    }
    return RowEvidence(
        row_index=row.line_index,
        raw=row.raw,
        mismatches=mismatches,
        outputs=outputs,
        net_values=net_values,
        unresolved_nets=sorted(sim.unresolved_nets),
        selects=selects,
        category=cat["category"] if cat else None,
        program_word=cat["word"] if cat else None,
        suspect_report=report,
    )


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------

def _bucket_key(r: RowEvidence):
    return (
        frozenset(m.get("column", "?") for m in r.mismatches),
        tuple(tuple(s) for s in r.selects),
        r.category,
    )


def _top_set(r: RowEvidence) -> set[int]:
    return {s.component_index
            for s in r.suspect_report.suspects[:_TOP_SUSPECTS]}


def _overlap(a: set[int], b: set[int]) -> float:
    if not a or not b:
        return 1.0          # an empty suspect set cannot contradict anything
    union = a | b
    return len(a & b) / len(union)


def _signature_dict(r: RowEvidence) -> dict:
    return {
        "columns": sorted({m.get("column", "?") for m in r.mismatches}),
        "selects": [list(s) for s in r.selects],
        "category": r.category,
    }


def cluster_rows(rows: list[RowEvidence], *, cap: int = _MAX_CLUSTERS,
                 min_overlap: float = _MIN_OVERLAP):
    """Group failing rows by signature; returns (clusters, notes).

    Rows sharing (mismatch columns, selects, category) join the same
    cluster when their top-suspect sets overlap enough — disjoint
    suspects split them, since one sub-agent cannot chase two unrelated
    causes. Past `cap` clusters, the smallest folds into its
    best-overlapping neighbor so every failing row stays represented."""
    notes: list[str] = []
    clusters: list[Cluster] = []
    meta: list[dict] = []                      # parallel: {"key", "tops"}
    for r in sorted(rows, key=lambda x: x.row_index):
        key = _bucket_key(r)
        tops = _top_set(r)
        placed = False
        for c, m in zip(clusters, meta):
            if m["key"] == key and _overlap(tops, m["tops"]) >= min_overlap:
                c.rows.append(r)
                m["tops"] |= tops
                placed = True
                break
        if not placed:
            clusters.append(Cluster(signature=_signature_dict(r), rows=[r]))
            meta.append({"key": key, "tops": set(tops)})

    while len(clusters) > cap:
        i = min(range(len(clusters)),
                key=lambda k: (len(clusters[k].rows), -k))
        small, small_meta = clusters.pop(i), meta.pop(i)
        j = max(range(len(clusters)),
                key=lambda k: (_overlap(small_meta["tops"], meta[k]["tops"]),
                               len(clusters[k].rows), -k))
        clusters[j].rows.extend(small.rows)
        clusters[j].rows.sort(key=lambda x: x.row_index)
        clusters[j].folded_rows += len(small.rows)
        meta[j]["tops"] |= small_meta["tops"]
        notes.append(
            f"cluster cap {cap}: folded {len(small.rows)} row(s) with "
            f"signature {small.signature} into a neighboring cluster."
        )

    for c in clusters:
        c.merged = merge_reports([r.suspect_report for r in c.rows])
    return clusters, notes


# ---------------------------------------------------------------------------
# Payload (frozen §3 sub-agent INPUT)
# ---------------------------------------------------------------------------

def compact_circuit_facts(circuit, netlist=None, graph=None) -> dict:
    """The §3 `circuit` field: the same compact CircuitFacts view the L2
    explainer sends (inventory, io, subcircuits, selectors, ...)."""
    return _compact_facts(extract_facts(circuit, netlist, graph).to_dict())


# Semantic (non-visual) attributes worth showing the sub-agent per
# suspect. Data words are deliberately summarized, never listed: a ROM's
# stored program may be tool-injected official content that must not
# leave the backend.
_SUSPECT_ATTR_KEYS = (
    "AddrBits", "Bits", "Inputs", "Selector Bits",
    "Input Splitting", "Output Splitting", "Value", "intFormat",
    "inverterConfig", "flipSelPos", "Signed", "inputBits", "outputBits",
    "splitterSpreading", "isProgramCounter",
)


def _suspect_attrs(comp, *, hide_rom_words: bool = False) -> dict:
    """The semantics-bearing attributes of one suspect component. This is
    what let the sub-agent derive a decode table: without the splitter's
    bit ranges and the ROM's shape, the r37 live run guessed the word
    layout wrong on every try.

    ``hide_rom_words`` (a rom-injected run): stored words came from the
    official course program and must never transit where a reply could
    echo them back to the student — only the shape ships. A student's
    OWN small table stays visible (r38) so a partially-wrong word can be
    convicted and corrected instead of guessed."""
    out: dict = {}
    for k in _SUSPECT_ATTR_KEYS:
        v = comp.attributes.get(k)
        if v not in (None, "", []):
            out[k] = v
    if comp.element_name in ("ROM", "RAM", "EEPROM", "RAMDualPort"):
        raw = comp.attributes.get("Data", "")
        tokens = [t for t in str(raw or "").replace(",", " ").split() if t]
        out["data_words_stored"] = len(tokens)
        if not tokens:
            out["data_note"] = (
                "Data is EMPTY - every address reads 0. To program it, "
                "use exactly: {\"op\": \"change_attribute\", "
                "\"component_index\": <this component>, \"name\": "
                "\"Data\", \"value\": \"w0,w1,...\"} - comma-separated "
                "hex words, address 0 first, one word PER ADDRESS the "
                "circuit uses (the attribute name is Data, never Value).")
        elif not hide_rom_words and len(tokens) <= 32:
            out["stored_words"] = ",".join(tokens)
    return out


def suspect_wiring(circuit, netlist, indices: list[int],
                   rep_rows: list["RowEvidence"] | None = None,
                   hide_rom_words: bool = False) -> list[dict]:
    """Pin-level connection truth for the suspect components — for every
    suspect, each pin and the far ends of its net (the netlist already
    merges across tunnels). This is what lets the sub-agent tell WHICH of
    four identical Consts feeds the adder's c_i instead of guessing.

    When representative rows are given, every pin also carries its ACTUAL
    value on those rows (``values: {row_index: value}``) — the join
    between wiring and net_values done FOR the model, so same-scored
    suspects separate by behavior: the gate whose output contradicts what
    its element kind computes from its inputs is the prime candidate
    (the LED-lab lesson, same shape as the 4-Const lesson)."""
    out: list[dict] = []
    for idx in indices:
        if not (0 <= idx < len(circuit.components)):
            continue
        comp = circuit.components[idx]
        attrs = _suspect_attrs(comp, hide_rom_words=hide_rom_words)
        pins: list[dict] = []
        for net in netlist.nets:
            mine = [p for p in net.pins if p.component_index == idx]
            if not mine:
                continue
            others = []
            for q in net.pins:
                if q.component_index == idx:
                    continue
                qc = circuit.components[q.component_index]
                others.append({
                    "component_index": q.component_index,
                    "element": qc.element_name,
                    "label": qc.label,
                    "pin": q.pin_name,
                    "direction": q.direction,
                })
            values = {}
            for r in rep_rows or []:
                nv = r.net_values.get(str(net.net_id))
                if nv is not None:
                    values[str(r.row_index)] = nv.get("value")
            for p in mine:
                entry = {"pin": p.pin_name, "direction": p.direction,
                         "net_id": net.net_id,
                         "connects_to": others[:6]}
                if values:
                    entry["values"] = values
                pins.append(entry)
        rec = {"component_index": idx, "element": comp.element_name,
               "label": comp.label, "pins": pins}
        if attrs:
            rec["attrs"] = attrs
        out.append(rec)
    return out


def build_payload(compact_circuit: dict, spec: TestSpec, cluster: Cluster, *,
                  circuit=None, netlist=None,
                  max_representatives: int = _MAX_REPRESENTATIVES,
                  hide_rom_words: bool = False) -> dict:
    reps = cluster.rows[:max_representatives]
    payload = {
        "contract": CONTRACT,
        "circuit": compact_circuit,
        "testcase": {"name": spec.name, "headers": list(spec.headers)},
        "cluster": {
            "rows": [
                {"index": r.row_index, "raw": r.raw,
                 "mismatches": r.mismatches}
                for r in cluster.rows
            ],
            "representative_evidence": [
                {"row_index": r.row_index,
                 "net_values": r.net_values,
                 "unresolved_nets": r.unresolved_nets,
                 "outputs": r.outputs}
                for r in reps
            ],
        },
        "suspects": cluster.merged.to_dict(),
    }
    if circuit is not None and netlist is not None:
        payload["suspect_wiring"] = suspect_wiring(
            circuit, netlist, cluster.merged.suspect_indices(),
            rep_rows=reps, hide_rom_words=hide_rom_words)
    return payload


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def assemble_evidence(circuit, netlist, graph, spec: TestSpec, *,
                      manifest: dict | None = None,
                      failing_indices: list[int] | None = None,
                      jar_mismatches: dict[int, list[dict]] | None = None,
                      compact_circuit: dict | None = None,
                      max_clusters: int = _MAX_CLUSTERS,
                      max_representatives: int = _MAX_REPRESENTATIVES,
                      max_failing: int = GROSS_MAX_FAILING,
                      lazy_exempt: bool = False,
                      hide_rom_words: bool = False) -> EvidenceResult:
    """Steps 2-4 of the coordinator pipeline for one testcase.

    ``failing_indices`` (row line_index values) is the jar's per-row
    verdict and takes authority when given; ``jar_mismatches`` maps a
    failing index to Digital's expected-vs-found cells for it. Without
    ``failing_indices`` the evaluator sweeps every well-formed row
    itself, so the whole pipeline runs with no Digital.jar.

    ``lazy_exempt`` skips the gross-check lazy gate entirely (failing
    rows go straight to analysis). Instructor ruling (r37.1, marked
    temporary): control-unit files always analyze — the decode table is
    the one lab where Mode A must engage no matter how gross the
    failure shape looks."""
    res = EvidenceResult(spec_name=spec.name, headers=list(spec.headers))
    bindings = match_variables_to_io(spec.headers, circuit)
    rows_by_index = {r.line_index: r for r in spec.rows if not r.is_malformed}

    sims: dict[int, SimResult] = {}
    row_mismatch_columns: list[set] | None = None
    row_mismatch_cells: dict[int, list[dict]] | None = None
    if failing_indices is None:
        failing: list[int] = []
        row_mismatch_columns = []
        row_mismatch_cells = {}
        for row in spec.rows:
            if row.is_malformed:
                res.notes.append(
                    f"row {row.line_index} is malformed and was skipped.")
                continue
            try:
                sim = simulate_sequential(circuit, netlist, graph, spec,
                                          row.line_index)
            except Exception as exc:
                res.notes.append(
                    f"row {row.line_index}: evaluator error "
                    f"{type(exc).__name__}: {exc}")
                continue
            _outs, mism = _outputs_report(spec, bindings, row, sim)
            if mism:
                failing.append(row.line_index)
                sims[row.line_index] = sim
                row_mismatch_columns.append(
                    {m.get("column") for m in mism if m.get("column")})
                row_mismatch_cells[row.line_index] = list(mism)
    else:
        failing = list(failing_indices)
        if jar_mismatches:
            sets = [
                {c.get("column") for c in (jar_mismatches.get(i) or [])
                 if isinstance(c, dict) and c.get("column")}
                for i in failing]
            row_mismatch_columns = sets if any(sets) else None
            if row_mismatch_columns is not None:
                row_mismatch_cells = {
                    i: [c for c in (jar_mismatches.get(i) or [])
                        if isinstance(c, dict)]
                    for i in failing}
    res.failing_count = len(failing)

    if not failing:
        res.mode = "clear"
        return res

    if lazy_exempt:
        res.notes.append(
            "lazy-gate checks skipped for this file (control-unit rule).")
    else:
        flags = gross_check(circuit, spec, len(failing),
                            max_failing=max_failing,
                            row_mismatch_columns=row_mismatch_columns,
                            row_mismatch_cells=row_mismatch_cells)
        if flags:
            res.mode = "lazy"
            res.gross_flags = flags
            return res

    res.mode = "analysis"
    sel_cols = select_columns(circuit, netlist, spec, bindings)
    evidence: list[RowEvidence] = []
    for idx in failing:
        row = rows_by_index.get(idx)
        if row is None:
            res.notes.append(
                f"failing row {idx} is missing or malformed in the spec; "
                f"skipped.")
            continue
        jar_cells = (jar_mismatches or {}).get(idx)
        try:
            evidence.append(_row_evidence(
                circuit, netlist, graph, spec, bindings, row,
                sel_cols=sel_cols, manifest=manifest,
                sim=sims.get(idx), jar_cells=jar_cells, notes=res.notes,
            ))
        except Exception as exc:
            res.notes.append(
                f"row {idx}: evaluator error {type(exc).__name__}: {exc} — "
                f"evidence limited to Digital's cells.")
            evidence.append(RowEvidence(
                row_index=idx, raw=row.raw,
                mismatches=[dict(c) for c in jar_cells or []],
            ))

    if _frozen_trunk(spec, bindings, row_mismatch_cells) and evidence:
        # One mechanism (the frozen-trunk premise) → ONE cluster: the
        # sub-agent must repair EVERY failing row at once, so a partial
        # decode table gets refuted-with-evidence instead of shipping as
        # a per-row "verified" card (r37 live-run lesson: two confirmed
        # cards each fixed one row of s008's ROM, none fixed the file).
        first = evidence[0]
        clusters = [Cluster(signature=_signature_dict(first),
                            rows=list(evidence))]
        res.notes.append(
            "all failing rows show one frozen output stage — analyzed "
            "as a single cluster so a fix must repair every row.")
    else:
        clusters, cnotes = cluster_rows(evidence, cap=max_clusters)
        res.notes.extend(cnotes)
    res.clusters = clusters
    if compact_circuit is None:
        compact_circuit = compact_circuit_facts(circuit, netlist, graph)
    res.payloads = [
        build_payload(compact_circuit, spec, c, circuit=circuit,
                      netlist=netlist,
                      max_representatives=max_representatives,
                      hide_rom_words=hide_rom_words)
        for c in clusters
    ]
    return res


def assemble_evidence_for_file(dig_path, *, spec_name: str | None = None,
                               spec_index: int = 0,
                               manifest: dict | None = None,
                               use_manifest: bool = True,
                               **kwargs) -> EvidenceResult:
    """Parse + build + assemble for one file. ``spec_index`` counts every
    Testcase element in document order, exactly like /api/simulate; a
    ``spec_name`` match wins over the index. The manifest is looked up by
    the file's own name and its subcircuit references unless one is
    passed (or ``use_manifest=False``)."""
    circuit = parse_dig_file(str(dig_path))
    netlist = build_netlist(circuit)
    graph = build_signal_graph(circuit, netlist)
    specs = extract_test_specs(circuit)
    if not specs:
        raise ValueError(f"{Path(dig_path).name} has no testcase.")
    spec = None
    if spec_name is not None:
        spec = next((s for s in specs if s.name == spec_name), None)
        if spec is None:
            names = ", ".join(repr(s.name) for s in specs)
            raise ValueError(
                f"No testcase named {spec_name!r}; saw: {names}")
    else:
        if spec_index < 0 or spec_index >= len(specs):
            raise ValueError(
                f"spec_index {spec_index} out of range "
                f"({len(specs)} testcase(s)).")
        spec = specs[spec_index]
    if manifest is None and use_manifest:
        names = {Path(dig_path).name}
        for sub in circuit.subcircuits:
            ref = getattr(sub, "reference", None)
            if ref:
                names.add(ref)
        from dlc.l3.manifest import tree_element_names
        manifest = find_manifest(names,
                                 element_names=tree_element_names(circuit))
    return assemble_evidence(circuit, netlist, graph, spec,
                             manifest=manifest, **kwargs)
