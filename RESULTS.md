# GhostVerify - Test Results

Reproducible record of a full-suite run. Regenerate on every
release-grade change; counts quoted anywhere else must match this
file or be removed.

## Run of Oct 18, 2026 (evidence contract fields)

- Date (UTC): 2026-10-19T02:53Z
- Platform: Linux 7.0.0-39-generic x86_64
- Python: 3.12.3
- pytest: 9.1.1
- Tree: ghostverify v0.1.0, evidence-contract build (every
  verify-report.json now states tool_version, verification_level
  + basis, and a UTC timestamp)
- Command:

```sh
cd ghostverify
python3 -m pytest tests -q
```

- Result: **81 passed** (suite was 74 passed before this change;
  +7 in tests/test_evidence_contract.py)

### What changed

- `src/ghostverify/core.py`: `_contract_fields()` is spread into
  every report `_base_report` builds (pre, fix, and post all build
  on it) and into the exit-2 report `run_pre` returns when the
  bundle is unreadable, so every verify-report.json GhostVerify
  writes carries:
  - `tool_version`: the package version (`ghostverify.__version__`,
    the same source `ghostverify --version` and pyproject report;
    the pre-existing `version` field is kept unchanged).
  - `verification_level`: `static-analysis`, with
    `verification_level_basis` stating why: "static-analysis:
    GhostVerify compares the bundle against the project statically;
    it executes no project code and runs no agent, so no stronger
    level is claimed".
  - `timestamp`: ISO 8601 UTC creation time. The report carried no
    top-level timestamp before; the only timestamp in the old
    output was the `at` field of each `.ghostverify-history.jsonl`
    ledger line. The new field is named `timestamp` per the shared
    contract, and no existing field was renamed.
- One honest exception, in `run_post`: `post --run-tests` executes
  the project's own configured test command against the patched
  temporary copy (`subprocess.run(..., cwd=<temp copy>)`). When
  those tests actually ran (status passed, failed, or timeout),
  the report's level is `tests-executed` and the basis string says
  the bundle and patch checks themselves remain static analysis.
  A post run whose tests did not run (static-only, no command
  configured, patch did not apply) stays `static-analysis`.
- `src/ghostverify/reports.py`: VERIFY.md now prints the tool
  version, verification level + basis, and timestamp next to the
  verdict.
- Backward compatibility: the new fields are additive. A
  pre-contract report still flows through Verify's own readers
  (markdown renderer, ledger appender) — pinned by tests — and
  GhostConsole treats absent fields as "not stated".

### Cross-tool proof (GhostConsole reads the new fields)

No GhostConsole change was needed: its GhostVerify adapter already
applies stated contract fields generically
(`_apply_evidence` in ghostconsole/src/ghostconsole/adapters.py).
Proof, run live with the new GhostVerify on the suite's fixture
project (clean bundle, verdict `sufficient`, exit 0), then
GhostConsole over the same project:

```sh
PYTHONPATH=ghostverify/src python3 -m ghostverify pre \
  /tmp/gv-proof2/project /tmp/gv-proof2/context \
  --task "Fix checkout totals in app.py using compute_total and format_total" \
  --out-dir /tmp/gv-proof2/project
PYTHONPATH=ghostconsole/src python3 -m ghostconsole summary /tmp/gv-proof2/project
PYTHONPATH=ghostconsole/src python3 -m ghostconsole build /tmp/gv-proof2/project --json
```

The verify-report.json itself:

```json
{"tool": "ghostverify", "tool_version": "0.1.0", "version": "0.1.0",
 "verification_level": "static-analysis",
 "timestamp": "2026-10-19T02:53:04Z", "verdict": "gaps_found"}
```

(that excerpt is from the identical run without `--out-dir`; the
proof run's verdict was `sufficient`.)

Console's GhostVerify row, verbatim from the text summary:

```
    [ok] GhostVerify: verdict: sufficient (0m ago) | level: static-analysis
```

and the same section in Console's JSON output:

```json
{"tool": "GhostVerify", "state": "ok",
 "verification_level": "static-analysis", "tool_version": "0.1.0"}
```

The project's placement reason also picked the level up:
"strongest verification level: static-analysis (static only: no
tests executed, no live run)". GhostConsole's suite is unchanged
and still passes: 103 passed.

## Addendum, Oct 18, 2026 (CI diagnosis)

The first CI run of this repo failed on every Python version while
the suite passed locally. Root cause, reproduced locally: six
fix-flow tests require the GhostSlice integration (gsload's sibling
discovery), which a standalone checkout does not have. The product
degrades by design in that case, so the tests now skip with a stated
reason when GhostSlice is unavailable (helpers.ghostslice_available),
and CI gained an `integration` job that checks out GhostSlice as a
sibling so those tests run for real. Local counts after the change:
standalone 75 passed + 6 skipped; integration live 81 passed.
