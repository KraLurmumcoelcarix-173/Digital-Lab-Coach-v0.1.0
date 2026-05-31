"""Static Layer 2 component encyclopedia."""


def _binary_table_2(op) -> list[dict]:
    return [
        {"in": [a, b], "out": op(a, b)}
        for a in (0, 1) for b in (0, 1)
    ]

def _binary_table_3(op) -> list[dict]:
    return [
        {"in": [a, b, c], "out": op(op(a, b), c)}
        for a in (0, 1) for b in (0, 1) for c in (0, 1)
    ]


_AND   = lambda a, b: a & b
_OR    = lambda a, b: a | b
_XOR   = lambda a, b: a ^ b
_NAND  = lambda a, b: 1 - (a & b)
_NOR   = lambda a, b: 1 - (a | b)
_XNOR  = lambda a, b: 1 - (a ^ b)


COMPONENT_KB: dict[str, dict] = {

    "And": {
        "display_name": "AND gate",
        "image": "and.png",
        "description": (
            "Outputs HIGH only when ALL inputs are HIGH. The most "
            "basic conjunction primitive in any boolean expression."
        ),
        "transistor_count": "2N + 2 for an N-input AND",
        "transistor_note": "In CMOS this is a NAND followed by an inverter.",
        "port_summary": "N inputs -> 1 output",
        "extra": {
            "truth_table_2": _binary_table_2(_AND),
            "truth_table_3": _binary_table_3(_AND),
        },
    },
    "Or": {
        "display_name": "OR gate",
        "image": "or.png",
        "description": (
            "Outputs HIGH when ANY input is HIGH. The disjunction "
            "primitive; pairs with AND in sum-of-products forms."
        ),
        "transistor_count": "2N + 2 for an N-input OR",
        "transistor_note": "Implemented as a NOR followed by an inverter.",
        "port_summary": "N inputs -> 1 output",
        "extra": {
            "truth_table_2": _binary_table_2(_OR),
            "truth_table_3": _binary_table_3(_OR),
        },
    },
    "XOr": {
        "display_name": "XOR gate",
        "image": "xor.png",
        "description": (
            "Outputs HIGH when an ODD number of inputs are HIGH. "
            "Workhorse of arithmetic (sum bit of a half-adder is XOR)."
        ),
        "transistor_count": "~12 for the standard 2-input CMOS XOR",
        "transistor_note": (
            "Several implementations exist (e.g. 8T pass-transistor); 12T "
            "static CMOS is the textbook form."
        ),
        "port_summary": "N inputs -> 1 output",
        "extra": {
            "truth_table_2": _binary_table_2(_XOR),
            "truth_table_3": _binary_table_3(_XOR),
        },
    },
    "NAnd": {
        "display_name": "NAND gate",
        "image": "nand.png",
        "description": (
            "Inverted AND: outputs LOW only when ALL inputs are HIGH. "
            "Functionally complete -- any boolean function can be "
            "built from NAND alone."
        ),
        "transistor_count": "2N for an N-input NAND",
        "transistor_note": "N PMOS in parallel, N NMOS in series.",
        "port_summary": "N inputs -> 1 output",
        "extra": {
            "truth_table_2": _binary_table_2(_NAND),
            "truth_table_3": _binary_table_3(_NAND),
        },
    },
    "NOr": {
        "display_name": "NOR gate",
        "image": "nor.png",
        "description": (
            "Inverted OR: outputs HIGH only when ALL inputs are LOW. "
            "Also functionally complete (NOR alone can build anything)."
        ),
        "transistor_count": "2N for an N-input NOR",
        "transistor_note": "N PMOS in series, N NMOS in parallel.",
        "port_summary": "N inputs -> 1 output",
        "extra": {
            "truth_table_2": _binary_table_2(_NOR),
            "truth_table_3": _binary_table_3(_NOR),
        },
    },
    "XNOr": {
        "display_name": "XNOR gate",
        "image": "xnor.png",
        "description": (
            "Inverted XOR: outputs HIGH when inputs MATCH. Often used "
            "as a 1-bit equality comparator inside larger Comparator "
            "circuits."
        ),
        "transistor_count": "~12 for the standard 2-input CMOS XNOR",
        "port_summary": "N inputs -> 1 output",
        "extra": {
            "truth_table_2": _binary_table_2(_XNOR),
            "truth_table_3": _binary_table_3(_XNOR),
        },
    },
    "Not": {
        "display_name": "NOT gate (inverter)",
        "image": "not.png",
        "description": (
            "Flips a single bit. Simplest CMOS gate -- 1 PMOS on top, "
            "1 NMOS on bottom."
        ),
        "transistor_count": "2 (1 PMOS + 1 NMOS)",
        "port_summary": "1 input -> 1 output",
        "extra": {
            "truth_table_2": [
                {"in": [0], "out": 1},
                {"in": [1], "out": 0},
            ],
            "truth_table_3": [],
        },
    },

    "In": {
        "display_name": "Input port",
        "image": "in.png",
        "description": (
            "Top-level circuit input. The test bench drives values "
            "into this port; downstream logic reads them."
        ),
        "transistor_count": "N/A (pad / pin)",
        "port_summary": "0 inputs -> 1 output (Bits-wide)",
        "extra": {
            "behavior_example": (
                "If Label=A, Bits=4: testcase column 'A' supplies a "
                "4-bit value each row; the wire fans out to anything "
                "connected to this In's output pin."
            ),
        },
    },
    "Out": {
        "display_name": "Output port",
        "image": "out.png",
        "description": (
            "Top-level circuit output. Whatever drives this pin is "
            "what the test bench observes; an undriven Out is a "
            "classic 'forgot to wire' bug."
        ),
        "transistor_count": "N/A (pad / pin)",
        "port_summary": "1 input -> 0 outputs",
        "extra": {
            "behavior_example": (
                "If Label=Sum, Bits=4 and the upstream driver = 1100, "
                "the test bench reads Sum=12 (decimal) on that row."
            ),
        },
    },

    "Multiplexer": {
        "display_name": "Multiplexer (MUX)",
        "image": "mux.png",
        "description": (
            "Selects one of 2^N data inputs based on a Selector Bits "
            "control. The fundamental selection element behind ALUs, "
            "register-file read ports, and pipeline forwarding paths."
        ),
        "transistor_count": "~6-12 per data bit per input lane (varies by implementation)",
        "transistor_note": "Pass-transistor MUXes can be smaller; CMOS tree MUXes scale by 2^N.",
        "port_summary": "2^N data inputs + N select bits -> 1 output",
        "extra": {
            "behavior_example": (
                "Selector Bits=1, Bits=4: in0=0101, in1=1100, sel=1 "
                "-> out=1100. Selector Bits=2 gives 4 inputs (in0..in3)."
            ),
        },
    },
    "Decoder": {
        "display_name": "Decoder (1-of-N)",
        "image": "decoder.png",
        "description": (
            "Takes an N-bit selector and asserts exactly one of 2^N "
            "outputs HIGH. The address-decode block in a register "
            "file's write port is exactly this."
        ),
        "transistor_count": "~2^N AND-gate equivalents",
        "port_summary": "N select bits -> 2^N outputs",
        "extra": {
            "behavior_example": (
                "Selector Bits=3, sel=5: out_5=1, every other out=0. "
                "Useful for enabling exactly one register on a write."
            ),
        },
    },
    "PriorityEncoder": {
        "display_name": "Priority encoder",
        "image": "priority_encoder.png",
        "description": (
            "Scans 2^N priority inputs from low index to high and "
            "outputs the index of the highest-priority asserted line. "
            "Used in interrupt controllers and reservation stations."
        ),
        "transistor_count": "depends on width; grows ~N * 2^N",
        "port_summary": "2^N priority inputs -> N-bit num output",
        "extra": {
            "behavior_example": (
                "Selector Bits=3, inputs in_0=0,in_1=1,in_2=0,in_3=1 "
                "-> num=3 (highest set bit's index)."
            ),
        },
    },

    "Add": {
        "display_name": "Adder",
        "image": "adder.png",
        "description": (
            "N-bit ripple-carry or fast adder. Adds a + b + c_i and "
            "produces s and c_o. The arithmetic heart of any ALU."
        ),
        "transistor_count": "~28 per bit for a textbook full adder",
        "transistor_note": "Carry-lookahead variants trade transistors for speed.",
        "port_summary": "a, b (N bits), c_i (1 bit) -> s (N bits), c_o (1 bit)",
        "extra": {
            "behavior_example": (
                "Bits=4: a=0011 (3), b=0101 (5), c_i=0 -> s=1000 (8), "
                "c_o=0. Add c_i=1 instead -> s=1001 (9)."
            ),
        },
    },
    "BarrelShifter": {
        "display_name": "Barrel shifter",
        "image": "barrel_shifter.png",
        "description": (
            "Shifts an N-bit input by 0 to N-1 positions in one clock. "
            "Supports the shift-by-immediate instructions of RISC-V."
        ),
        "transistor_count": "~2 N log2(N) for a Mux-tree implementation",
        "port_summary": "in (N bits), sh (log2(N) bits) -> out (N bits)",
        "extra": {
            "behavior_example": (
                "Bits=8, in=00001111, sh=2 -> out=00111100 (logical "
                "shift left by 2)."
            ),
        },
    },
    "Comparator": {
        "display_name": "Comparator",
        "image": "comparator.png",
        "description": (
            "Compares two N-bit inputs and asserts one of three "
            "outputs: greater, equal, or less. Used wherever you "
            "need 'is x == y?' or 'x < threshold?' decisions."
        ),
        "transistor_count": "~6 N + reduction tree",
        "port_summary": "A, B (N bits) -> gr, eq, le (1 bit each)",
        "extra": {
            "behavior_example": (
                "Bits=4: A=0110 (6), B=0011 (3) -> gr=1, eq=0, le=0. "
                "Same A with B=0110 -> eq=1, the other two 0."
            ),
        },
    },
    "BitExtender": {
        "display_name": "Bit extender",
        "image": "bit_extender.png",
        "description": (
            "Sign- or zero-extends a narrow signal to a wider one. "
            "RISC-V immediate generation uses this constantly."
        ),
        "transistor_count": "N/A (pure wiring + replicated MSB)",
        "port_summary": "in (M bits) -> out (N bits, N > M)",
        "extra": {
            "behavior_example": (
                "Sign-extend in=1011 (4 bits, -5 signed) to 8 bits: "
                "out=11111011. Zero-extend would give 00001011."
            ),
        },
    },

    "Register": {
        "display_name": "Register",
        "image": "register.png",
        "description": (
            "N-bit edge-triggered storage element. On the rising edge "
            "of C, if en=1, D is latched into Q. The fundamental "
            "sequential cell behind pipelines and state machines."
        ),
        "transistor_count": "~24 per bit for a master-slave D flip-flop",
        "port_summary": "D (N bits), C (1), en (1) -> Q (N bits)",
        "extra": {
            "behavior_example": (
                "Bits=4, en=1. At cycle 0 D=0011, Q reflects whatever "
                "was previously latched. After clock edge, Q=0011. If "
                "D changes to 0101 with en=0, Q stays 0011."
            ),
        },
    },
    "ROM": {
        "display_name": "ROM",
        "image": "rom.png",
        "description": (
            "Read-only memory holding 2^AddrBits words of Bits width. "
            "Used for instruction memory and lookup tables. DLC warns "
            "if Data is empty (Digital does not flag this on its own)."
        ),
        "transistor_count": "~6 per bit cell (NOR-style)",
        "port_summary": "A (AddrBits), sel (1) -> D (Bits)",
        "extra": {
            "behavior_example": (
                "AddrBits=3, Bits=8, Data=82,86,80,81,87,c2,c7,c0. "
                "A=2 -> D=0x80 = 10000000."
            ),
        },
    },

    "Splitter": {
        "display_name": "Splitter / merger",
        "image": "splitter.png",
        "description": (
            "Slices an N-bit bus into smaller groups or merges several "
            "narrow signals into a wider one. No logic -- pure routing."
        ),
        "transistor_count": "N/A (wires only)",
        "port_summary": "configurable (Input Splitting / Output Splitting)",
        "extra": {
            "behavior_example": (
                "Input Splitting=4, Output Splitting=1,1,1,1: a 4-bit "
                "bus in=1101 produces out0=1 (LSB), out1=0, out2=1, "
                "out3=1 (MSB)."
            ),
        },
    },
    "Tunnel": {
        "display_name": "Tunnel (named net)",
        "image": "tunnel.png",
        "description": (
            "Wireless connector. Two tunnels with the same NetName "
            "are electrically the same wire even across the canvas. "
            "Reduces visual clutter in large circuits."
        ),
        "transistor_count": "N/A (wires only)",
        "port_summary": "1 bidirectional pin",
        "extra": {
            "behavior_example": (
                "Two Tunnels both named 'WriteReg' anywhere on the "
                "canvas form one electrical net -- whatever drives "
                "one shows up at the other."
            ),
        },
    },
    "Clock": {
        "display_name": "Clock",
        "image": "clock.png",
        "description": (
            "Generates a periodic HIGH/LOW signal that drives "
            "Register / RAM / ROM update edges. The heartbeat of "
            "every sequential circuit."
        ),
        "transistor_count": "N/A (signal source)",
        "port_summary": "0 inputs -> 1 output (1 bit)",
        "extra": {
            "behavior_example": (
                "Each test row with a 'C' token in the clock column "
                "advances every Register's stored value to whatever D "
                "presents on the rising edge."
            ),
        },
    },
    "Const": {
        "display_name": "Constant",
        "image": "const.png",
        "description": (
            "Hard-wired constant value. Typical uses: Const(1) on a "
            "Register's en pin to always-write; Const(0) as a default "
            "Mux input."
        ),
        "transistor_count": "N/A (wires to power / ground)",
        "port_summary": "0 inputs -> 1 output (Bits-wide)",
        "extra": {
            "behavior_example": (
                "Value=5, Bits=4 -> out=0101 forever. Value=0, Bits=32 "
                "is the canonical 'feed a zero into the Mux' pattern."
            ),
        },
    },
    "Ground": {
        "display_name": "Ground (logical 0)",
        "image": "ground.png",
        "description": (
            "Drives a constant 0 of the given width. Equivalent to "
            "Const(0) but typographically distinct."
        ),
        "transistor_count": "N/A (rail)",
        "port_summary": "0 inputs -> 1 output (Bits-wide)",
        "extra": {
            "behavior_example": (
                "Bits=32, drives 32'b0 to wherever it connects -- often "
                "tied to the unused 0th register in a register file."
            ),
        },
    },
    "VDD": {
        "display_name": "VDD (logical 1)",
        "image": "vdd.png",
        "description": (
            "Drives a constant 1. Often used on an en pin to mean "
            "'always enabled'."
        ),
        "transistor_count": "N/A (rail)",
        "port_summary": "0 inputs -> 1 output (Bits-wide)",
        "extra": {
            "behavior_example": (
                "Bits=1, drives 1 -- the canonical 'always-write' "
                "wire on a Register's en pin."
            ),
        },
    },
    "Seven-Seg": {
        "display_name": "Seven-segment display",
        "image": "seven_seg.png",
        "description": (
            "Seven independently-driven segments forming a digit. "
            "Useful for Lab-1-style LED projects that display a "
            "decoded hex digit."
        ),
        "transistor_count": "N/A (output device)",
        "port_summary": "7 segment inputs + optional dp -> visible digit",
        "extra": {
            "behavior_example": (
                "Drive a=1,b=1,c=1,d=0,e=0,f=1,g=1 to display the "
                "digit '3'. Drive all 7 high to display '8'."
            ),
        },
    },

}


ANNOTATION_TYPES = {"Testcase", "Rectangle"}


def library_for_inventory(inventory: dict) -> list[dict]:
    seen: set[str] = set()
    cards: list[dict] = []
    for element_name, count in (inventory or {}).items():
        if element_name in ANNOTATION_TYPES:
            continue
        if element_name.endswith(".dig"):
            key = "Subcircuit"
            if key in seen:
                continue
            seen.add(key)
            cards.append({
                "key": key,
                "display_name": "Subcircuit reference",
                "image": "subcircuit.png",
                "description": (
                    "Instance of another .dig file. The parent treats "
                    "it as a black box; pin directions come from the "
                    "child circuit's In/Out elements."
                ),
                "transistor_count": "depends on child circuit",
                "port_summary": "varies (child's I/O list)",
                "extra": {
                    "behavior_example": (
                        "Hover the subcircuit instance on the Dashboard "
                        "graph to see its individual pin labels."
                    ),
                },
                "count": count,
            })
            continue
        if element_name in seen:
            continue
        seen.add(element_name)
        entry = COMPONENT_KB.get(element_name)
        if entry is None:
            cards.append({
                "key": element_name,
                "display_name": element_name,
                "image": "placeholder.png",
                "description": (
                    f"No encyclopedia entry yet for '{element_name}'. "
                    f"Add it to dlc/web/component_kb.py."
                ),
                "transistor_count": "?",
                "port_summary": "?",
                "extra": {},
                "count": count,
            })
            continue
        cards.append({
            "key": element_name,
            **entry,
            "count": count,
        })
    return cards