# GhostVerify examples

A three-file demo project (`demo_project/`: `app.py` calls
`pricing.compute_total` and `formatting.format_total`) and two
patches (`patches/`) to walk the whole Slice, Verify, patch loop
by hand. Commands below assume the install from the main README.

## 1. Make a bundle with GhostSlice

```bash
ghostslice export examples/demo_project -o /tmp/demo-context
```

## 2. Plant a gap and watch pre catch it

Open `/tmp/demo-context/manifest.json`, set the `pricing.py`
entry's `mode` to `exclude`, and delete
`/tmp/demo-context/files/pricing.py`. Then:

```bash
ghostverify pre examples/demo_project /tmp/demo-context \
  --task "Fix checkout totals in app.py using compute_total and format_total"
echo $?   # 1: hard gap, compute_total is called but not in the bundle
```

`VERIFY.md` and `verify-report.json` next to the bundle name the
gap: file `pricing.py`, symbol `compute_total`, class MISSING-HARD.

## 3. Let fix heal it

```bash
ghostverify fix examples/demo_project /tmp/demo-context -o /tmp/demo-context-v2 \
  --task "Fix checkout totals in app.py using compute_total and format_total"
ghostverify pre examples/demo_project /tmp/demo-context-v2 \
  --task "Fix checkout totals in app.py using compute_total and format_total"
echo $?   # 0: no gaps found by static check
```

The fix output shows the token delta per expansion round, so the
cost of correctness is visible next to the original bundle total
and the full-project total.

## 4. Check patches before accepting them

```bash
# Touches only formatting.py, a file the agent saw: passes.
ghostverify post examples/demo_project /tmp/demo-context-v2 \
  --patch examples/patches/good_patch.diff

# Adds a call to process_refund, a symbol that was never in the
# bundle and is not created by the patch: flagged.
ghostverify post examples/demo_project /tmp/demo-context-v2 \
  --patch examples/patches/unseen_symbol_patch.diff
```

Post never modifies `demo_project/`: patches are applied to a
temporary copy only. With no test command configured, post says
plainly that it ran static checks only.
