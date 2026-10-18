# GhostVerify

**Was the slice enough, and did the patch hold?**

GhostSlice proves a context bundle is well-formed (manifest hashes
and token totals match disk). It does not prove the bundle was
*sufficient* for the task, or that the patch an agent brings back
only uses what it was shown. GhostVerify is the checker that sits
on both sides of the agent run:

- **Before spend (`pre`)**: static sufficiency check of a GhostSlice
  bundle against the project graph and the task. Finds called or
  imported symbols that are absent or excluded before you pay for a
  run that was doomed by missing context.
- **Self-healing expand (`fix`)**: adds the missing items at the
  lowest sufficient mode and re-checks, producing a new bundle with
  a new manifest and a visible token delta per round.
- **After the run (`post`)**: checks the returned patch against the
  bundle (files touched, symbols referenced, tests edited) and,
  only when you have configured a test command, runs it in a
  temporary copy of the project. The working tree is never touched.

A passing verdict means **"no gaps found by static check"**. It is
evidence, not a guarantee that an agent will succeed.

By Ghost Developer Studio. MIT core (see PLAN.md for the pricing
posture: free core, paid layer not decided). Zero dependencies:
Python standard library only, local-first, no network calls, no
telemetry. Token counts come from GhostSlice's estimator and are
labelled estimates; prices come from GhostSlice's local, dated
`prices.json` table.

Position in the Ghost stack: GhostSlice governs what an agent
*reads*, GhostVerify governs whether what it read was *enough*,
Agent Seatbelt governs what an agent *does*, Ghost Hands is the
governed browser body.

## Install

```bash
cd ghostverify
pip install .
ghostverify --help
```

Or run from a checkout without installing:

```bash
PYTHONPATH=src python3 -m ghostverify --help
```

Requires Python 3.10 or newer.

## Quickstart

```bash
# 1. Build a bundle with GhostSlice (a sibling tool)
ghostslice export ~/my-project -o /tmp/my-context

# 2. Check the bundle is sufficient for the task, before the run
ghostverify pre ~/my-project /tmp/my-context \
  --task "Fix checkout totals using compute_total"
echo $?   # 0 sufficient, 1 hard gaps found, 2 could not verify

# 3. If pre found hard gaps, expand the bundle and re-check
ghostverify fix ~/my-project /tmp/my-context -o /tmp/my-context-v2 \
  --task "Fix checkout totals using compute_total"

# 4. After the agent runs, check its patch against the bundle
ghostverify post ~/my-project /tmp/my-context-v2 --patch agent.diff
```

A worked walkthrough with a tiny demo project lives in
[`examples/`](examples/README.md).

## Commands

| Command | What it does |
|---|---|
| `ghostverify pre <project> <bundle> --task "..."` | Pre-flight sufficiency check: kept set from the bundle manifest, reachability over GhostSlice's call/import graph, task-term check. Writes `VERIFY.md` + `verify-report.json` next to the bundle and appends a verdict line to `.ghostverify-history.jsonl` in the project. |
| `ghostverify fix <project> <bundle> -o <bundle-v2> [--task "..."]` | Auto-expand: adds MISSING-HARD items (lowest sufficient mode first), re-runs `pre`, stops when it passes or after 3 rounds with no progress. Shows the token delta per round. Never downgrades entry points or profile-protected modes. Never edits the source bundle. |
| `ghostverify post <project> <bundle> --patch <file.diff>` | Post-run patch check, static by default. Applies the patch to a temporary copy only. Flags: files touched that are not in the bundle, symbols referenced that are not in the bundle and not created by the patch, and edits to test files. Runs the profile's configured test command in the temp copy, with a timeout, only when one is configured; otherwise it says plainly that it was static-only. |
| `ghostverify mcp` | Stdio MCP server (local only) exposing `verify_bundle`, `expand_bundle`, `check_patch` with capped summary payloads, no raw file bodies by default. |

### How `pre` classifies a finding

| Class | Meaning |
|---|---|
| `MISSING-HARD` | A kept symbol calls or imports this item directly and it is absent from the bundle or present only as `exclude`. The verdict fails (exit 1). |
| `MISSING-SOFT` | Present only as signatures/interfaces where the task likely needs a body. A heuristic, always labelled with its reason. Does not by itself fail the verdict. |
| `OK-STUB` | Present as signatures only and no body reference was found. Recorded, not a finding against the bundle. |

Task terms are checked the same way: if the task names a file or
symbol that is not in the bundle at any mode, that is reported
before the run, not discovered after it.

Findings carried over from GhostSlice (context lint L001-L010 and
the secret guard) are surfaced, not re-implemented. Secret findings
are always redacted: path, line, and type only, never the value.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Sufficient (`pre`), expanded bundle passes (`fix`), or patch check clean (`post`). |
| 1 | Hard gaps found (`pre`), could not expand to a passing bundle (`fix`), or the patch check flagged the patch (`post`). |
| 2 | Could not verify: missing or corrupt bundle, unparseable patch, or no graph could be built. A bundle that could not be verified never exits 0. |

Exact `post` flag semantics: editing a test file is *flagged, not
auto-rejected*. The report names the flag and the reason; accepting
or rejecting stays a human (or Seatbelt) decision.

## Reports

Every `pre` / `fix` / `post` run produces, next to the bundle:

- `VERIFY.md`: the human report. Verdict, each gap with its file,
  symbol, class, and reason, expansion rounds with token deltas,
  patch outcome, and per-language confidence (see Limits).
- `verify-report.json`: the same data machine-readable, for CI and
  for GhostBench. A verdict is reproducible from this file plus the
  bundle manifest hash alone.

And, in the project:

- `.ghostverify-history.jsonl`: one line per run (task, bundle
  hash, verdict, gaps, tokens before/after, patch result). Add it
  to `.gitignore` if you do not want verdict history in the repo.
  GhostVerify appends to this ledger only; it never rewrites
  GhostSlice's `.ghostslice-history.jsonl`.

## Profile

GhostVerify reads GhostSlice's `.ghostslice.json` in the project.
The profile is where you configure the optional test command for
`post` and where entry points and protected modes live; those
modes are never downgraded by `fix`. If no test command is
configured, `post` is static-only and says so in its report.

## Limits, stated plainly

- **Parser-lite languages are weaker evidence.** Python reachability
  uses GhostSlice's full call graph. Other languages use the import
  graph plus outline symbols, so MISSING-SOFT findings are noisier
  there. Every report labels confidence per language instead of
  averaging it away.
- **Static evidence is not run-time truth.** `pre` cannot see
  dynamic dispatch, reflection, or string-built imports. "No gaps
  found" means no gaps found *by this check*.
- **Test execution is opt-in and contained.** It runs only a
  command you configured, only in a temporary copy, with a
  timeout, and output captured and truncated. Nothing runs against
  your working tree in v0.1.
- **Diagnosis does not invent causes.** When a test failure cannot
  be tied to the slice by evidence, the report says the cause is
  not tied to the slice.
- **`fix` expands context, it does not fix code.** Patch problems
  are reported, never rewritten by GhostVerify.

## Out of scope (v0.1)

Running an agent, calling a model API, judging answer quality with
an LLM, applying patches to the working tree, committing, opening
PRs, cloud/accounts/telemetry, and any paid layer.

## Tests

```bash
PYTHONPATH=src python3 -m pytest tests -q     # full suite
python3 -m pytest tests/test_fixtures.py -q   # fixtures only, no CLI needed
```

The suite is black-box: CLI tests drive `pre` / `fix` / `post`
and the MCP handshake as a subprocess and skip cleanly if no
`ghostverify` CLI is discoverable. The fixture tests always run
and pin the verification bar from the plan: a clean bundle that
covers every called symbol, a planted-gap bundle in both gap
shapes (`exclude` mode and manifest-absent), and patch fixtures
for a good patch, an unseen-symbol patch, a test-editing patch,
and an out-of-bundle patch.

Fixtures live in `tests/fixtures/` (mini project + patches) and
the bundle builder in `tests/helpers.py` writes GhostSlice-shaped
manifests (per-file mode, original/sliced token estimates, and
sha256 of the shipped content).

## Layout

```
PLAN.md            the v0.1 plan this build implements
README.md          this file
src/ghostverify/   the implementation (core build)
tests/             black-box CLI tests + fixture-validity tests
examples/          three-file demo project, two patches, walkthrough
```
