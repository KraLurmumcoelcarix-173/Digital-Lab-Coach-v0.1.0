# Configuring DLC for your own lab (instructor guide)

> Step 1 of the instructor flow (see the README's *Instructor setup*).
> Related: ROM payloads — `instructor_rom_config.md`; course proxy
> deployment — `../proxy/README.md`.

DLC works on any Digital (`.dig`) circuit out of the box: structural
coverage (mux arms, boundaries, constant outputs and any structural circuit
health detections) needs **zero configuration**. What this guide adds is the
*intent* layer — the small amount of course knowledge that turns "arm 2 is
never selected" into "the `sub` instruction is never tested", protects your
official tests, and unlocks the RISC-V program coach.

Two local artifacts:

| Artifact | What it is | Where it lives |
|---|---|---|
| **Manifest** (a json) | Names your lab's test categories + how to decode program words | `data/manifests/<your-lab>.json` in your fork |
| **Official tests** | The instructor-issued testcase per file | Settings ⚙ → Official tests (stored in `~/.dlc/official_tests.json`), or shipped defaults in `data/official_tests_defaults.json` |

A manifest holds only **input patterns and names** (which opcode values
exist, which digit each ABCD pattern means). It has no expected outputs,
no wiring, no solution content.

---

## Quickstart: a new lab in five steps

(Note: DLC's "server" is just the tool itself, running locally on each
user's own machine — there is no shared server. And a manifest is only
needed when the lab requires semantic interpretation — opcode / RISC-V
standard decode, display-digit classes etc.; the shipped defaults already
cover the 311 course labs.)

1. **Fork the repo** (or work in your local clone).
2. **Register the official tests**: Settings ⚙ → Official tests → filename
   + paste the testcase rows (the header line + data rows exactly as in
   the `.dig`'s test editor; content must be valid Digital test format —
   the tool rejects anything else). Comments and spacing are ignored by
   the match, changed rows are not.
3. **(Forks) Ship the official tests as built-in defaults** — generate
   ready-made entries straight from your `.dig` files with the
   fingerprint helper:

       uv run python -m dlc.fingerprint cpu.dig register-file.dig -o defaults.json

   It reads each file's first testcase and emits
   `{"<file>.dig": {"content": "...", "sha1": "..."}}` — merge those
   entries into `data/official_tests_defaults.json` in your fork, done.
   The sha1 is the same normalized fingerprint the Settings list shows;
   `--hashes-only` prints the `{filename: sha1}` shape used by a
   manifest's `official_tests` block instead.
4. **Write the manifest** — copy `data/manifests/tier3_latched_display.json` as a
   template and edit (details below). Drop it in `data/manifests/`,
   **named after the lab's top-level `.dig`**.
5. **Validate**: upload the lab, open L3 Coach, run the Coverage Coach.
   You should see `lab manifest '<name>' applied` in the whole-tree notes
   and a `categories N/M` chip on the file. If not, see Troubleshooting.

---

## The manifest, block by block

```json
{
  "lab": "my-lab",
  "applies_to": ["my-top.dig", "my-sub.dig"],
  "categories": { ... },
  "official_tests": {},
  "reference_dir": null
}
```

- `lab` — any short name; shown in the scan notes.
- `applies_to` — the EXACT filenames of your lab (matching is by
  filename; if students rename files, the manifest will not attach).
- `official_tests` — optional sha1 fingerprints (the Settings store
  usually replaces this; leave `{}`).
- `reference_dir` — leave `null`. If you keep solution circuits on YOUR
  machine, point the `DLC_REFERENCE_DIR` environment variable at that
  folder when starting the server: proposed rows are then also checked
  against the solutions before students see them. Never ship solutions.

### `categories` — name the cases that matter

One list per file. Each category = a name + the input cells that
identify it, using the **testcase's own column names**:

```json
"categories": {
  "my-display.dig": [
    {"name": "digit_5", "when": {"A": 0, "B": 1, "C": 0, "D": 1, "load": 1}},
    {"name": "hold",    "when": {"load": 0}}
  ]
}
```

A circuit is category-GREEN when every named category is matched by at
least one test row. The Coverage Coach proposes rows for the missing
ones, and its category claims are checked deterministically — the model
cannot mislabel a row.

Values may be written as decimal, `0x...`, or `0b...`. Every column
named in a `when` must exist in that file's testcase header, or the
manifest stays silent for that file (by design — it never guesses).

### `program_decode` — RISC-V CPUs (copy-paste block)

For a CPU whose instructions come from a program ROM (a ROM component
with *Program Memory* checked), add this block. For any RV32I lab you
can copy it **verbatim** — the bit fields are the RISC-V standard:

```json
"program_decode": {
  "categories_from": "control-unit.dig",
  "fields": {
    "opcode": [0, 7], "funct3": [12, 3], "funct7": [25, 7],
    "rd": [7, 5], "rs1": [15, 5], "rs2": [20, 5]
  },
  "observe": {"rs1_port": "ReadData1", "rs2_port": "ReadData2"}
}
```

Adjust only two things:

- `categories_from`: the file whose `categories` list enumerates the
  instructions your lab implements (typically your control/decode unit —
  categories written over `opcode`/`funct3`/`funct7` columns, see
  `data/manifests/cpu.json` as example).
- `observe`: the CPU testcase's column names that show the register-file
  read ports. This is what lets the coach add machine-derived read-back
  rows (`addi x0, xN, 0`) so every value an extension writes is actually
  observed.

With this block the tool decodes every program word deterministically,
rejects lazy or undefined instructions, derives register values by
constant propagation, and — if the model's proposal fails its gates —
machine-builds a correct extension on its own (this part even works
offline).

---

## What anyone can and cannot do in Settings

- **Built-in defaults are view-only for everyone** — the only way to
  raise a default's standard is *Adopt into official tests* after a
  Coverage Coach run ends **all set** (server-side, from the verified
  temp circuit — no free-form editing). An Adopt override can always be
  deleted to revert to the shipped default.
- **Anyone may add official tests for their own labs as test standards** (filename +
  testcase content); content is validated as Digital test format and
  rejected otherwise. These entries stay fully editable and deletable.
- Manifests are repo/fork files — configuring a NEW lab's semantics is
  the instructor's (fork owner's) job, per the quickstart above.

## Troubleshooting

- **No `manifest applied` note** → a filename in the uploaded tree must
  appear in `applies_to`; check exact spelling and case.
- **No `categories` chip** → a `when` column name doesn't match the
  file's testcase header exactly; the manifest stays silent rather than
  guess.
- **`official test` chip missing** → the file has no entry in Settings →
  Official tests (or the content was modified — the chip then says
  `modified`, which is the point).
- **Program coach inactive** → the ROM component must have *Program
  Memory* checked in Digital; `program_decode` must be present; the
  testcase needs a clock column.
