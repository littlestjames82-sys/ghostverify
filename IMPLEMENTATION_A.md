# GhostVerify v0.1 - Implementation Notes (Agent A, core)

Built Oct 14, 2026 from `PLAN.md`. Python stdlib only, no network,
no PyPI, no GitHub push, no announcements, no spend.

## What was built (Agent A ownership: src/**, pyproject.toml, this file)

```
pyproject.toml                  ghostverify 0.1.0, zero dependencies, console script
src/ghostverify/__init__.py     version 0.1.0
src/ghostverify/__main__.py     python -m ghostverify
src/ghostverify/gsload.py       optional GhostSlice integration + token estimator
src/ghostverify/bundle.py       bundle reader + kept-set
src/ghostverify/project.py      project scan, symbol universe, import + call graph
src/ghostverify/core.py         pre / fix / post
src/ghostverify/patch.py        unified-diff parser + temp-copy applier
src/ghostverify/reports.py      verify-report.json + VERIFY.md + ledger
src/ghostverify/mcp_server.py   stdio MCP server (verify_bundle, expand_bundle, check_patch)
src/ghostverify/cli.py          pre / fix / post / mcp commands
```

## Design decisions

- **GhostSlice reuse without a hard dependency.** `gsload.py` uses
  GhostSlice's estimator, exporter, profile loader, lint and secrets
  engines when GhostSlice is importable (installed, on PYTHONPATH,
  via `GHOSTSLICE_SRC`, or as a sibling `ghostslice/src` checkout).
  Lint/secret rules are never re-implemented (per PLAN); when
  GhostSlice is absent the carry-over is reported as unavailable and
  the built-in fallbacks (same documented BPE-like token split,
  factor 1.0) cover tokens/graph only. `fix` refuses with exit 2
  rather than faking an exporter.
- **Bundle reader (`bundle.py`).** Parses `manifest.json`, hashes the
  raw manifest bytes (sha256 recorded in every report), and builds
  the kept-set: `full` / `strip-tests` / `symbols` are body-kept,
  `signatures` / `interfaces` / `structure` / `headings` are stubs,
  `exclude` or missing `files/` bytes are not kept, whatever the
  manifest claims. Malformed manifests raise `BundleError`, which
  the CLI maps to exit 2 - never a pass.
- **Project graph (`project.py`).** Python via real `ast`: symbols
  (functions, methods, classes, constants), import resolution
  (including the unique-suffix fallback for src layouts), call and
  reference edges. Two passes: all definitions register before any
  edge is built, so cross-file calls are not lost to file order
  (this bug was caught by the smoke test below and fixed). Attribute
  calls fan out conservatively to every project method of that
  name. Non-Python languages are parser-lite (regex outline +
  TS/JS import graph) and every report carries per-language
  confidence labels that are never averaged away.
- **pre (`core.run_pre`).** Reachability BFS seeded from every kept
  symbol and kept file's module node. Callee absent/excluded via an
  exact/heuristic/attribute/reference edge = MISSING-HARD (exit 1);
  callee kept only as signatures and directly called = MISSING-SOFT
  (heuristic, labelled, does not fail); stub reached only by
  reference = OK-STUB (counted). File-level import of an
  absent/excluded file is also MISSING-HARD. Task terms (words,
  snake/camel parts, file names) that name a project symbol/file
  absent from the bundle are MISSING-HARD. Bundle-file lint and
  secret findings are carried over from GhostSlice, filtered to
  kept files. No analysable symbols or an unreadable bundle/project
  = could_not_verify (exit 2).
- **fix (`core.run_fix`).** Max 3 rounds. Each round takes the hard
  gaps' files, upgrades them one rung (exclude/missing ->
  signatures, signatures/interfaces -> full; profile entry points
  are never downgraded), and re-exports through GhostSlice
  `build_bundle` with `include` limited to the original bundle's
  files plus the gap files - sliced files are never hand-edited.
  Per-round changed files, sliced-token totals and remaining hard
  gaps are recorded in the report.
- **post (`core.run_post`).** Parses a unified diff, copies the
  project to a temp directory (the working tree is never written -
  verified in smoke), applies the patch there with a small exact-
  context hunk applier, and statically flags: files touched that
  are not in the bundle, added calls to project symbols defined in
  missing/excluded files (symbols created by the patch itself are
  excluded), and test-file edits (flagged, not auto-rejected).
  Tests run only with `--run-tests` plus a command from
  `--test-command` or `.ghostverify.json` `test_command`, in the
  temp copy, with a timeout and truncated captured output.
  Diagnosis ties a failure to the slice only when the output names
  a stub symbol; otherwise it says "cause not tied to the slice".
- **Reports + ledger (`reports.py`).** `verify-report.json` and
  `VERIFY.md` are written next to the bundle by default (not inside
  it, not inside the project tree) and include the honesty line
  verbatim. One line per run is appended to
  `<project>/.ghostverify-history.jsonl`; the GhostSlice savings
  ledger is never touched. Tool ledgers are excluded from project
  scans so reports cannot feed back into token totals.
- **MCP (`mcp_server.py`).** Newline-delimited JSON-RPC 2.0 over
  stdio, same pattern as GhostSlice's server. Gap lists capped at
  50 with truncation flags; no raw file bodies in any payload.

## Smoke test (run by Agent A, /tmp/gv-demo, PYTHONPATH=src + ghostslice src)

Demo project: `a.py` (`main()` calls `b.helper()`), `b.py`
(`helper()`), bundles exported with the real GhostSlice exporter.

| Check | Result |
|---|---|
| `pre` on clean full bundle, task "run main" | exit 0, verdict sufficient, 0 hard, 0 soft |
| `pre` on planted-gap bundle (`b.py` mode exclude) | exit 1, verdict gaps_found; MISSING-HARD `helper` in `b.py` via `a.py:main` (exact) plus the file-level import gap |
| `fix` on the gap bundle -> `bundle-v2` | exit 0, verdict sufficient_after_expand; round 1: `b.py (exclude -> signatures)`, sliced tokens back to 37, hard gaps 0; re-running `pre` on v2 passes (one MISSING-SOFT for `helper`, signatures only) |
| `post` good patch (`helper() + 0`) | exit 0, verdict ok, applied to temp copy only |
| `post` good patch + `--run-tests` (`python3 -c "import a; assert a.main()==42"`) | exit 0, tests passed |
| `post` breaking patch + `--run-tests` | exit 1, verdict failed, tests failed |
| `post` patch calling `secret_fn()` (defined in `c.py`, not in bundle) + adding a test file | exit 1, verdict flagged: `references_unseen_symbol`, `touches_file_not_in_bundle`, `edits_test_file` |
| Working tree after all post runs | `a.py` byte-identical to the original - patches only ever touched temp copies |
| Malformed bundle (manifest not JSON) | exit 2, verdict could_not_verify |
| MCP stdio | initialize returns serverInfo ghostverify 0.1.0; tools/list returns the 3 tools; tools/call verify_bundle returns the gap report |
| Reports / ledger | verify-report.json + VERIFY.md written next to the bundle; `.ghostverify-history.jsonl` lines carry task, bundle manifest sha256, verdict, gap counts, token before/after, patch result |
| `python -m py_compile` on all modules | OK |

Agent B's test suite (tests/**, their ownership) was run against
this core after the smoke test: **33 passed, 1 skipped** (17.5s,
`PYTHONPATH=src pytest tests -q`). Two findings from that run were
fixed in core and re-verified: (1) `post` now also flags added
calls to names defined nowhere in the bundle or project (the
PLAN's "not in the bundle and not created by the patch" case, e.g.
a hallucinated `process_refund()`), excluding builtins, keywords,
patch-created names, patch imports, attribute calls and symbols
defined in the patched file itself; (2) an earlier full-suite run
that appeared to hang was parallel-build CPU contention (26
builders at once), not a deadlock - the same suite completes in
under 20s unloaded. Packaging was also verified: a fresh venv
`pip install .` from pyproject.toml succeeds, the installed
`ghostverify --version` reports 0.1.0, and the installed CLI
reproduces the planted-gap verdict (exit 1, both MISSING-HARD
findings) with only `GHOSTSLICE_SRC` pointing at the sibling
GhostSlice checkout.

## Known limits (v0.1, honest)

- Dynamic dispatch, `getattr`, string registries and framework
  callbacks are not traced; a symbol reachable only that way can be
  reported missing. Verdicts are evidence, not guarantees - the
  report says so.
- Cross-file Python call resolution is exact for imports and local
  definitions, heuristic for unique bare names, and a conservative
  fan-out for attribute calls (every same-named method). Receiving-
  object types are not inferred.
- Non-Python reachability is import-graph + outline level only;
  MISSING-SOFT there is noisier, hence the per-language confidence
  labels.
- `fix` upgrades whole files (signatures, then full); it does not
  do symbol-level expansion, and it needs GhostSlice importable.
- The patch applier handles standard unified hunks with exact
  context; renames, binary patches and fuzzy context are reported
  as failed hunks rather than guessed.
- Test execution is opt-in and runs a user-supplied command with
  the user's own privileges in a temp copy; it is not sandboxed.
  Per PLAN, a future in-place runner belongs behind Agent Seatbelt.
- No dashboard panel (deferred to v0.2 per PLAN), no agent/model
  calls anywhere.
