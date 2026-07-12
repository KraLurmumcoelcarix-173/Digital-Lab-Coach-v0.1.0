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
