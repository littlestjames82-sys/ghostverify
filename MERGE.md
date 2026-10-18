# MERGE.md - ghostverify merge of harden / polish / deepen lanes

Date: 2026-10-18 (redo run, verified on disk)
Base: pristine tree `~/workspace/ghostverify/` (never edited)
Order applied: harden, then polish, then deepen.
Method: `diff -rq` each lane against pristine, then file-by-file copy for
single-lane files and three-way `git merge-file` (current=harden-applied,
base=pristine, other=incoming lane) for multi-lane files. Hand-resolved
the one conflict (see below). No IMPROVE.md was copied into merged.

## Test counts (real runs in this redo, command as documented in the lane notes)

Command: `PYTHONPATH=src python3 -m pytest tests -q`

- merged: **74 passed in 56.97s**
  Verbatim summary line: `74 passed in 56.97s`
  (38 pre-existing + 28 new harden tests + 8 new deepen tests = 74)
- pristine-check (fresh pristine copy at the same depth,
  `~/workspace/.improve-2026-10-18/ghostverify/pristine-check/`):
  **5 failed, 33 passed in 25.82s**
  Verbatim summary line: `5 failed, 33 passed in 25.82s`

Acceptance: merged failures (0) are a SUBSET of pristine-check failures (5).
Zero failures appear only in merged. The 5 pristine-check failures are the
pre-existing GhostSlice-discovery `fix` tests, all failing with
"ghostslice exporter unavailable" at the deeper copy depth:

- tests/test_cli_fix.py::TestFix::test_fix_on_a_clean_bundle_stays_passing
- tests/test_cli_fix.py::TestFix::test_fix_reports_token_delta_and_rounds
- tests/test_cli_fix.py::TestFix::test_fix_restores_a_passing_bundle
- tests/test_seam_fixes.py::TestFixReportPaths::test_fix_cites_no_bundle_it_did_not_write
- tests/test_seam_fixes.py::TestFixReportPaths::test_fix_expanded_bundle_exists_when_written

Merged turns those 5 green via the harden lane's gsload.py ancestor-walk
discovery fix, as the harden IMPROVE.md predicted.

## Files by lane provenance (13 files changed vs pristine: 11 modified, 2 new)

Harden only:
- src/ghostverify/gsload.py
- tests/test_harden.py (NEW, 28 tests)

Polish only:
- examples/README.md
- src/ghostverify/__main__.py

Deepen only:
- tests/test_fixture_reachability.py (NEW, 8 tests)

Harden + polish (three-way merged clean, no conflict):
- src/ghostverify/bundle.py (harden manifest validation + polish docstrings)
- src/ghostverify/cli.py (harden unwritable-report handling + polish help text)
- src/ghostverify/mcp_server.py (harden JSON-RPC -32700/-32600 errors + polish docstrings)
- src/ghostverify/patch.py (harden patch containment / hunk parsing + polish docstring)

Polish + deepen (three-way merged clean, no conflict):
- README.md (polish decision table / config / troubleshooting + deepen fixture docs)
- src/ghostverify/project.py (polish docstrings + deepen fixture edges / parameter binding)
- src/ghostverify/reports.py (polish docstrings + deepen fixture_references rendering)

All three lanes:
- src/ghostverify/core.py (harden honesty/config/fix fixes + polish docstrings
  + deepen fixture classification and fixture_references count) - see conflict below

## Conflicts and resolutions

1. src/ghostverify/core.py, `run_post` head - ONLY conflict.
   - Harden inserted a non-positive-timeout guard immediately after the
     `def run_post(...)` line (fall back to timeout=120 instead of
     manufacturing an instant "timeout" verdict).
   - Polish inserted a docstring for `run_post` in exactly the same spot.
   - Resolution: BOTH survive, in natural order: polish docstring first,
     then the harden timeout guard directly after it, before the body.
     No duplicate logic; function compiles (`py_compile` ok for all modules)
     and no conflict markers remain anywhere in merged (grep verified).
   - No harden fix was dropped anywhere in the merge.

All other multi-lane files merged cleanly in three-way merge; spot greps
confirmed both lanes' signatures present in each combined file (bundle
BundleError validation + docstrings, cli error print + help text, mcp
-32700/-32600 + _capped/run_stdio docstrings, patch escape refusal +
parse_patch docstring, project is_fixture/fixture edges + docstrings,
reports fixture_references line + docstrings, README decision table +
fixture section).

## Anything suspicious

- Deepen's lane copy lacks the pristine `build/` directory and
  `src/ghostverify.egg-info/`. Neither is mentioned in deepen's IMPROVE.md
  as a deletion, so this was treated as a lane-copy artifact, not a change:
  merged retains the pristine copies of both.
- The stale `build/lib/ghostverify/*.py` copies under `build/` are pristine
  copies (not lane-updated); they are not on the test path (`PYTHONPATH=src`),
  consistent with the harden lane note. Not updated, not a regression.
- Test runs generated `__pycache__`/`.pytest_cache` inside merged and
  pristine-check; those caches were removed after the runs. Counts above
  are from the runs before that cleanup.
- No version bump, no new dependencies, LICENSE untouched (all lanes agreed).
