"""Mode B row proposer (dlc/l3/proposer.py) + POST /api/l3/propose.

The model is always faked — these tests never touch the network. What IS
real: the coverage scan grounding, the prompt build, and the untrusted-
output pipeline (JSON parsing, header validation, value-level dedupe).
"""

import json

import pytest
from fastapi.testclient import TestClient

from dlc.l3 import proposer
from dlc.l3.coverage import scan_tree_coverage
from dlc.web import server
from dlc.web.server import app

client = TestClient(app)

_AND = "data/sample_circuits/tier1_minimal/single_and.dig"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("DLC_LIMITS_PATH", str(tmp_path / "limits.json"))
    monkeypatch.setenv("DLC_TELEMETRY_DB", str(tmp_path / "telemetry.db"))
    monkeypatch.delenv("DLC_ENFORCE_LIMITS", raising=False)


def _fake(text):
    def call(prompt, **kw):
        return {"ok": True, "text": text, "error": None,
                "usage": None, "model": kw.get("model")}
    return call


def _fake_two(first, second):
    """First call (proposals), second call (self-check derivation)."""
    n = {"v": 0}
    def call(prompt, **kw):
        n["v"] += 1
        return {"ok": True, "text": first if n["v"] == 1 else second,
                "error": None, "usage": None, "model": kw.get("model")}
    return call


# ---------------------------------------------------------------------------
# Grounding + prompt build
# ---------------------------------------------------------------------------

def test_targets_carry_headers_io_and_existing_rows():
    report = scan_tree_coverage(_AND)
    targets = proposer.build_targets(report)
    assert len(targets) == 1
    t = targets[0]
    assert t["file"] == "single_and.dig"
    assert t["headers"] == ["A", "B", "Y"]
    assert len(t["existing_rows"]) == 4
    assert {i["label"] for i in t["inputs"]} == {"A", "B"}


def test_prompt_embeds_report_and_targets_not_paths():
    report = scan_tree_coverage(_AND)
    targets = proposer.build_targets(report)
    prompt = proposer.build_prompt(report, targets)
    assert "SPOILER GUARD" in prompt
    assert '"single_and.dig"' in prompt
    assert '"existing_rows"' in prompt
    assert "sample_circuits" not in prompt      # no filesystem paths leak


# ---------------------------------------------------------------------------
# Untrusted-output pipeline
# ---------------------------------------------------------------------------

def test_parse_tolerates_fences_and_prose():
    text = ("Here you go:\n```json\n"
            + json.dumps({"proposals": [
                {"file": "f.dig", "spec_name": "T", "rows": [" 1 0 0 "],
                 "why": "gap"}]})
            + "\n```\nGood luck!")
    got = proposer.parse_proposals(text)
    assert got == [{"file": "f.dig", "spec_name": "T",
                    "rows": ["1 0 0"], "why": "gap"}]


def test_parse_returns_empty_on_garbage():
    assert proposer.parse_proposals("no json here") == []
    assert proposer.parse_proposals('{"proposals": "nope"}') == []
    assert proposer.parse_proposals("") == []


def test_validate_drops_illegal_duplicate_and_mistargeted_rows():
    report = scan_tree_coverage(_AND)
    targets = proposer.build_targets(report)
    proposals = [
        {"file": "single_and.dig", "spec_name": targets[0]["spec_name"],
         # 0x1 0x1 0x1 duplicates existing "1 1 1" by VALUE; "1 0" is short;
         # "1 0 1" is new and legal (even though the circuit will fail it —
         # that is 2.4's job to discover, not the validator's).
         "rows": ["0x1 0x1 0x1", "1 0", "1 0 1"], "why": "w"},
        {"file": "ghost.dig", "spec_name": "T", "rows": ["1 1 1"], "why": "w"},
    ]
    valid, rejected = proposer.validate_and_dedupe(proposals, targets)
    assert len(valid) == 1 and valid[0]["rows"] == ["1 0 1"]
    reasons = " | ".join(r["reason"] for r in rejected)
    assert "duplicate" in reasons
    assert "columns" in reasons                 # the short row
    assert "unknown target" in reasons          # ghost.dig


def test_total_row_cap_applies_across_groups():
    report = scan_tree_coverage(_AND)
    targets = proposer.build_targets(report)
    sp = targets[0]["spec_name"]
    many = [{"file": "single_and.dig", "spec_name": sp,
             "rows": [f"1 0 {i % 2}" if i else "1 0 1" for i in range(10)],
             "why": "w"}]
    # craft 10 distinct legal rows: vary A/B/Y bits
    many[0]["rows"] = ["1 0 1", "0 1 1", "1 1 0", "0 0 1",
                       "0x1 0x0 0x0", "0b0 0b1 0b0", "1 1 1",  # dup of existing
                       "0 0 0",                                 # dup of existing
                       "1 0 0", "0 1 0"]
    valid, rejected = proposer.validate_and_dedupe(many, targets)
    n_valid = sum(len(v["rows"]) for v in valid)
    assert n_valid <= proposer._MAX_TOTAL_ROWS


# ---------------------------------------------------------------------------
# propose_rows end to end (fake model)
# ---------------------------------------------------------------------------

def _two_row_and(tmp_path):
    """single_and with only 2 of 4 vectors tested — leaves correct new
    rows free to propose (the full fixture has all four, so every correct
    row would be a duplicate)."""
    import re as _re
    xml = open(_AND).read()
    m = _re.search(r"<dataString>.*?</dataString>", xml, _re.S)
    xml2 = xml.replace(m.group(0),
                       "<dataString>A B Y\n0 0 0\n1 1 1</dataString>")
    p = tmp_path / "single_and.dig"
    p.write_text(xml2)
    return p


def test_propose_rows_happy_path_with_fake_model(tmp_path):
    student = _two_row_and(tmp_path)
    spec_name = proposer.build_targets(
        scan_tree_coverage(str(student)))[0]["spec_name"]
    text = json.dumps({"proposals": [
        {"file": "single_and.dig", "spec_name": spec_name,
         "rows": ["1 0 0"], "why": "boundary case A=1,B=0"},
    ]})
    selfcheck = json.dumps({"rows": [{"index": 0, "outputs": {"Y": "0"}}]})
    out = proposer.propose_rows(str(student), call=_fake_two(text, selfcheck))
    assert out["ok"] is True
    assert len(out["proposals"]) == 1
    assert out["proposals"][0]["rows"] == ["1 0 0"]
    assert any("self-check confirmed" in n for n in out["notes"])
    assert out["error"] is None


def test_selfcheck_drops_a_row_it_cannot_reproduce(tmp_path):
    # DELIBERATELY WRONG row (1 AND 0 is NOT 1) — this test PROVES the
    # self-check gate kills hallucinated expectations before display.
    student = _two_row_and(tmp_path)
    spec_name = proposer.build_targets(
        scan_tree_coverage(str(student)))[0]["spec_name"]
    text = json.dumps({"proposals": [
        {"file": "single_and.dig", "spec_name": spec_name,
         "rows": ["1 0 1"], "why": "gap"},
    ]})
    selfcheck = json.dumps({"rows": [{"index": 0, "outputs": {"Y": "0"}}]})
    out = proposer.propose_rows(str(student), call=_fake_two(text, selfcheck))
    assert out["ok"] is True
    assert out["proposals"] == []
    assert any("self-check" in r["reason"] for r in out["rejected"])


def test_reference_gate_drops_rows_the_reference_refutes(tmp_path, monkeypatch):
    student = _two_row_and(tmp_path)
    # Reference: the correct full circuit, under the manifest's applies_to
    # name, in a dir DLC_REFERENCE_DIR points at.
    refdir = tmp_path / "refs"
    refdir.mkdir()
    (refdir / "single_and.dig").write_text(open(_AND).read())
    monkeypatch.setenv("DLC_REFERENCE_DIR", str(refdir))
    mdir = tmp_path / "manifests"
    mdir.mkdir()
    (mdir / "t.json").write_text(json.dumps({
        "lab": "t", "applies_to": ["single_and.dig"],
        "categories": {}, "official_tests": {}, "reference_dir": None,
    }))
    monkeypatch.setenv("DLC_MANIFEST_DIR", str(mdir))

    spec_name = proposer.build_targets(scan_tree_coverage(str(student)))[0]["spec_name"]
    text = json.dumps({"proposals": [
        {"file": "single_and.dig", "spec_name": spec_name,
         # "1 0 1" is DELIBERATELY WRONG (proves the reference kills it);
         # "0 1 0" is correct and must survive.
         "rows": ["1 0 1", "0 1 0"], "why": "gaps"},
    ]})
    selfcheck = json.dumps({"rows": [{"index": 0, "outputs": {"Y": "0"}}]})
    out = proposer.propose_rows(str(student), call=_fake_two(text, selfcheck))
    assert out["ok"] is True
    assert len(out["proposals"]) == 1
    assert out["proposals"][0]["rows"] == ["0 1 0"]
    assert any("lab reference" in r["reason"] for r in out["rejected"])


def test_propose_rows_refuses_when_scan_has_flags():
    bug = ("data/sample_circuits/30_bug_benchmark/"
           "bug1_meaningless_mux_in3/tier3_calculator.dig")
    out = proposer.propose_rows(bug, call=_fake("{}"))
    assert out["ok"] is False
    assert "disagree" in out["error"]


def test_propose_rows_survives_model_failure_and_garbage():
    def dead(prompt, **kw):
        return {"ok": False, "text": None, "error": "no key", "usage": None,
                "model": kw.get("model")}
    out = proposer.propose_rows(_AND, call=dead)
    assert out["ok"] is False and "no key" in out["error"]

    out2 = proposer.propose_rows(_AND, call=_fake("not json at all"))
    assert out2["ok"] is True and out2["proposals"] == []
    assert out2["notes"]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

def _upload_and():
    with open(_AND, "rb") as fh:
        r = client.post("/api/circuit",
                        files=[("files", ("single_and.dig", fh, "application/xml"))])
    assert r.status_code == 200
    return r.json()["session_id"]


def test_propose_endpoint_uses_the_proposer(monkeypatch):
    spec_name = proposer.build_targets(scan_tree_coverage(_AND))[0]["spec_name"]
    text = json.dumps({"proposals": [
        {"file": "single_and.dig", "spec_name": spec_name,
         "rows": ["0 1 0"], "why": "boundary"},
    ]})
    monkeypatch.setattr(proposer, "call_llm", _fake(text))
    sid = _upload_and()
    try:
        r = client.post("/api/l3/propose", json={
            "session_id": sid, "filename": "single_and.dig",
        })
        body = r.json()
        # "0 1 0" duplicates an existing row by value -> validator drops it
        assert body["ok"] is True
        assert body["proposals"] == []
        assert any("dropped" in n for n in body["notes"])
    finally:
        server._SESSIONS.pop(sid, None)


def test_propose_endpoint_404s_on_unknown_session():
    r = client.post("/api/l3/propose", json={
        "session_id": "nope", "filename": "x.dig",
    })
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# 2.10: program-extension proposals (cpu-like ROM-driven targets)
# ---------------------------------------------------------------------------

from dlc.testing.runner import find_digital_jar  # noqa: E402

_needs_jar = pytest.mark.skipif(
    find_digital_jar() is None, reason="Digital.jar not configured",
)


def _cpu_like_target():
    return {"file": "cpu.dig", "spec_name": "Test",
            "headers": ["clk", "ReadData1", "ReadData2"],
            "inputs": [],
            "outputs": [{"label": "ReadData1", "bits": 32},
                        {"label": "ReadData2", "bits": 32}],
            "existing_rows": [], "existing_rows_omitted": 0,
            "has_clock": True, "clock_col": "clk",
            "has_program_rom": True, "program_words": ["13"],
            "rom_capacity_left": 100}


def test_program_group_survives_validation_atomically():
    t = _cpu_like_target()
    props = [{"file": "cpu.dig", "spec_name": "Test",
              "rows": ["C 1 2", "C 3 4"], "why": "r-type gap",
              "program_words": ["628e33", "0x40430EB3"]}]
    valid, rejected = proposer.validate_and_dedupe(props, [t])
    assert rejected == []
    assert len(valid) == 1
    assert valid[0]["program_words"] == ["628e33", "40430eb3"]  # normalized
    assert valid[0]["rows"] == ["C 1 2", "C 3 4"]


def test_program_group_rejections():
    t = _cpu_like_target()
    base = {"file": "cpu.dig", "spec_name": "Test", "why": "w"}

    def reason(p, target=t):
        v, r = proposer.validate_and_dedupe([p], [target])
        assert v == [] and len(r) == 1
        return r[0]["reason"]

    assert "one row per program word" in reason(
        {**base, "rows": ["C 1 2"], "program_words": ["13", "93"]})
    assert "hex" in reason(
        {**base, "rows": ["C 1 2"], "program_words": ["not-hex"]})
    assert "clock" in reason(                      # row must pulse the clock
        {**base, "rows": ["0 1 2"], "program_words": ["93"]})
    assert "duplicates an instruction" in reason(
        {**base, "rows": ["C 1 2"], "program_words": ["13"]})
    assert "only valid for clocked" in reason(
        {**base, "rows": ["C 1 2"], "program_words": ["13"]},
        target={**_cpu_like_target(), "has_program_rom": False})
    assert "does not fit" in reason(
        {**base, "rows": ["C 1 2"], "program_words": ["13"]},
        target={**_cpu_like_target(), "rom_capacity_left": 0})


def test_parse_proposals_carries_program_words():
    text = json.dumps({"proposals": [
        {"file": "cpu.dig", "spec_name": "Test", "rows": ["C 1 2"],
         "why": "w", "program_words": ["628e33"]}]})
    got = proposer.parse_proposals(text)
    assert got[0]["program_words"] == ["628e33"]
    # absent key stays absent on normal groups
    text2 = json.dumps({"proposals": [
        {"file": "f.dig", "spec_name": "T", "rows": ["1 0 0"], "why": "w"}]})
    assert "program_words" not in proposer.parse_proposals(text2)[0]


@_needs_jar
def test_inject_endpoint_as_second_builds_second_testcase():
    spec_name = proposer.build_targets(scan_tree_coverage(_AND))[0]["spec_name"]
    sid = _upload_and()
    try:
        r = client.post("/api/l3/inject", json={
            "session_id": sid, "filename": "single_and.dig",
            "spec_name": spec_name, "rows": ["1 0 0"], "as_second": True,
        })
        body = r.json()
        assert body["ok"] is True and body["outcome"] == "all_set"
        assert body["spec_name"] == f"{spec_name}_second"
        assert body["spec_index"] == 1
        assert body["base_spec"]["all_passed"] is True
        assert body["temp_filename"] == "single_and__coach.dig"
    finally:
        server._SESSIONS.pop(sid, None)

def _lab5ish_manifest():
    return {
        "lab": "t", "applies_to": ["cpu.dig"],
        "categories": {"control-unit.dig": [
            {"name": "add",  "when": {"opcode": "0b0110011", "funct3": "0b000",
                                      "funct7": "0b0000000"}},
            {"name": "addi", "when": {"opcode": "0b0010011", "funct3": "0b000"}},
        ]},
        "official_tests": {}, "reference_dir": None,
        "program_decode": {"categories_from": "control-unit.dig",
                           "fields": {"opcode": [0, 7], "funct3": [12, 3],
                                      "funct7": [25, 7]}},
    }


def test_program_word_decode_gate_and_word_info():
    m = _lab5ish_manifest()
    t = {**_cpu_like_target(), "program_words": ["fec00213"],
         "program_categories_missing": ["add"]}
    # a word the lab ISA does not define is rejected
    v, r = proposer.validate_and_dedupe(
        [{"file": "cpu.dig", "spec_name": "Test", "rows": ["C 1 2"],
          "why": "w", "program_words": ["ffffffff"]}], [t], manifest=m)
    assert v == [] and "not an instruction this lab defines" in r[0]["reason"]
    # a defined word that closes a missing category survives with word_info
    v, r = proposer.validate_and_dedupe(
        [{"file": "cpu.dig", "spec_name": "Test", "rows": ["C 1 2"],
          "why": "w", "program_words": ["628e33"]}], [t], manifest=m)
    assert r == [] and v[0]["word_info"] == [
        {"word": "628e33", "category": "add", "closes_gap": True}]
    # duplicating an existing program word names its category in the reason
    v, r = proposer.validate_and_dedupe(
        [{"file": "cpu.dig", "spec_name": "Test", "rows": ["C 1 2"],
          "why": "w", "program_words": ["fec00213"]}], [t], manifest=m)
    assert v == [] and "category 'addi'" in r[0]["reason"]


_PIPE = "data/sample_circuits/tier3_realistic/pipelined_adder_correct.dig"


def test_replay_gate_drops_state_ignorant_clocked_rows():
    # The 2-stage pipelined adder: Sum lags two rows. After the official
    # rows the pipe holds 0+0 twice, so an appended row must expect Sum=0.
    from dlc.parser.dig_parser import parse_dig_file
    from dlc.testing.spec import extract_test_specs
    spec = extract_test_specs(parse_dig_file(_PIPE))[0]
    t = {"file": "pipelined_adder_correct.dig", "spec_name": spec.name,
         "headers": list(spec.headers), "inputs": [], "outputs": [],
         "existing_rows": [], "existing_rows_omitted": 0,
         "has_clock": True, "clock_col": "Clk", "has_program_rom": False}
    paths = {"pipelined_adder_correct.dig": _PIPE}
    valid = [{"file": t["file"], "spec_name": spec.name,
              # row 1 RIGHT (two-stage pipe still flushing 0s), row 2 WRONG
              # (expects 5+5 immediately — it lands one row later), row 3
              # would be right but FOLLOWS a dropped row: prefix-keep drops
              # it too, because its context is gone.
              "rows": ["5 5 C 0", "0 0 C 99", "0 0 C 10"], "why": "w"}]
    kept, rejected, notes = proposer._replay_gate(valid, [], [], [t], paths)
    assert len(kept) == 1 and kept[0]["rows"] == ["5 5 C 0"]
    assert len(rejected) == 2
    assert "wrong expected value" in rejected[0]["reason"]
    assert "your circuit computes" in rejected[0]["reason"]
    assert "follows a dropped row" in rejected[1]["reason"]
    assert any("replaying your circuit" in n for n in notes)


def test_propose_model_default_and_override(monkeypatch):
    monkeypatch.delenv("DLC_L3_PROPOSE_MODEL", raising=False)
    assert proposer._propose_model() == proposer._PROPOSE_MODEL_FALLBACK
    monkeypatch.setenv("DLC_L3_PROPOSE_MODEL", "claude-sonnet-5")
    assert proposer._propose_model() == "claude-sonnet-5"
