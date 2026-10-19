"""CLI tests for `ghostverify fix` (plan MVP item 2).

The auto-expand loop must: add MISSING-HARD items, produce a NEW
bundle with a new manifest (never hand-edit the source bundle),
re-run pre until it passes or max 3 rounds elapse, and show the
token delta per round.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

TASK = "Fix checkout totals in app.py using compute_total and format_total"


@unittest.skipUnless(helpers.cli_available(),
                     "ghostverify CLI not discoverable (see REPORT_B.md)")
class TestFix(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-fix-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.project = helpers.copy_project(self.tmp / "project")

    def _pre(self, bundle: Path):
        return helpers.run_cli(
            ["pre", str(self.project), str(bundle), "--task", TASK])

    @unittest.skipUnless(helpers.ghostslice_available(),
                         "GhostSlice integration not available")
    def test_fix_restores_a_passing_bundle(self):
        bundle = helpers.make_bundle(
            self.tmp / "gap", modes={"pricing.py": "exclude"})
        fixed = self.tmp / "fixed"
        proc = helpers.run_cli(
            ["fix", str(self.project), str(bundle), "-o", str(fixed),
             "--task", TASK])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue((fixed / "manifest.json").exists(),
                        "fix did not write a new bundle manifest")
        # The expanded bundle must now pass pre.
        proc2 = self._pre(fixed)
        self.assertEqual(proc2.returncode, 0,
                         proc2.stdout + proc2.stderr)
        # The restored file is in the new bundle with real content.
        content = fixed / "files" / "pricing.py"
        self.assertTrue(content.exists(),
                        "fix did not restore pricing.py content")
        self.assertIn("compute_total", content.read_text())

    def test_fix_does_not_modify_the_source_bundle(self):
        bundle = helpers.make_bundle(
            self.tmp / "gap", modes={"pricing.py": "exclude"})
        before = helpers.tree_hashes(bundle)
        fixed = self.tmp / "fixed"
        helpers.run_cli(["fix", str(self.project), str(bundle),
                         "-o", str(fixed), "--task", TASK])
        after = helpers.tree_hashes(bundle)
        # Report files next to the bundle are allowed to appear;
        # bundle payload (manifest, PROMPT, files/) must not change.
        changed = {k for k in before
                   if k in after and before[k] != after[k]}
        self.assertEqual(changed, set(),
                         f"fix mutated the source bundle: {changed}")
        manifest = json.loads((bundle / "manifest.json").read_text())
        modes = {e["path"]: e["mode"] for e in manifest["files"]}
        self.assertEqual(modes["pricing.py"], "exclude")

    @unittest.skipUnless(helpers.ghostslice_available(),
                         "GhostSlice integration not available")
    def test_fix_reports_token_delta_and_rounds(self):
        bundle = helpers.make_bundle(
            self.tmp / "gap", modes={"pricing.py": "exclude"})
        fixed = self.tmp / "fixed"
        proc = helpers.run_cli(
            ["fix", str(self.project), str(bundle), "-o", str(fixed),
             "--task", TASK])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        evidence = (proc.stdout + proc.stderr
                    + helpers.report_text(bundle)
                    + helpers.report_text(fixed))
        self.assertTrue(any(word in evidence.lower()
                            for word in ("round", "delta", "tokens")),
                        "no round / token delta evidence in fix output")
        old_total = json.loads((bundle / "manifest.json").read_text())[
            "total_sliced_tokens"]
        new_total = json.loads((fixed / "manifest.json").read_text())[
            "total_sliced_tokens"]
        self.assertGreater(new_total, old_total,
                           "expanded bundle must cost more tokens "
                           "than the gapped one")

    @unittest.skipUnless(helpers.ghostslice_available(),
                         "GhostSlice integration not available")
    def test_fix_on_a_clean_bundle_stays_passing(self):
        bundle = helpers.make_bundle(self.tmp / "clean")
        fixed = self.tmp / "fixed"
        proc = helpers.run_cli(
            ["fix", str(self.project), str(bundle), "-o", str(fixed),
             "--task", TASK])
        self.assertIn(proc.returncode, (0, 1), proc.stdout + proc.stderr)
        target = fixed if (fixed / "manifest.json").exists() else bundle
        proc2 = self._pre(target)
        self.assertEqual(proc2.returncode, 0,
                         proc2.stdout + proc2.stderr)


if __name__ == "__main__":
    unittest.main()
