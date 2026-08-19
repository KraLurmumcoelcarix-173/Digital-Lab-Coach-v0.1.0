"""
Fingerprint helper CLI (dlc/fingerprint.py): turns .dig files into
ready-to-ship official-test entries; the sha1 must be byte-identical to
what the store/manifest machinery matches with.
"""

import json
import subprocess
import sys

from dlc import fingerprint as fp
from dlc.l3.manifest import normalized_test_hash
from dlc.parser.dig_parser import parse_dig_file
from dlc.testing.spec import extract_test_specs

_AND = "data/sample_circuits/tier1_minimal/single_and.dig"


def _and_spec():
    return extract_test_specs(parse_dig_file(_AND))[0]


def test_defaults_shape_written_to_file(tmp_path):
    out = tmp_path / "defaults.json"
    rc = fp.main([_AND, "-o", str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    spec = _and_spec()
    entry = data["single_and.dig"]
    assert entry["content"] == spec.raw_data_string
    assert entry["sha1"] == normalized_test_hash(spec.raw_data_string)
    assert set(entry) == {"content", "sha1"}


def test_hashes_only_prints_manifest_shape(capsys):
    rc = fp.main(["--hashes-only", _AND])
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {"single_and.dig":
                    normalized_test_hash(_and_spec().raw_data_string)}


def test_entry_feeds_the_official_store(tmp_path, monkeypatch):
    from dlc.l3 import official_store as ost
    out = tmp_path / "defaults.json"
    assert fp.main([_AND, "-o", str(out)]) == 0
    monkeypatch.setenv("DLC_OFFICIAL_DEFAULTS_PATH", str(out))
    assert ost.status_for("single_and.dig",
                          _and_spec().raw_data_string) == "official"
    assert ost.status_for("single_and.dig", "A B Y\n1 1 0") == "modified"


def test_bad_files_are_skipped_with_nonzero_exit(tmp_path, capsys):
    no_tc = tmp_path / "empty.dig"
    no_tc.write_text("<circuit><visualElements/><wires/></circuit>")
    rc = fp.main([str(no_tc), str(tmp_path / "ghost.dig"), _AND])
    err = capsys.readouterr().err
    assert rc == 1
    assert "SKIPPED empty.dig" in err and "SKIPPED ghost.dig" in err
    assert "single_and.dig" in err
    rc = fp.main([str(no_tc)])
    assert rc == 1


def test_runs_as_a_module():
    r = subprocess.run([sys.executable, "-m", "dlc.fingerprint", _AND],
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0
    assert "single_and.dig" in json.loads(r.stdout)
    assert "fingerprint" in r.stderr
