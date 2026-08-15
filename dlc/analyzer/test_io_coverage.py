"""Advisory card: top-level I/O pins the tests never touch.

Live conviction: extra In pins rode along that no
test column ever drove — the tool said nothing about them until the
per-row run surfaced confusing residue. Either such a pin is redundant
and should go, or the testcase is incomplete and misses the behavior it
carries. Both deserve a nudge; neither is a structural ERROR, so this
check emits a WARNING (rendered as the purple card) and never blocks
tests, Mode A, or Mode B — the gates count errors only.

Runs at upload time from the server (not inside check_all_l1): it needs
the EFFECTIVE testcase — the file's own rows, or the official rows the
grader would inject for this filename — which the pure structural pass
does not know about."""

from dlc.analyzer.wire_completeness import Issue, IssueSeverity
from dlc.testing.spec import match_variables_to_io

KIND = "test_io_coverage"


def check_test_io_coverage(circuit, specs) -> list[Issue]:
    """One purple card naming every top-level In/Out no spec column
    binds. Silent when there are no usable specs, when every pin is
    covered, or when NO header matched at all (a fully-unbound testcase
    is a naming mismatch — the rename guidance owns that story, and
    listing every pin here would just shout over it)."""
    specs = [s for s in (specs or []) if getattr(s, "headers", None)]
    if not specs:
        return []
    bound: set[int] = set()
    for spec in specs:
        for b in match_variables_to_io(spec.headers, circuit).values():
            if b.component_index is not None:
                bound.add(b.component_index)
    if not bound:
        return []

    def name_of(comp) -> str:
        if comp.label:
            return comp.label
        pos = getattr(comp, "position", None)
        at = f" at ({pos.x}, {pos.y})" if pos is not None else ""
        return f"(unlabeled {comp.element_name}{at})"

    ins: list[str] = []
    outs: list[str] = []
    indices: list[int] = []
    for i, comp in enumerate(circuit.components):
        if i in bound:
            continue
        if comp.is_input():
            ins.append(name_of(comp))
            indices.append(i)
        elif comp.is_output():
            outs.append(name_of(comp))
            indices.append(i)
    if not indices:
        return []

    parts: list[str] = []
    if ins:
        parts.append(("inputs " if len(ins) > 1 else "input ")
                     + ", ".join(ins))
    if outs:
        parts.append(("outputs " if len(outs) > 1 else "output ")
                     + ", ".join(outs))
    n = len(indices)
    return [Issue(
        kind=KIND,
        severity=IssueSeverity.WARNING,
        title=f"{n} pin{'s' if n != 1 else ''} never used by the tests",
        message=("The tests drive or check every other top-level pin "
                 "but never touch " + " or ".join(parts) + ". Either "
                 "these pins are redundant, or the tests are incomplete "
                 "and miss the behavior they carry."),
        component_indices=sorted(indices),
        suggested_fix=("If the pin belongs to this lab's interface, add "
                       "test columns/rows that exercise it; if it does "
                       "not, remove the pin."),
    )]
