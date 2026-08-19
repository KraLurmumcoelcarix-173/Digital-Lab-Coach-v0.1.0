# Instructor guide: configuring the answer ROM data for a lab

> Step 2 of the instructor flow (see the README's *Instructor setup*).
> Start with `MANIFEST_GUIDE.md` if you have not configured the
> official test set yet; deploy the course proxy last
> (`../proxy/README.md`).

This page teaches an instructor how to register the **hidden runtime ROM
payload** for a specific lab — the program words the tool loads into a
student's *empty* ROM during test runs and Mode A analysis, the same way
the autograder does. It also covers the official-testcase entry the
payload rides on, because both live in the same config record.

---

## 1. Decide whether this lab should have a ROM payload at all

Ask one question: **is the ROM's content a runtime INPUT to the lab, or
is it the lab's ANSWER?**

| Situation | Configure a payload? | Example |
|---|---|---|
| The ROM holds a *program the circuit executes* — students are graded on the datapath around it, not on the words | **Yes** | `cpu.dig` instruction memory |
| The ROM *is the deliverable* — filling it would hand out the answer and make a wrong/empty submission pass | **NEVER** | `control-unit.dig` decode table |

A lab with no payload configured still gets official-test injection;
its empty ROM simply stays empty (the student sees the Layer-1 warning,
and Mode A treats the missing words as the prime suspect — that is the
teaching path).

## 2. Where the configuration lives

One JSON file, shipped with the tool:

```
data/official_tests_defaults.json
```

One entry per lab **filename** (matching is by exact filename, e.g.
`cpu.dig`):

```json
{
  "romlab.dig": {
    "content": "A D\n0 5\n1 6",
    "sha1": "<normalized fingerprint of content — step 5>",
    "runtime": "<base64 blob — step 4>"
  }
}
```

- `content` — the official testcase rows (Digital test format: first
  line is the signal header, then value rows). Injected into a run-scoped
  copy whenever a student file's own testcase is missing or modified.
- `sha1` — fingerprint used to recognize an *unmodified* official
  testcase inside a student file (see step 5).
- `runtime` — the hidden payload. **Optional.** Only add it when step 1
  said yes.

The `runtime` key can only be configured here, in the shipped defaults
file. This is by design: the Settings page and the user-layer store
(`~/.dlc/official_tests.json`) never carry, list, or render it, so
answer-adjacent data never sits in a browser-reachable layer.

## 3. Get the ROM words from your answer circuit

Open your answer `.dig` in Digital, double-click the ROM, and read the
data table — or read the `Data` attribute straight out of the XML:

```xml
<entry>
  <string>Data</string>
  <data>5,6</data>
</entry>
```

Format rules (exactly what Digital itself stores):

- comma-separated words, **address 0 first**, one word per address;
- **bare hex** by default (`fe,82,1a` — no `0x` prefixes). The words are
  parsed with the student ROM's `intFormat` attribute, which is `hex`
  unless a student changed it — plain hex is the safe choice;
- Digital's run-length shorthand is supported: `7*1f` stores `1f` at 7
  consecutive addresses;
- trailing addresses you omit read as 0 (Digital semantics).

## 4. Build the base64 `runtime` blob

The blob is base64 over a tiny JSON object with a `rom` key:

```bash
.venv/bin/python -c "import base64, json; print(base64.b64encode(json.dumps({'rom': '5,6'}).encode()).decode())"
```

Replace `'5,6'` with your comma-separated words. Paste the printed
string as the entry's `"runtime"` value.

Why base64? It keeps the program words grep-proof at rest (no plaintext
answer strings in the repo) — it is **obfuscation, not encryption**.
Keep the repository private and keep answer `.dig` files out of it.

## 5. Compute the `sha1` for `content`

The fingerprint is a *normalized* hash (comments stripped, whitespace
collapsed) so cosmetic edits in a student's copy don't break matching.
Always compute it with the tool's own function:

```bash
.venv/bin/python -c "
from dlc.l3.manifest import normalized_test_hash
print(normalized_test_hash(open('official_rows.txt').read()))"
```

where `official_rows.txt` holds exactly the `content` text.

## 6. Restart and verify

1. Restart the server (the defaults file is read per request, but a
   restart guarantees no stale process).
2. Upload a student-style file with the right filename and an **empty**
   ROM, and run its tests. You should see the note
   *"the course program was loaded into 1 empty ROM for this run …"* and
   rows judged with the program in place.
3. Run Mode A on a failing file: every prompt carries the internal
   [ROM NOTE] (the model must not touch grader-loaded words, and the
   words themselves never ride the model payload), and any verified fix
   card ends with **"Check your ROM data"** — reminding the student
   their own file's ROM is still unprogrammed.

## 7. What the payload does and does not do (behavior contract)

- Fills **only empty ROMs** — a ROM the student programmed, even
  wrongly, is never overwritten (their words are their work, and Mode A
  can convict a wrong word).
- Applies to a **run-scoped sibling copy** only: the student's file is
  never modified, and the registered coach temps never store the words.
- Every empty ROM in the file receives the **same** words — a lab whose
  answer needs two *different* ROM programs is not supported by the
  single `rom` key yet.
- The words never appear in any UI, the Settings page, `list_tests()`,
  or the Mode A model payload.

## 8. Quick reference (maintenance)

| Piece | Where |
|---|---|
| Defaults file | `data/official_tests_defaults.json` |
| Payload reader | `dlc/l3/official_store.py::get_runtime_payload(filename, "rom")` |
| Injection (tests + Mode A) | `dlc/testing/inject.py::prepare_injected_run` → `_fill_empty_roms` |
| Fingerprint | `dlc/l3/manifest.py::normalized_test_hash` |
| Mode A behaviors | `dlc/l3/debugger.py` (`[ROM NOTE]`), `dlc/web/l3_routes.py` (`_apply_rom_hint`) |
