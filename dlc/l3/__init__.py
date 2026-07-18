"""Layer 3 — strategic debugging coach (deterministic substrate).

This package holds the DETERMINISTIC engines Layer 3 stands on
(see docs + the L3 blueprint):

  oracle.py     Temp-circuit injection + per-row rerun — the self-verify
                oracle. Mode B injects accepted coach rows; Mode A's retest
                and the verified-root-cause metric rerun proposed
                fixes. Never modifies the student's original file.

LLM-side modules (the /api/llm/debug coordinator, coverage agent,
detective classifier) build ON these but live behind their own frozen
I/O contract; nothing in this package calls a model.
"""