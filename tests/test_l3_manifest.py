"""Per-lab manifest engine (dlc/l3/manifest.py): fingerprints, category
coverage, and reference verdicts. Deterministic; no network, no jar."""

import json

from dlc.l3 import manifest as mf
from dlc.parser.dig_parser import parse_dig_file
from dlc.testing.spec import extract_test_specs

_AND = "data/sample_circuits/tier1_minimal/single_and.dig"


def test_hash_ignores_comments_and_whitespace():
    a = mf.normalized_test_hash("A B Y\n0 0 0\n1 1 1")
    b = mf.normalized_test_hash("A  B   Y   # header\n\n0 0 0\n1 1 1  # ok\n")
    c = mf.normalized_test_hash("A B Y\n0 0 0\n1 1 0")
    assert a == b and a != c


def test_official_status_official_modified_and_unknown():
    m = {"official_tests": {"x.dig": mf.normalized_test_hash("A Y\n0 0")}}
    assert mf.official_status(m, "x.dig", "A Y # hi\n0  0") == "official"
    assert mf.official_status(m, "x.dig", "A Y\n0 1") == "modified"
    assert mf.official_status(m, "y.dig", "A Y\n0 0") is None
    assert mf.official_status(None, "x.dig", "A Y\n0 0") is None


def test_category_coverage_counts_touched_and_missing():
    spec = extract_test_specs(parse_dig_file(_AND))[0]   # all 4 AND vectors
    m = {"categories": {"single_and.dig": [
        {"name": "both_high", "when": {"A": 1, "B": "0b1"}},
        {"name": "impossible", "when": {"A": "0x2"}},    # 1-bit input, never 2
    ]}}
    cc = mf.category_coverage(m, "single_and.dig", spec)
    assert cc == {"total": 2, "touched": ["both_high"],
                  "missing": ["impossible"]}
    # unknown column => predicates can't bind => stay silent
    m2 = {"categories": {"single_and.dig": [{"name": "x", "when": {"Q": 1}}]}}
    assert mf.category_coverage(m2, "single_and.dig", spec) is None
    assert mf.category_coverage(None, "single_and.dig", spec) is None


def test_reference_verdicts_agree_disagree(tmp_path):
    ref = tmp_path / "single_and.dig"
    ref.write_text(open(_AND).read())
    v = mf.reference_row_verdicts(ref, ["A", "B", "Y"],
                                  ["1 0 0", "1 0 1", "1 1 1"])
    assert [x["verdict"] for x in v] == ["agrees", "disagrees", "agrees"]
    assert "reference computes 0" in v[1]["detail"]


def test_manifest_discovery_by_filename(tmp_path, monkeypatch):
    d = tmp_path / "m"
    d.mkdir()
    (d / "lab.json").write_text(json.dumps(
        {"lab": "t", "applies_to": ["foo.dig"]}))
    (d / "broken.json").write_text("{nope")
    monkeypatch.setenv("DLC_MANIFEST_DIR", str(d))
    assert mf.find_manifest({"foo.dig", "bar.dig"})["lab"] == "t"
    assert mf.find_manifest({"bar.dig"}) is None


# ---------------------------------------------------------------------------
# RV32I word quality (lazy gate) + verified encoding knowledge
# ---------------------------------------------------------------------------

from pathlib import Path                                     # noqa: E402

_LAB5 = json.loads(
    Path("data/manifests/lab5.json").read_text(encoding="utf-8"))


def test_lazy_word_reason_flags_only_the_certain_cases():
    # add x5, x0, x0 — both operands zero: every op computes 0
    assert "BOTH operands" in mf.lazy_word_reason(_LAB5, 0x2B3)
    # add x0, x0, x0 — all zero, rd discarded too
    assert "BOTH operands" in mf.lazy_word_reason(_LAB5, 0x33)
    # addi x0, x0, 7 — discards the result AND reads only x0
    assert "discards its result" in mf.lazy_word_reason(_LAB5, 0x00700013)
    # addi x5, x0, 0 — (0, 0) again, via the immediate
    assert "immediate 0" in mf.lazy_word_reason(_LAB5, 0x00000293)
    # addi x5, x0, 7 — the idiomatic register loader must NEVER be flagged
    assert mf.lazy_word_reason(_LAB5, 0x00700293) is None
    # addi x0, x5, 0 — the lab's READ-BACK idiom (exposes x5 on a read
    # port); the official program itself uses it — must NEVER be flagged
    assert mf.lazy_word_reason(_LAB5, 0x00028013) is None
    # add x0, x5, x6 — discarded result but LIVE sources on the read ports
    assert mf.lazy_word_reason(_LAB5, 0x00628033) is None
    # add x7, x5, x6 / sub x8, x5, x6 — real operands, fine
    assert mf.lazy_word_reason(_LAB5, 0x006283B3) is None
    assert mf.lazy_word_reason(_LAB5, 0x40628433) is None
    # not a lab instruction => not judged (the decode gate handles it)
    assert mf.lazy_word_reason(_LAB5, 0xFFFFFFFF) is None
    # a manifest without rd/rs1/rs2 fields refuses to judge
    slim = {**_LAB5, "program_decode": {
        "categories_from": "control-unit.dig",
        "fields": {"opcode": [0, 7], "funct3": [12, 3], "funct7": [25, 7]}}}
    assert mf.lazy_word_reason(slim, 0x2B3) is None


def test_encode_category_word_round_trips_every_lab5_category():
    cats = [c["name"] for c in _LAB5["categories"]["control-unit.dig"]]
    assert len(cats) == 8
    for name in cats:
        w = mf.encode_category_word(_LAB5, name, rd=7, rs1=5, rs2=6, imm=9)
        assert w is not None, name
        d = mf.decode_program_word(_LAB5, w)
        assert d["category"] == name
        assert d["fields"]["rd"] == 7 and d["fields"]["rs1"] == 5
    # golden values against the RV32I spec
    assert mf.encode_category_word(_LAB5, "addi", rd=5, rs1=0, imm=7) == 0x00700293
    assert mf.encode_category_word(_LAB5, "add", rd=7, rs1=5, rs2=6) == 0x006283B3
    # unknown category / undecodable manifest refuse instead of guessing
    assert mf.encode_category_word(_LAB5, "nope", rd=1, rs1=1) is None
    assert mf.encode_category_word({}, "add", rd=1, rs1=1) is None


def test_category_word_examples_verified_chained_and_never_lazy():
    missing = ["addi", "add", "slt", "andi"]
    ex = mf.category_word_examples(_LAB5, missing)
    assert [e["category"] for e in ex] == missing
    words = {}
    for e in ex:
        w = int(e["word"], 16)
        d = mf.decode_program_word(_LAB5, w)
        assert d["category"] == e["category"]
        assert mf.lazy_word_reason(_LAB5, w) is None
        words[e["category"]] = d["fields"]
    # the addi example doubles as the setup loader the others read from
    assert words["add"]["rs1"] == words["addi"]["rd"]
    assert "x0" in ex[0]["asm"]                       # addi xN, x0, 7
    # rd avoidance: registers already written by the program are skipped
    taken = mf.encode_category_word(_LAB5, "addi", rd=5, rs1=0, imm=1)
    ex2 = mf.category_word_examples(_LAB5, ["add"], [taken])
    assert all(mf.decode_program_word(_LAB5, int(e["word"], 16))
               ["fields"]["rd"] != 5 for e in ex2)
    assert mf.category_word_examples(_LAB5, []) == []


def test_constant_registers_prove_values_and_never_lie():
    w_x4 = 0xFEC00213                                # addi x4, x0, -20
    w_x5 = mf.encode_category_word(_LAB5, "addi", rd=5, rs1=4, imm=30)
    w_x6 = mf.encode_category_word(_LAB5, "sub", rd=6, rs1=4, rs2=5)
    w_x7 = mf.encode_category_word(_LAB5, "slti", rd=7, rs1=4, imm=0)
    known = mf.constant_registers(_LAB5, [w_x4, w_x5, w_x6, w_x7])
    assert known[0] == 0
    assert known[4] == 0xFFFFFFEC                    # -20, two's complement
    assert known[5] == 10                            # -20 + 30
    assert known[6] == 0xFFFFFFE2                    # -20 - 10 = -30
    assert known[7] == 1                             # -20 < 0 signed
    # a word the walker can't track DROPS its rd instead of lying
    lui_x4 = 0x00000237                              # opcode 0x37, rd bits = 4
    known2 = mf.constant_registers(_LAB5, [w_x4, w_x5, lui_x4])
    assert 4 not in known2 and known2[5] == 10
    # no rd field configured => refuses to track anything
    slim = {**_LAB5, "program_decode": {
        "categories_from": "control-unit.dig",
        "fields": {"opcode": [0, 7], "funct3": [12, 3], "funct7": [25, 7]}}}
    assert mf.constant_registers(slim, [w_x4]) == {}


def test_category_word_examples_prefer_program_proven_sources():
    # official program proves x4 = -20 and x6 = 7 (constant propagation)
    w_x4 = 0xFEC00213                                # addi x4, x0, -20
    w_x6 = mf.encode_category_word(_LAB5, "addi", rd=6, rs1=0, imm=7)
    ex = mf.category_word_examples(_LAB5, ["add", "sub"], [w_x4, w_x6])
    for e in ex:
        f = mf.decode_program_word(_LAB5, int(e["word"], 16))["fields"]
        assert (f["rs1"], f["rs2"]) == (4, 6)        # live, distinct values
        assert f["rd"] not in (4, 6)                 # never clobber a source
        assert e["reads"] == {"x4": -20, "x6": 7}    # proven ground truth
    # a source whose value becomes unprovable is abandoned
    lui_x4 = 0x00000237
    ex2 = mf.category_word_examples(_LAB5, ["add"], [w_x4, w_x6, lui_x4])
    f2 = mf.decode_program_word(_LAB5, int(ex2[0]["word"], 16))["fields"]
    assert 4 not in (f2["rs1"], f2["rs2"])
    assert "reads" not in ex2[0]                     # nothing proven => silent
