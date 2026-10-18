"""CLI tests for `ghostverify post` (plan MVP item 3) plus the
safety rules that surround all three commands.

Static pass must flag: a patch that touches a file not in the
bundle, a patch that references a symbol not in the bundle and not
created by the patch, and a patch that edits a test to make it
pass (flagged, not silently accepted). Post must NEVER modify the
working tree: patches are applied to a temporary copy only.

Flag semantics are read leniently (plan says "flagged" without an
exit-code table for post): a flagged patch must surface the flag in
stdout/stderr or in the report, and must not exit 0 silently with
no flag anywhere.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

TASK = "Fix checkout totals in app.py using compute_total and format_total"


def _source_hashes(hashes: dict) -> dict:
    """Hash map minus bookkeeping noise (ledgers, caches, pycache).

    GhostVerify appends its history ledger in the project (plan
    item 4), fix re-exports through GhostSlice which keeps its own
    cache/ledger there, and Python creates __pycache__ on import.
    None of those are source changes; every real source file must
    keep its hash.
    """
    ignored_prefixes = (".ghostverify-history.jsonl",
                        ".ghostslice-history.jsonl",
                        ".ghostslice-cache/")
    return {k: v for k, v in hashes.items()
            if "__pycache__" not in k
            and not k.startswith(ignored_prefixes)}


@unittest.skipUnless(helpers.cli_available(),
                     "ghostverify CLI not discoverable (see REPORT_B.md)")
class TestPost(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-post-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.project = helpers.copy_project(self.tmp / "project")
        # deploy.py is in the project but deliberately not bundled.
        self.bundle = helpers.make_bundle(
            self.tmp / "bundle", omit=("deploy.py",))
        self.before = helpers.tree_hashes(self.project)

    def tearDown(self):
        after = helpers.tree_hashes(self.project)
        self.assertEqual(_source_hashes(self.before),
                         _source_hashes(after),
                         "project source files changed during post")

    def _post(self, patch_name: str):
        return helpers.run_cli(
            ["post", str(self.project), str(self.bundle),
             "--patch", str(helpers.PATCHES / patch_name)])

    def _evidence(self, proc) -> str:
        return (proc.stdout + proc.stderr
                + helpers.report_text(self.bundle))

    def _assert_flagged(self, proc, *needles: str):
        evidence = self._evidence(proc)
        silent_pass = proc.returncode == 0 and not any(
            word in evidence.lower()
            for word in ("flag", "warn", "outside", "not in bundle",
                         "unseen", "unknown"))
        self.assertFalse(silent_pass,
                         "patch passed silently with no flag anywhere")
        for needle in needles:
            self.assertIn(needle, evidence)

    def test_good_patch_passes(self):
        proc = self._post("good_patch.diff")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_unseen_symbol_patch_is_flagged(self):
        proc = self._post("unseen_symbol_patch.diff")
        self._assert_flagged(proc, "process_refund")

    def test_test_editing_patch_is_flagged(self):
        proc = self._post("test_edit_patch.diff")
        evidence = self._evidence(proc)
        self.assertTrue(
            "test_app" in evidence
            and any(w in evidence.lower()
                    for w in ("flag", "warn", "test")),
            "test-editing patch was not flagged as touching a test")

    def test_out_of_bundle_patch_is_flagged(self):
        proc = self._post("out_of_bundle_patch.diff")
        self._assert_flagged(proc, "deploy")

    def test_post_without_test_command_says_static_only(self):
        # No test command is configured for the fixture project, so
        # the plan requires post to say it is static-only, plainly.
        proc = self._post("good_patch.diff")
        evidence = self._evidence(proc).lower()
        self.assertTrue(
            any(phrase in evidence for phrase in
                ("static", "no test command", "tests not run",
                 "test pass skipped")),
            "post did not state that test execution was skipped")

    def test_malformed_patch_never_passes(self):
        # The plan pins exit 2 (could-not-verify) under `pre` only;
        # for post the requirement is that a garbage patch is
        # rejected loudly (1 or 2), never accepted, never applied.
        bad = self.tmp / "bad.diff"
        bad.write_text("this is not a diff at all\n", encoding="utf-8")
        proc = helpers.run_cli(
            ["post", str(self.project), str(self.bundle),
             "--patch", str(bad)])
        self.assertIn(proc.returncode, (1, 2),
                      proc.stdout + proc.stderr)


@unittest.skipUnless(helpers.cli_available(),
                     "ghostverify CLI not discoverable (see REPORT_B.md)")
class TestSafetyInvariants(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-safe-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.project = helpers.copy_project(self.tmp / "project")

    def test_pre_and_fix_do_not_write_into_the_source_tree(self):
        bundle = helpers.make_bundle(
            self.tmp / "gap", modes={"pricing.py": "exclude"})
        before = helpers.tree_hashes(self.project)
        helpers.run_cli(["pre", str(self.project), str(bundle),
                         "--task", TASK])
        helpers.run_cli(["fix", str(self.project), str(bundle),
                         "-o", str(self.tmp / "fixed"), "--task", TASK])
        after = helpers.tree_hashes(self.project)
        # The history ledger is the one allowed new file in the
        # project (plan item 4); source files must never change.
        changed = {k for k in before
                   if k in after and before[k] != after[k]}
        self.assertEqual(changed, set(),
                         f"source files changed: {changed}")
        new_files = set(after) - set(before)
        allowed_prefixes = (".ghostverify-history.jsonl",
                            ".ghostslice-history.jsonl",
                            ".ghostslice-cache/")
        self.assertTrue(
            all(name.startswith(allowed_prefixes)
                or "__pycache__" in name for name in new_files),
            f"unexpected new files in project: {new_files}")

    def test_secret_shaped_content_is_never_echoed(self):
        # Plant a fake secret-shaped line in a bundle file. Whether
        # pre passes or flags it, the literal must never appear in
        # any output or report (plan: findings are redacted).
        secret = "fixture-secret-value-12345"
        bundle = helpers.make_bundle(self.tmp / "clean")
        target = bundle / "files" / "formatting.py"
        target.write_text(
            target.read_text() + f'\nAPI_TOKEN = "{secret}"\n',
            encoding="utf-8")
        proc = helpers.run_cli(["pre", str(self.project), str(bundle),
                                "--task", TASK])
        evidence = (proc.stdout + proc.stderr
                    + helpers.report_text(bundle))
        self.assertNotIn(secret, evidence)


if __name__ == "__main__":
    unittest.main()
