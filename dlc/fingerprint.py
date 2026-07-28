"""Fingerprint helper CLI — instructor tooling for shipping
official tests in a fork.

Turns .dig files into ready-to-ship official-test entries:

    uv run python -m dlc.fingerprint cpu.dig register-file.dig
    uv run python -m dlc.fingerprint *.dig -o defaults.json
    uv run python -m dlc.fingerprint --hashes-only *.dig

Default output is the data/official_tests_defaults.json shape
({filename: {content, sha1}}); --hashes-only prints the manifest
official_tests shape ({filename: sha1}). The fingerprint is the SAME
normalized sha1 Mode B matches with (comments and whitespace ignored),
and the same one the Settings list shows next to each saved test.

Each file contributes its FIRST testcase — the one Mode B scans.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def fingerprint_file(path: str) -> dict:
    """{content, sha1, rows} for the file's first testcase.
    Raises ValueError when the file has no testcase (or can't parse)."""
    from dlc.l3.manifest import normalized_test_hash
    from dlc.parser.dig_parser import parse_dig_file
    from dlc.testing.spec import extract_test_specs
    try:
        specs = extract_test_specs(parse_dig_file(path))
    except Exception as exc:
        raise ValueError(f"could not parse: {exc}")
    if not specs:
        raise ValueError("no testcase in this file")
    spec = specs[0]
    return {"content": spec.raw_data_string,
            "sha1": normalized_test_hash(spec.raw_data_string),
            "rows": spec.row_count()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m dlc.fingerprint",
        description=("Generate official-test fingerprints from .dig files "
                     "(the entries data/official_tests_defaults.json ships)."))
    ap.add_argument("files", nargs="+", help=".dig files to fingerprint")
    ap.add_argument("-o", "--out",
                    help="write the JSON here instead of stdout")
    ap.add_argument("--hashes-only", action="store_true",
                    help="print the manifest official_tests shape "
                         "({filename: sha1}) instead of full entries")
    args = ap.parse_args(argv)

    out: dict[str, object] = {}
    failed = 0
    for f in args.files:
        name = Path(f).name
        try:
            e = fingerprint_file(f)
        except ValueError as exc:
            print(f"  SKIPPED {name}: {exc}", file=sys.stderr)
            failed += 1
            continue
        out[name] = e["sha1"] if args.hashes_only else {
            "content": e["content"], "sha1": e["sha1"]}
        print(f"  {name:34s} fingerprint {e['sha1'][:12]}  "
              f"({e['rows']} rows)", file=sys.stderr)

    if not out:
        print("nothing fingerprinted.", file=sys.stderr)
        return 1
    text = json.dumps(out, indent=1)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {len(out)} entr{'y' if len(out) == 1 else 'ies'} to "
              f"{args.out} — merge into data/official_tests_defaults.json "
              f"(fork) or keep for your records.", file=sys.stderr)
    else:
        print(text)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
