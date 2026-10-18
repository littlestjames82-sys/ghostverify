# GhostVerify v0.1 - Agent B Report (tests / docs / examples)

Status: COMPLETE. Final result against Agent A's finished CLI:
**34 passed, 0 failed, 0 skipped** under pytest (run twice), and
33 tests OK under plain `python -m unittest discover` (pytest
collects one extra test; both runners green).

## What Agent B built

File ownership respected: only `tests/**`, `README.md`,
`examples/**`, and this `REPORT_B.md` were created or edited.
No edits to `src/**`, `pyproject.toml`, or `PLAN.md`.

- `tests/helpers.py` - black-box CLI discovery + runner, a
  GhostSlice-shaped bundle builder (per-file path/mode/language/
  package/original_tokens/sliced_tokens/sha256 + totals, matching
  the GhostSlice v1.0.x manifest fields), fixture project copier,
  lenient report finder (verify-report.json / VERIFY.md inside the
  bundle dir or its parent), tree hashing for working-tree safety
  checks, and small AST helpers.
- `tests/fixtures/mini_project/` - a runnable Python fixture
  project: `app.py` calls `pricing.compute_total` and
  `formatting.format_total` (checkout([10.0]) == "$10.80", proven
  by a fixture test), plus `test_app.py` and a `deploy.py` that
  patch tests deliberately leave out of the bundle.
- `tests/fixtures/patches/` - four patches: good (formatting.py
  only), unseen-symbol (adds a call to `process_refund`, defined
  nowhere), test-edit (rewrites the test assertion), and
  out-of-bundle (touches unbundled `deploy.py`).
- `tests/test_fixtures.py` - fixture-validity tests that need no
  CLI: clean bundle covers every called symbol, planted gaps are
  real in both shapes (mode `exclude` and manifest-absent),
  manifests are sha256/total consistent, patch fixtures touch
  exactly what the CLI tests expect.
- `tests/test_cli_pre.py` - plan exit codes (0 sufficient, 1 hard
  gaps, 2 could-not-verify for missing bundle and corrupt
  manifest), gap named by file + symbol and classified hard,
  task naming a missing symbol fails, signatures-only dependency
  is never a hard gap, reports + ledger written, no guarantee
  wording, reproducibility via manifest reference.
- `tests/test_cli_fix.py` - fix restores a passing bundle, does
  not mutate the source bundle payload, reports round/token
  delta, expanded total is greater than the gapped total, fix on
  a clean bundle stays passing.
- `tests/test_cli_post.py` - good patch passes; unseen-symbol,
  test-edit, and out-of-bundle patches are flagged (flag text in
  output or report, never a silent pass); static-only is stated
  when no test command is configured; malformed patch exits 2;
  plus safety invariants: post/pre/fix never modify the working
  tree (only `.ghostverify-history.jsonl` may appear), and a
  planted fake secret literal never appears in any output.
- `tests/test_mcp.py` - conservative MCP stdio handshake test:
  skips unless `mcp` answers initialize + tools/list, then
  asserts verify_bundle / expand_bundle / check_patch.
- `README.md` - install (pip and PYTHONPATH routes), quickstart,
  command table, gap classes, exit codes, report and ledger
  formats, profile notes, limits stated plainly (parser-lite
  confidence, static evidence is not run-time truth, opt-in
  contained test execution, no invented diagnoses), scope, tests,
  layout.
- `examples/` - three-file demo project, good + unseen-symbol
  patches, and a walkthrough that plants a gap, heals it with
  fix, and checks both patches with post.

## Assumptions (core API not final when these tests were written)

1. CLI surface is exactly the plan's: `ghostverify pre <project>
   <bundle> --task "..."`, `ghostverify fix <project> <bundle>
   -o <out> [--task "..."]`, `ghostverify post <project> <bundle>
   --patch <file.diff>`, `ghostverify mcp`.
2. Discovery: `GHOSTVERIFY_CMD`, a `ghostverify` on PATH, or
   `python -m ghostverify` with PYTHONPATH=src (then repo root).
   If none responds to `--help`, CLI tests skip rather than fail.
3. Reports land "next to the bundle" (plan wording): tests accept
   the bundle dir itself or its parent for verify-report.json
   and VERIFY.md. The ledger is accepted in the project (plan
   wording) or next to/in the bundle.
4. Gap JSON shape is not pinned by the plan, so tests read the
   report leniently (top-level or nested `gaps` / `findings` /
   `missing` / `issues`) and also scan report text for the file,
   symbol, and a hard classification tag.
5. Post flag exit semantics are not pinned by the plan ("flagged,
   not auto-rejected" for test edits), so post tests require the
   flag to be visible in output or report and forbid a silent 0;
   only the good patch must exit 0 and a malformed patch must
   exit 2.
6. Bundle fixtures are generated at test time by
   `tests/helpers.make_bundle` with a fixture token estimate
   (chars/4, labelled as a fixture estimate in the manifest), not
   GhostSlice's estimator.

## Test runs (honest timeline)

- Fixture tests, core absent: 10 passed.
- Full suite, core partially present (src package existed but
  `ghostverify.cli` not yet landed, CLI probe correctly failed):
  11 passed, 23 skipped (all CLI/MCP tests skipped with the
  reason "ghostverify CLI not discoverable").
- First run against the landed CLI: 16 failed. Triage split
  those into (a) Agent B's own test bugs, fixed in Agent B files
  only, and (b) one real product gap, below.
- Final: **pytest 34 passed** (twice, 16s and 8s) and
  **unittest discover: 33 tests, OK**.

### Agent B test bugs found and fixed during triage

1. The task-gap test named a symbol that exists nowhere in the
   project (`process_refund`). That is new code an agent would
   create, not a context gap, and pre correctly passed it. The
   test now names `deploy_target` in `deploy.py`, a project symbol
   a bundle can genuinely omit, and pre flags it MISSING-HARD.
2. Working-tree safety assertions were too naive: GhostVerify
   appends its ledger in the project (by design), fix writes
   GhostSlice's cache/ledger there (GhostSlice behaviour on
   re-export), and Python writes __pycache__. Safety tests now
   pin every pre-existing *source* file's hash and whitelist
   only those bookkeeping paths for new files.
3. The malformed-patch test demanded exit 2; the plan pins exit
   2 under `pre` only. The test now requires a loud rejection
   (1 or 2), never acceptance. Agent A's CLI exits 1 with a
   `patch_does_not_apply` finding.
4. The MCP test initially dropped the PYTHONPATH the CLI was
   discovered with; it now uses `helpers.cli_env()` and passes
   against the real server (all three plan tools listed).

### Real product gap found, then fixed by Agent A mid-run

- `ghostverify post` initially did NOT flag the unseen-symbol
  patch: a patch adding a call to `process_refund()` (not in the
  bundle, not in the project, not created by the patch) exited 0
  with "no findings", which contradicts plan MVP item 3.
  Agent B kept that test strict rather than softening it.
  Agent A's `core.py` was updated at 13:14 ET during this run;
  post now exits 1 with
  `references_unseen_symbol: app.py added line calls
  process_refund(), which is not defined in the bundle or the
  project and not created by the patch (unseen)`,
  and the full suite went green. Parent should re-verify this
  behaviour on the final tree, since it changed mid-run.

### Also verified by hand during triage (matches the tests)

- pre on the clean fixture bundle: exit 0, "no hard gaps found
  by static check"; reports written next to the bundle
  (verify-report.json + VERIFY.md), ledger in the project.
- post flags: test-edit patch exits 1 (`edits_test_file`,
  flagged not auto-rejected), out-of-bundle patch exits 1
  (`touches_file_not_in_bundle`), good patch exits 0 and states
  tests were not run (static-only).
