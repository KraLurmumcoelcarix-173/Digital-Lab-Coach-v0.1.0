"""
Parser: reads a .dig XML file and produces a Circuit object.

Handles:
  - Components and wires from the file's literal XML.
  - Subcircuit references: when a component's element_name ends in .dig,
    the referenced file is recursively loaded from the parent's folder.

Caches resolved subcircuits in a single parse session to avoid re-parsing
the same file twice and to detect circular references.
"""
from pathlib import Path
from lxml import etree
from dlc.parser.models import Circuit, Component, SubcircuitReference, Wire, Position


def _parse_attributes(element_attributes) -> dict:
    """
    Convert a <elementAttributes> XML block into a plain Python dict.

    Example XML:
      <elementAttributes>
        <entry>
          <string>Label</string>
          <string>A</string>
        </entry>
        <entry>
          <string>Bits</string>
          <int>4</int>
        </entry>
      </elementAttributes>

    Produces: {"Label": "A", "Bits": 4}

    Anything failed to be recognized now will be added in in the future development or placed as raw text.
    """
    attributes = {}
    if element_attributes is None:
        return attributes

    for entry in element_attributes.findall("entry"):
        children = list(entry)
        if len(children) < 2:
            continue
        key_element = children[0]
        value_element = children[1]
        key = key_element.text
        if key is None:
            continue
        value_tag = value_element.tag
        value_text = value_element.text

        if value_tag in ("int", "long"):
            value = int(value_text) if value_text is not None else 0
        elif value_tag == "boolean":
            value = (value_text == "true")
        elif value_tag == "string":
            value = value_text if value_text is not None else ""
        elif value_tag == "rotation":
            rot_attr = value_element.get("rotation")
            value = int(rot_attr) if rot_attr is not None else 0
        elif value_tag == "testData":
            data_string = value_element.find("dataString")
            if data_string is not None and data_string.text is not None:
                value = data_string.text
            else:
                value = ""
        else:
            # Unknown value type with no geometry (e.g. <shape>).
            if len(value_element) > 0:
                value = None
            else:
                value = value_text if value_text is not None else value_tag

        attributes[key] = value

    return attributes


def _parse_position(element) -> Position:
    """
    Read a <pos x="..." y="..."/> child from an element.
    Every visualElement and every wire endpoint has one.
    """
    pos = element.find("pos")
    if pos is None:
        # Won't possibly happen
        raise ValueError("Element missing <pos> tag")
    x = int(pos.get("x"))
    y = int(pos.get("y"))
    return Position(x, y)


def _parse_component(visual_element) -> Component:
    element_name = visual_element.findtext("elementName")
    if element_name is None:
        raise ValueError("visualElement missing <elementName>")

    attributes = _parse_attributes(visual_element.find("elementAttributes"))
    position = _parse_position(visual_element)
    label = attributes.get("Label")

    return Component(
        element_name=element_name,
        position=position,
        attributes=attributes,
        label=label,
    )


def _parse_wire(wire_element) -> Wire:
    """
    Turn one <wire> block into a Wire object.
    """
    p1 = wire_element.find("p1")
    p2 = wire_element.find("p2")
    if p1 is None or p2 is None:
        raise ValueError("wire missing p1 or p2")

    return Wire(
        p1=Position(int(p1.get("x")), int(p1.get("y"))),
        p2=Position(int(p2.get("x")), int(p2.get("y"))),
    )


def parse_dig_file(path: str) -> Circuit:
    """
    Public entry point: parse a .dig file (and any subcircuits it references).

    Subcircuits are resolved relative to the parent file's folder.
    Each parse session uses its own cache, so the same subcircuit referenced
    multiple times is loaded only once, and circular references are detected.
    """

    cache: dict[str, Circuit] = {}
    in_progress: set[str] = set()
    return _parse_with_cache(path, cache, in_progress)

def _parse_with_cache(
    path: str,
    cache: dict[str, Circuit],
    in_progress: set[str],
) -> Circuit:
    """
    Internal recursive parser. Maintains:
      cache:       absolute_path -> already-parsed Circuit
      in_progress: absolute_paths currently being parsed (for cycle detection)
    """
    abs_path = str(Path(path).resolve())

    # Cycle detection: if we're already parsing this file higher up the stack
    if abs_path in in_progress:
        raise ValueError(f"Circular subcircuit reference: {abs_path}")

    if abs_path in cache:
        return cache[abs_path]

    in_progress.add(abs_path)
    try:
        circuit = _parse_one_file(path, cache, in_progress)
    finally:
        in_progress.discard(abs_path)

    cache[abs_path] = circuit
    return circuit

def _resolve_subcircuit_path(parent_dir: Path, reference: str) -> tuple[str | None, str | None]:
    """
    Find a referenced .dig file under the parent's folder tree.

    Digital stores the reference as a bare filename (e.g. "alu.dig") even when
    the file lives in a subfolder. So we search:
      1. Directly in parent_dir.
      2. Recursively in subfolders of parent_dir with any depth.

    Returns: (resolved_absolute_path, error_message). One is always None.
      - On success: (path_string, None)
      - On miss:    (None, "Referenced file not found: <name>")
      - On ambiguity (multiple matches in subfolders):
          (closest_match_path, "Ambiguous: multiple matches found for ...")
        We still proceed with the closest match so parsing continues, but
        flag it so higher layers can warn the students.

    The reference may also literally contain a path separator (e.g. "subs/x.dig")
    if a future Digital version writes one, parser handles that as a direct path lookup.
    """
    # Case 1: the reference already includes a path separator. 
    if "/" in reference or "\\" in reference:
        direct = parent_dir / reference
        if direct.exists():
            return str(direct.resolve()), None
        return None, f"Referenced file not found: {direct}"

    # Case 2: bare filename. Check same folder first.
    same_folder = parent_dir / reference
    if same_folder.exists():
        return str(same_folder.resolve()), None

    # Case 3: search subfolders recursively.
    matches = list(parent_dir.rglob(reference))
    if len(matches) == 1:
        return str(matches[0].resolve()), None
    if len(matches) > 1:
        # Pick the shallowest.
        matches.sort(key=lambda p: len(p.parts))
        chosen = matches[0]
        error = (
            f"Ambiguous reference '{reference}': found at "
            f"{[str(m) for m in matches]}; using shallowest match."
        )
        return str(chosen.resolve()), error

    return None, f"Referenced file not found: {reference} (searched {parent_dir} recursively)"

def _parse_one_file(
    path: str,
    cache: dict[str, Circuit],
    in_progress: set[str],
) -> Circuit:
    """Parse a single .dig file and recursively resolve any subcircuit references it contains."""
    tree = etree.parse(path)
    root = tree.getroot()

    if root.tag != "circuit":
        raise ValueError(f"Expected root <circuit>, got <{root.tag}>")

    version_text = root.findtext("version")
    format_version = int(version_text) if version_text is not None else None

    components: list[Component] = []
    visual_elements = root.find("visualElements")
    if visual_elements is not None:
        for ve in visual_elements.findall("visualElement"):
            components.append(_parse_component(ve))

    wires: list[Wire] = []
    wires_block = root.find("wires")
    if wires_block is not None:
        for w in wires_block.findall("wire"):
            wires.append(_parse_wire(w))
 
    circuit = Circuit(
        components=components,
        wires=wires,
        source_path=path,
        format_version=format_version,
        subcircuits=[],
    )

    # Resolve subcircuit references. They are components whose element_name
    # ends in ".dig". Their file is expected to live in no higher folder level 
    # compared to the parent.
    parent_dir = Path(path).parent
    for comp in components:
        if not comp.element_name.endswith(".dig"):
            continue

        ref = comp.element_name
        resolved_path, resolution_error = _resolve_subcircuit_path(parent_dir, ref)

        sub_ref = SubcircuitReference(
            reference=ref,
            parent_component=comp,
            resolved_path=resolved_path,
            resolution_error=resolution_error,
        )

        if resolved_path is not None:
            try:
                child = _parse_with_cache(resolved_path, cache, in_progress)
                sub_ref.child_circuit = child
            except Exception as e:
                sub_ref.resolution_error = f"{type(e).__name__}: {e}"

        circuit.subcircuits.append(sub_ref)
    return circuit