# GhostVerify - Self-Healing Slice Checker for GhostSlice
Working name, prepared Oct 14, 2026. Paired tool #1 for GhostSlice.
Decision: NOT Goliath. Goliath stays reserved for a non-agent product (Ryan, Oct 11-12, 2026).

## Why this name
- GhostVerify: plain job, plain name. It verifies a slice before spend and verifies a patch after the agent runs.
- Checked against the family: sits next to GhostSlice the way a checker sits next to a cutter. No claim of a live product search yet. Treat as a working name until a name check runs at build gate, same as GhostSlice did Oct 12.
- Alternatives held in reserve: GhostCheck (too close to GhostSlice's own `check` command, which only validates bundle hashes), GhostMend (implies it edits code on its own, which v0.1 must not do).

## The problem
GhostSlice v1.0.6 (948 tests, 25 commands) proves a bundle is well-formed with `ghostslice check`: manifest hashes and token totals match disk. It does not prove the bundle is *sufficient* for a task.

A slice can pass `check` and still fail the agent:
- A kept symbol calls a symbol that was sliced to signatures only, or excluded.
- An interface, type, constant, or fixture the task needs is not in the bundle.
- The agent returns a patch that references what it never saw, breaks tests, or touches a file that was never in context.

Today the developer pays for that failure in a full agent run, then guesses what to add and re-runs. There is no pre-flight sufficiency check and no post-run diagnosis tied back to the slice.

## Position in the Ghost stack
- GhostSlice governs what an agent READS (scanner, AST slicer, token estimator, call graph, `.claude_context` bundle with manifest + sha256).
- GhostVerify governs whether what it read was ENOUGH, and whether what it wrote back HOLDS.
- Agent Seatbelt governs what an agent DOES (consequential actions, approvals).
- Ghost Hands is the governed browser body.

Story: Slice -> Verify (pre) -> agent runs under Seatbelt -> Verify (post) -> accept or re-slice.
No overlap: Slice's `check` stays bundle integrity. Verify is task sufficiency + patch outcome.

## How it pairs with GhostSlice, concretely
Reuses, does not rebuild:
- Bundle format: reads any GhostSlice bundle (`manifest.json`, `PROMPT.md`, `files/`) and any preset (claude-code, aider, generic, cursor, copilot, anthropic-xml, json).
- Call graph: `src/ghostslice/callgraph.py` + `graph.py` import graph for reachability. Python call edges are complete in v1.0.6; other languages use import graph + outline symbols and are labelled parser-lite.
- Symbol index: `ghostslice symbols` output as the universe of what *could* have been included.
- Cache and profile: respects `.ghostslice.json`, the per-project content cache, and the savings ledger `.ghostslice-history.jsonl`. Verify writes its own verdict lines to a sibling ledger, it does not rewrite Slice history.
- Token estimator and `prices.json`: same estimator version stamp, same local dated price table. All counts labelled estimates.
- MCP: extends the pattern of Slice's stdio MCP server (9 tools at v1.0.5+) with its own tools, see below. No network in v0.1.

Integration contract:
- Input: project path + bundle path + a task string (or a task file), optional test command supplied by the user in the profile.
- Output: a verdict file `verify-report.json` + a human report `VERIFY.md` next to the bundle, never inside the project source tree by default.
- If Verify expands a slice, it does so by calling GhostSlice export again with added symbols/files, producing bundle v2 with a new manifest. It never hand-edits sliced files.

## MVP (v0.1) - local CLI first, dashboard panel later
Rationale: same as Slice v0.1. CLI proves the checker on real repos in days. A panel in Slice's dashboard is v0.2 once the verdicts are trusted.

1. Pre-flight sufficiency check: `ghostverify pre <project> <bundle> --task "..."`
   - Build the kept set from the manifest (file + mode + symbols kept).
   - Walk reachability from kept symbols: calls, imports, type references, constants, test fixtures referenced by kept tests.
   - Classify every reachable-but-not-kept item:
     - MISSING-HARD: called or imported directly, absent or excluded. Verdict fails.
     - MISSING-SOFT: present as signatures/interfaces only where the task likely needs a body (heuristic, labelled as heuristic, with the reason shown).
     - OK-STUB: signatures only and no body reference found.
   - Check task terms (TF-IDF selection, same method as `ghostslice task`) against the bundle: task names a file/symbol that is not in the bundle at any mode.
   - Secret and lint carry-over: if Slice lint (L001-L010) or secrets would have flagged a bundle file, surface it, do not re-implement the rules.
   - Exit codes: 0 = sufficient, 1 = hard gaps found, 2 = could not verify (parse failure, no graph). Never exit 0 on an unverified bundle.

2. Auto-expand (the self-healing half): `ghostverify fix <project> <bundle> -o <bundle-v2>`
   - Adds MISSING-HARD items at the lowest sufficient mode first (signatures before full), re-runs pre, and stops when pre passes or no progress is made (max 3 rounds, hard stop, report why).
   - Shows a token delta per round: original Slice total, expanded total, and the full-project total, so the cost of correctness is visible. Example shape only, real numbers come from the run.
   - Entry points and profile-protected modes from `.ghostslice.json` are never downgraded, matching Slice `--fit` behaviour.

3. Post-run patch check: `ghostverify post <project> <bundle> --patch <file.diff>`
   - Applies the patch to a temporary copy of the project only. Never applies to the working tree in v0.1.
   - Static pass: patch touches a file not in the bundle, references a symbol not in the bundle and not created by the patch, or edits a test to make it pass (flagged, not auto-rejected).
   - Test pass, opt-in only: runs the user-configured test command in the temp copy with a timeout, no network assumed, output captured and truncated. If no test command is configured, post is static-only and says so plainly.
   - Diagnosis: on failure, maps the failure back to context: "test X failed, it calls Y, Y was signatures-only in the bundle" when the evidence supports it. If it does not, it says "cause not tied to the slice" rather than inventing one.

4. Reports and ledger
   - `VERIFY.md`: verdict, gaps with file + symbol + reason, expansion rounds, patch outcome.
   - `verify-report.json`: same data machine-readable for CI and for GhostBench.
   - `.ghostverify-history.jsonl` in the project (gitignore suggested by `doctor`-style check): task, bundle hash, verdict, gaps, tokens before/after, patch result.

5. MCP tools (stdio, local only)
   - `verify_bundle`, `expand_bundle`, `check_patch` with capped summary payloads, same discipline as Slice MCP (no raw file bodies by default).

## Out of scope for v0.1
- Running an agent itself, calling any model API, or judging answer quality with an LLM. Verdicts are static/graph/test evidence only.
- Applying patches to the real working tree, committing, or opening PRs.
- Languages beyond what Slice already supports. Verify is only as strong as Slice's graph for that language and must label the difference.
- Fixing code. Verify expands context, it does not rewrite the agent's patch.
- Cloud, accounts, telemetry, paid layer.

## Build estimate
- Bundle reader + kept-set + reachability over Slice graph: 3-4 days
- Pre rules + task-term check + report: 2-3 days
- Auto-expand loop calling Slice export: 2 days
- Post static patch check + opt-in test runner (temp copy, timeout, capture): 3-4 days
- Tests (golden bundles with planted gaps, adversarial patches), README, demo on GhostBus + GhostSlice itself: 2-3 days
Total: roughly 2 to 2.5 weeks to a verified v0.1 if built straight through. Post test-runner is the part most likely to slip; static-only pre + post could ship as v0.1 and the runner move to v0.2 if needed, with the scope cut stated up front.

## Pricing draft (NOT public, NOT decided)
- MIT core CLI free, same pattern as Slice / Seatbelt / Ghost Hands.
- Paid candidate later only: team CI gate packs / hosted verdict history. Do not build a paid layer before a free launch validates demand.

## Verification bar
- Planted-gap test: take the known-good GhostBus bundle (GhostSlice demo tree, 129,555 -> 45,770 estimated tokens on the calibrated estimator), remove a called file, Verify pre must fail it by name and `fix` must restore a passing bundle.
- Clean-bundle test: the unmodified GhostBus and GhostSlice bundles must pass pre for a task they were built for. False hard-gaps are a release blocker.
- Patch tests: a good patch passes, a patch referencing an unseen symbol is flagged, a patch that edits the test itself is flagged.
- Every verdict reproducible from `verify-report.json` + bundle manifest hash alone.

## Risks / honest notes
- Parser-lite limits carry over: for non-Python languages reachability is import + outline level, so MISSING-SOFT will be noisier there. Label per-language confidence in every report, do not average it away.
- Test running is the risky surface: arbitrary user commands, timeouts, and side effects. Temp copy + opt-in + timeout + no working-tree writes are the v0.1 guardrails, and Agent Seatbelt policy should govern any future version that runs tests in place.
- A sufficiency verdict is evidence, not a guarantee an agent will succeed. Copy must say "no gaps found by static check" not "this slice will work."

## Next gate
Ryan's go on: (1) GhostVerify as working name, (2) CLI-first, dashboard panel deferred, (3) post-run test execution opt-in only and temp-copy only, (4) build order: Verify is #1 of the four companions.
