"""CLI tests for `ghostverify pre` (plan MVP item 1).

Exit-code contract from the plan: 0 = sufficient, 1 = hard gaps
found, 2 = could not verify. Never 0 on an unverified bundle.
Reports: verify-report.json + VERIFY.md next to the bundle.
All tests skip cleanly while no ghostverify CLI is discoverable.
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
# deploy_target exists in the project (deploy.py) but the bundle in
# this test omits deploy.py, so a task naming it must fail pre.
MISSING_TASK = "Update deploy_target in deploy.py"


@unittest.skipUnless(helpers.cli_available(),
                     "ghostverify CLI not discoverable (see REPORT_B.md)")
class TestPre(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-pre-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.project = helpers.copy_project(self.tmp / "project")

    def _pre(self, bundle: Path, task: str = TASK):
        return helpers.run_cli(
            ["pre", str(self.project), str(bundle), "--task", task])

    def test_clean_bundle_passes(self):
        bundle = helpers.make_bundle(self.tmp / "clean")
        proc = self._pre(bundle)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_clean_bundle_writes_reports(self):
        bundle = helpers.make_bundle(self.tmp / "clean")
        proc = self._pre(bundle)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report, md = helpers.find_report(bundle)
        self.assertIsNotNone(report, "verify-report.json not written")
        self.assertIsNotNone(md, "VERIFY.md not written")
        data = json.loads(report.read_text(encoding="utf-8"))
        # A verdict must be machine-readable somewhere in the report.
        blob = json.dumps(data).lower()
        self.assertTrue(
            any(word in blob for word in ("sufficient", "pass", '"ok"')),
            f"no passing verdict recorded in report: {blob[:400]}")
        # Plan wording rule: a pass is "no gaps found by static
        # check", never a guarantee the slice will work.
        text = helpers.report_text(bundle).lower()
        self.assertNotIn("this slice will work", text)
        self.assertNotIn("guaranteed to work", text)

    def test_planted_gap_excluded_file_fails_by_name(self):
        bundle = helpers.make_bundle(
            self.tmp / "gap", modes={"pricing.py": "exclude"})
        proc = self._pre(bundle)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        evidence = proc.stdout + proc.stderr + helpers.report_text(bundle)
        self.assertIn("pricing", evidence)
        self.assertIn("compute_total", evidence)
        report = helpers.load_report(bundle)
        self.assertIsNotNone(report)
        gaps = helpers.gaps_of(report)
        self.assertTrue(gaps, "report carries no gap list")
        blob = json.dumps(gaps)
        self.assertIn("compute_total", blob)
        self.assertTrue(
            any(tag in blob for tag in ("MISSING-HARD", "missing-hard",
                                        "hard")),
            f"gap not classified as hard: {blob[:400]}")

    def test_planted_gap_absent_file_fails_by_name(self):
        bundle = helpers.make_bundle(self.tmp / "gap2", omit=("pricing.py",))
        proc = self._pre(bundle)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        evidence = proc.stdout + proc.stderr + helpers.report_text(bundle)
        self.assertIn("compute_total", evidence)

    def test_task_naming_a_missing_symbol_fails(self):
        bundle = helpers.make_bundle(self.tmp / "clean",
                                     omit=("deploy.py",))
        proc = self._pre(bundle, task=MISSING_TASK)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        evidence = proc.stdout + proc.stderr + helpers.report_text(bundle)
        self.assertIn("deploy", evidence)

    def test_signatures_only_dependency_is_not_a_hard_gap(self):
        # pricing.py at signatures: the symbol IS in the bundle.
        # The plan classifies this MISSING-SOFT (heuristic) or
        # OK-STUB, never MISSING-HARD, so the exit must not be 1
        # for a hard gap alone; if it is 1, the report must class
        # the finding as soft, not hard.
        bundle = helpers.make_bundle(
            self.tmp / "soft", modes={"pricing.py": "signatures"})
        proc = self._pre(bundle)
        self.assertIn(proc.returncode, (0, 1),
                      proc.stdout + proc.stderr)
        if proc.returncode == 1:
            blob = helpers.report_text(bundle)
            self.assertNotIn("MISSING-HARD", blob)
            self.assertNotIn("missing-hard", blob)

    def test_missing_bundle_is_could_not_verify(self):
        proc = self._pre(self.tmp / "does-not-exist")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_corrupt_manifest_is_could_not_verify(self):
        bundle = helpers.make_bundle(self.tmp / "corrupt")
        (bundle / "manifest.json").write_text("{ not json",
                                              encoding="utf-8")
        proc = self._pre(bundle)
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_pre_appends_a_ledger_line(self):
        bundle = helpers.make_bundle(self.tmp / "clean")
        proc = self._pre(bundle)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        candidates = [self.project / ".ghostverify-history.jsonl",
                      bundle.parent / ".ghostverify-history.jsonl",
                      bundle / ".ghostverify-history.jsonl"]
        ledgers = [p for p in candidates if p.exists()]
        self.assertTrue(ledgers,
                        "no .ghostverify-history.jsonl in project or "
                        "next to the bundle")
        lines = [line for line in
                 ledgers[0].read_text(encoding="utf-8").splitlines()
                 if line.strip()]
        self.assertTrue(lines, "ledger exists but is empty")
        record = json.loads(lines[-1])
        blob = json.dumps(record).lower()
        self.assertTrue(any(k in blob for k in ("verdict", "result",
                                                "sufficient", "status")),
                        f"ledger line carries no verdict: {blob[:300]}")

    def test_verdict_is_reproducible(self):
        # Plan verification bar: a verdict must be reproducible from
        # verify-report.json + the bundle manifest hash alone.
        bundle = helpers.make_bundle(
            self.tmp / "gap", modes={"pricing.py": "exclude"})
        self._pre(bundle)
        first = helpers.report_text(bundle)
        first_hash = json.loads(
            (bundle / "manifest.json").read_text())["files"][0]["sha256"]
        proc = self._pre(bundle)
        self.assertEqual(proc.returncode, 1)
        report = helpers.load_report(bundle)
        self.assertIsNotNone(report)
        blob = json.dumps(report) + first
        self.assertTrue(
            first_hash in blob or "manifest" in blob.lower(),
            "report does not reference the bundle manifest/hash")
        gaps_first = sorted(
            str(g) for g in helpers.gaps_of(
                json.loads(json.dumps(report))) if "compute_total" in str(g))
        self.assertTrue(gaps_first or "compute_total" in first)


if __name__ == "__main__":
    unittest.main()
