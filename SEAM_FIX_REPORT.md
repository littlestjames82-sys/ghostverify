# GhostVerify seam fix report (Seams #5)

Date: Oct 14, 2026. Scope: the two GhostVerify defects found by the
ecosystem pass (record: ~/workspace/ghostslice-companions/ECOSYSTEM.md,
Seams #5), found by running the Fixture -> Slice -> Verify loop on a
real bundle. No version bump; stdlib only; nothing pushed, published,
or announced.

## Test counts

- Before: 34 passed, 0 failed (`python3 -m pytest tests -q`).
- After: **38 passed, 0 failed**. The 4 new tests are additive in
  `tests/test_seam_fixes.py`; no existing test was edited, weakened,
  or deleted.

## Defect 1: phantom gap symbols

### Reproduction (before)

On the emergent run's own artifacts (GhostLogs project, the bundle
GhostSlice exported for it, the run's task text), pre reported
verdict `gaps_found` with exactly 4 MISSING-HARD gaps, all phantoms:

- `find_budget.budget` in `tests/conftest.py` (via task)
- `test_budget_600_lands_at_or_under_budget.budget` (via task)
- `test_default_pack_respects_its_stated_budget.budget` (via task)
- `test_over_budget_path_is_honest.budget` (via task)

A minimal self-built repro (a `find_budget` function with locals
`budget`/`estimate`, its file carried in the bundle as mode
`strip-tests` with no bytes on disk, task mentioning "budget")
reproduced the same shape: gaps `find_budget.budget` and
`check_budget.budget`, exit 1.

### Root cause

`project.py`'s Python analyser registered assignments inside
function bodies as project `constant` symbols: the local
`budget = None` inside `find_budget` became the "symbol"
`find_budget.budget`, and `budget, estimate = ...` tuple unpacks in
test functions became `test_....budget`. The task-term check then
matched the local's name (`budget`) against a task word and reported
a hard gap for a name that exists nowhere as a definition. A slice
can include or exclude a file or a definition, never a local
variable, so the gap was unclosable by construction: `fix` correctly
reported "no upgradeable hard gaps left" and closed `gaps_remain`
against a requirement that was an artifact of keyword-to-symbol
synthesis. (The same phantom appeared on the standard mini fixture:
`compute_total.subtotal`, a local in `pricing.py`, showed up as a
hard gap when pricing.py was excluded.)

### Fix (src/ghostverify/project.py, src/ghostverify/core.py)

- The analyser no longer registers function-body assignments as
  symbols. Module-level and class-level constants are still symbols
  (they are real, sliceable definitions). Function-body assignment
  names are recorded separately in `Project.local_names`
  (name -> [{path, enclosing}]), never in the symbol universe, so
  they can no longer become gaps via task matching, reachability
  edges, or the post-check.
- Plain-words ungrounded requirements: when a task word matches
  only such a local name (and no real symbol of that name exists),
  the report now carries an `ungrounded_requirements` entry, e.g.
  "the task mentions 'budget', but in this project 'budget' is only
  a local variable (for example inside find_budget in
  tests/conftest.py), not a definition a slice can include or
  exclude; this requirement cannot be grounded in a real symbol and
  is not counted as a gap". Rendered in VERIFY.md under
  "Ungrounded requirements (not gaps)" and in the CLI summary.
  Ungrounded requirements never affect the verdict or exit code.
- Grounding safety net in `run_pre`: every hard and soft gap is
  validated before the report is assembled. A symbol gap is
  grounded only if the symbol exists in the project scan or in the
  bundle manifest's `symbols_included`; a file-level gap only if
  the file exists in the project. Anything ungrounded is moved to
  `ungrounded_requirements` in plain words, so the invariant "no
  reported gap symbol is absent from the bundle/tree" holds
  structurally, not just for this one synthesis path.

### After (same emergent artifacts, read-only rerun)

- pre: verdict **sufficient**, exit 0, 0 hard gaps, 35 soft gaps
  (unchanged heuristic set), and 2 ungrounded requirements stated in
  plain words: `budget` (local inside find_budget) and `tier`
  (a local inside `build_pack` in GhostLogs' own pack.py). The
  bundle was in fact sufficient; the phantom gaps were the only
  thing failing it.
- Self-healing on real gaps did not regress: a task naming the real
  symbol `apply_budget` in the stripped file still fails pre by
  name (exit 1), and the full existing suite (planted-gap, fix
  restore, reproducibility, post-check) passes unchanged.

## Defect 2: fix cites an `expanded_bundle` path never written

### Reproduction (before)

On the emergent artifacts, `fix` recorded one round
("no upgradeable hard gaps left"), exported nothing, and still set
`"expanded_bundle": ".../repro/bundle3"`. That path did not exist on
disk. Same on the minimal repro: `expanded_bundle` cited the `-o`
path while the output directory was never created.

### Root cause

`run_fix` set `expanded_bundle` from `rounds` being non-empty, but a
round record is also appended for the no-progress break (no gap
file's mode has an upgrade), where no export ever happens.

### Fix (src/ghostverify/core.py, src/ghostverify/reports.py)

Smallest honest change: `run_fix` tracks the path only when
`exporter.build_bundle` actually ran (`expanded_path`), and cites it
only if it exists on disk at report time; otherwise
`expanded_bundle` is null and a note says so in plain words ("fix
wrote no expanded bundle: no hard gap could be upgraded by
re-export, so expanded_bundle is null rather than a path that does
not exist"). VERIFY.md's expansion section now prints the expanded
bundle path when one was written, or "none written" when not,
matching the JSON. Nothing about the export behaviour itself
changed: a successful fix still writes the bundle at the `-o` path
and the report cites exactly that path.

## Regression tests (tests/test_seam_fixes.py, 4 tests, additive)

- `test_task_word_matching_only_a_local_is_not_a_gap`: the emergent
  scenario rebuilt as a fixture; pre passes, the phantom symbols
  appear nowhere, every reported gap symbol (hard and soft) is
  grounded against an ast-computed set of real symbols plus the
  manifest, and 'budget' shows up as a plain-words ungrounded
  requirement in JSON and VERIFY.md.
- `test_grounded_gap_is_still_reported_by_name`: a task naming the
  real `apply_budget` in the stripped file still fails pre with
  that exact gap, all gap symbols grounded.
- `test_fix_cites_no_bundle_it_did_not_write`: fix on the
  unupgradeable real gap records its round, verdict `gaps_remain`,
  `expanded_bundle` is null, the `-o` directory was not created.
- `test_fix_expanded_bundle_exists_when_written`: the planted-gap
  mini fixture; fix exits 0, `expanded_bundle` equals the `-o` path,
  exists on disk with a manifest, and the expanded bundle passes
  pre (self-healing intact).

Pin check: run against the pre-fix code (the `build/lib` copy), the
3 defect-pinning tests fail and the success-path test passes, as
expected.

## Files changed

- `src/ghostverify/project.py` (locals out of the symbol universe,
  `Project.local_names`)
- `src/ghostverify/core.py` (ungrounded requirements, grounding net,
  `expanded_bundle` honesty, stable `ungrounded_requirements` key in
  early-return reports)
- `src/ghostverify/reports.py` (VERIFY.md: ungrounded section,
  expanded-bundle line)
- `src/ghostverify/cli.py` (summary prints ungrounded requirements)
- `tests/test_seam_fixes.py` (new, additive)
- This report.

Deliberately untouched: version (still 0.1.0), all existing tests,
the `_UPGRADE` mode table (whether `strip-tests` gaps should be
upgradable to `full` is a separate contract question; with phantom
symbols gone it no longer decides any verdict in these scenarios),
and the post-check's unseen-symbol findings, which already state
their plain-words basis ("not defined in the bundle or the
project").
