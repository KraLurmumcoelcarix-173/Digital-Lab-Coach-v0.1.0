import json
from pathlib import Path

from dlc.l3 import manifest as mf
from dlc.l3 import oracle
from dlc.l3.oracle import InjectedRow

_RV = json.loads(Path("data/manifests/cpu_new.json").read_text(encoding="utf-8"))
_LAB5 = json.loads(Path("data/manifests/cpu.json").read_text(encoding="utf-8"))
_HEADERS = ["clk", "PCout", "ReadData1", "ReadData2"]

W = lambda cat, **kw: mf.encode_category_word(_RV, cat, **kw)


def _program():
    return [
        W("addi", rd=1, rs1=0, imm=5),
        W("addi", rd=2, rs1=0, imm=12),
        W("addi", rd=1, rs1=1, imm=-1),
        W("bne", rs1=1, rs2=0, imm=-4),
        W("sw", rs1=0, rs2=2, imm=0),
        W("lw", rd=3, rs1=0, imm=0),
        W("jal", rd=0, imm=0),
        W("addi", rd=9, rs1=0, imm=1),
    ]


def test_manifest_attaches_to_the_tree_that_shares_most_files():
    new_tree = {"cpu_new.dig", "controlunit.dig", "immgen.dig", "alu.dig",
                "register-file.dig", "add-sub.dig", "slt-unit.dig"}
    old_tree = {"cpu.dig", "control-unit.dig", "alu.dig", "register-file.dig",
                "add-sub.dig", "boolean-unit.dig"}
    assert mf.find_manifest(new_tree)["lab"] == "lab5-rv32i"
    assert mf.find_manifest(old_tree)["lab"] == "lab5"


def test_every_rv32i_category_round_trips_and_examples_exist():
    cats = [c["name"] for c in _RV["categories"]["controlunit.dig"]]
    assert len(cats) == 37
    for name in cats:
        w = W(name, rd=7, rs1=5, rs2=6, imm=8)
        assert w is not None, name
        assert mf.decode_program_word(_RV, w)["category"] == name
    ex = mf.category_word_examples(_RV, cats, [])
    assert {e["category"] for e in ex} == set(cats)
    for e in ex:
        for hexword in e.get("words") or [e["word"]]:
            assert mf.lazy_word_reason(_RV, int(hexword, 16)) is None
    jalr = next(e for e in ex if e["category"] == "jalr")
    assert len(jalr["words"]) == 2 and jalr["asm"].startswith("auipc")


def test_immediates_of_every_format_round_trip():
    for cls, imm in (("I", -20), ("STORE", 0x7EC), ("BR", -8), ("JAL", 0x87EC),
                     ("JAL", -1048576), ("JALR", 13)):
        assert mf.word_immediate(mf._place_immediate(cls, imm), cls) == imm
    lui = mf.word_immediate(mf._place_immediate("LUI", 0xABCDE000), "LUI")
    assert lui & 0xFFFFFFFF == 0xABCDE000 and lui < 0


def test_lazy_rules_for_the_new_classes():
    assert "itself" in mf.lazy_word_reason(_RV, W("beq", rs1=3, rs2=3, imm=4))
    assert mf.lazy_word_reason(_RV, W("beq", rs1=3, rs2=4, imm=4)) is None
    assert "halt" in mf.lazy_word_reason(_RV, W("jal", rd=0, imm=0))
    assert mf.lazy_word_reason(_RV, W("jal", rd=5, imm=4)) is None
    assert "x0" in mf.lazy_word_reason(_RV, W("lw", rd=0, rs1=0, imm=0))
    assert mf.lazy_word_reason(_RV, W("sw", rs1=0, rs2=5, imm=0)) is None


def test_interpreter_follows_the_loop_and_keeps_memory():
    run = mf.execute_program(_RV, _program())
    assert run["halted"] and run["pc"] == 6 * 4
    assert run["regs"][1] == 0 and run["regs"][2] == 12 and run["regs"][3] == 12
    pcs = [t["pc"] for t in run["trace"]]
    assert 28 not in pcs and run["regs"][9] == 0
    assert pcs.count(8) == 5 and pcs.count(12) == 5
    assert run["trace"][2]["a"] == 5
    assert mf.program_halt_index(_RV, _program()) == 6
    assert mf.program_halt_index(_RV, _program()[:6]) is None


def test_interpreter_stops_honestly_on_unknown_values():
    words = [0x00000237,
             mf.encode_category_word(_LAB5, "addi", rd=5, rs1=4, imm=1)]
    run = mf.execute_program(_LAB5, words)
    assert 4 not in run["regs"] and 5 not in run["regs"]
    words_rv = [0x00000237, W("beq", rs1=4, rs2=5, imm=4), W("addi", rd=6, rs1=0, imm=1)]
    run2 = mf.execute_program(_RV, words_rv)          # lui IS defined here
    assert run2["regs"].get(4) == 0 and run2["regs"].get(6) == 1
    assert run2["stopped"] == "pc left the program" and not run2["halted"]
    run3 = mf.execute_program(_RV, [W("lw", rd=4, rs1=7, imm=0), W("beq", rs1=4, rs2=5, imm=8)],
                              regs={0: 0, 5: 1})
    assert run3["stopped"] == "branch on an unknown value"


def test_constant_registers_treat_appended_words_as_before_the_halt():
    base = _program()
    extra = [W("addi", rd=8, rs1=2, imm=1), W("jal", rd=9, imm=4)]
    known = mf.constant_registers(_RV, base + extra, appended=len(extra))
    assert known[8] == 13
    assert known[9] == 6 * 4 + 4 + 4                    # jal sits at the old halt + 4
    assert mf.constant_registers(_RV, base + extra)[8] != 13 or 9 not in mf.constant_registers(_RV, base + extra)
    assert mf.spliced_program(_RV, base, extra) == base[:6] + extra + base[6:]
    assert mf.spliced_program(_RV, base[:6], extra) == base[:6] + extra


def test_synthesis_lands_before_the_halt_with_shifted_pcs():
    syn = mf.synthesize_program_extension(
        _RV, _program(), ["lbu", "jalr", "auipc"], _HEADERS, "clk")
    assert syn["insert_at"] == 6
    assert syn["pc_shift"] == 4 * len(syn["program_words"])
    assert len(syn["rows"]) == len(syn["program_words"])
    pcs = [int(r.split()[1], 16) for r in syn["rows"]]
    assert pcs == [24 + 4 * i for i in range(len(pcs))]
    assert "halt loop" in syn["why"]
    spliced = mf.spliced_program(_RV, _program(),
                                 [int(w, 16) for w in syn["program_words"]])
    assert mf.execute_program(_RV, spliced)["pc"] == 24 + syn["pc_shift"]
    plain = mf.synthesize_program_extension(
        _RV, _program()[:6], ["lbu"], _HEADERS, "clk")
    assert "insert_at" not in plain and plain["rows"][0].startswith("C 0x18 ")


def _dig_with_rom_and_rows(words, rows):
    data = ",".join(f"{w:x}" for w in words)
    body = "\n".join(rows)
    return (
        '<?xml version="1.0" encoding="utf-8"?><circuit><version>2</version>'
        '<attributes/><visualElements>'
        '<visualElement><elementName>ROM</elementName><elementAttributes>'
        '<entry><string>isProgramMemory</string><boolean>true</boolean></entry>'
        f'<entry><string>Data</string><data>{data}</data></entry>'
        '<entry><string>AddrBits</string><int>8</int></entry>'
        '</elementAttributes><pos x="0" y="0"/></visualElement>'
        '<visualElement><elementName>Testcase</elementName><elementAttributes>'
        '<entry><string>Testdata</string><testData><dataString>'
        f'clk PCout ReadData1 ReadData2\n{body}\n</dataString></testData>'
        '</entry></elementAttributes><pos x="0" y="100"/></visualElement>'
        '</visualElements><wires/></circuit>')


def test_rom_splice_and_row_insertion_text_transforms():
    src = _dig_with_rom_and_rows([0x11, 0x22, 0x6F], [
        "0 0 0 0", "C 0x4 1 1", "C 0x8 2 2   # halt", "C 0x8 2 2"])
    out = oracle.extend_program_rom_text(src, [0xAA, 0xBB], insert_at=2)
    assert oracle.find_program_rom(out)[0] == [0x11, 0x22, 0xAA, 0xBB, 0x6F]
    appended = oracle.extend_program_rom_text(src, [0xAA])
    assert oracle.find_program_rom(appended)[0] == [0x11, 0x22, 0x6F, 0xAA]

    rows_raw = ["0 0 0 0", "C 0x4 1 1", "C 0x8 2 2   # halt", "C 0x8 2 2"]
    rewrite = oracle.shift_pc_cells(rows_raw, ["clk", "PCout", "ReadData1", "ReadData2"],
                                    "PCout", 0x8, 8)
    assert rewrite == {2: "C 0x10 2 2 # halt", 3: "C 0x10 2 2"}
    new = oracle.inject_rows_text(
        src, 0, [InjectedRow("C 0x8 9 9"), InjectedRow("C 0xC 8 8")],
        insert_before=2, rewrite=rewrite)
    inner = new.split("<dataString>")[1].split("</dataString>")[0]
    lines = [l for l in inner.split("\n") if l.strip()]
    assert lines == ["clk PCout ReadData1 ReadData2", "0 0 0 0", "C 0x4 1 1",
                     "C 0x8 9 9", "C 0xC 8 8", "C 0x10 2 2 # halt", "C 0x10 2 2"]
    tail = oracle.inject_rows_text(src, 0, [InjectedRow("C 0xC 3 3")])
    assert tail.split("</dataString>")[0].rstrip().endswith("C 0xC 3 3")
