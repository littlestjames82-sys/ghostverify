"""Evidence-contract tests: every verify-report.json GhostVerify
writes carries the shared evidence contract fields (tool_version,
verification_level + basis, UTC timestamp).

Verify's honest level is "static-analysis": it compares a bundle
against the project statically and executes no project code and no
agent. The one exception is `post --run-tests`, which runs the
project's own configured test command against the patched temporary
copy; only when those tests actually ran does the report state
"tests-executed".

The CLI tests are black-box (helpers, like the rest of the suite);
one in-process test pins that a pre-contract report (no new fields)
still flows through Verify's own report readers (markdown renderer
and ledger appender) unchanged.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

import ghostverify  # noqa: E402
from ghostverify.reports import append_ledger, render_markdown  # noqa: E402

TASK = "Fix checkout totals in app.py using compute_total and format_total"
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def assert_contract_fields(case: unittest.TestCase, report: dict,
                           level: str = "static-analysis") -> None:
    case.assertEqual(report.get("tool"), "ghostverify")
    case.assertEqual(report.get("tool_version"), ghostverify.__version__)
    # The pre-existing "version" key stays; the contract field is
    # the same value under the shared name.
    case.assertEqual(report.get("tool_version"), report.get("version"))
    case.assertEqual(report.get("verification_level"), level)
    basis = report.get("verification_level_basis") or ""
    case.assertIn(level, basis)
    stamp = report.get("timestamp") or ""
    case.assertRegex(stamp, TS_RE)
    # Parseable as ISO 8601 UTC, not just shaped like it.
    datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")


@unittest.skipUnless(helpers.cli_available(),
                     "ghostverify CLI not discoverable (see REPORT_B.md)")
class TestContractFieldsOnCliReports(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-contract-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.project = helpers.copy_project(self.tmp / "project")

    def test_pre_report_carries_contract_fields(self):
        bundle = helpers.make_bundle(self.tmp / "clean")
        proc = helpers.run_cli(
            ["pre", str(self.project), str(bundle), "--task", TASK])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = helpers.load_report(bundle)
        self.assertIsNotNone(report)
        assert_contract_fields(self, report)
        # VERIFY.md shows the same fields on its face.
        _, md = helpers.find_report(bundle)
        text = md.read_text(encoding="utf-8")
        self.assertIn("static-analysis", text)
        self.assertIn(ghostverify.__version__, text)
        self.assertIn(report["timestamp"], text)

    def test_pre_could_not_verify_report_carries_contract_fields(self):
        # Even the exit-2 path (bundle unreadable) writes a report
        # that states its level and version.
        proc = helpers.run_cli(
            ["pre", str(self.project), str(self.tmp / "nope"),
             "--task", TASK, "--out-dir", str(self.tmp / "out")])
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        report = json.loads(
            (self.tmp / "out" / "verify-report.json").read_text())
        self.assertEqual(report.get("verdict"), "could_not_verify")
        assert_contract_fields(self, report)

    @unittest.skipUnless(helpers.ghostslice_available(),
                         "GhostSlice integration not available")
    def test_fix_report_carries_contract_fields(self):
        bundle = helpers.make_bundle(
            self.tmp / "gap", modes={"pricing.py": "exclude"})
        fixed = self.tmp / "fixed"
        proc = helpers.run_cli(
            ["fix", str(self.project), str(bundle), "-o", str(fixed),
             "--task", TASK])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = helpers.load_report(bundle)
        self.assertIsNotNone(report)
        self.assertEqual(report.get("kind"), "fix")
        assert_contract_fields(self, report)

    def test_post_static_report_is_static_analysis(self):
        bundle = helpers.make_bundle(self.tmp / "bundle",
                                     omit=("deploy.py",))
        proc = helpers.run_cli(
            ["post", str(self.project), str(bundle), "--patch",
             str(helpers.PATCHES / "good_patch.diff")])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = helpers.load_report(bundle)
        self.assertIsNotNone(report)
        self.assertEqual(report.get("kind"), "post")
        self.assertEqual(report["post"]["tests"]["status"].split(" ")[0],
                         "not")
        assert_contract_fields(self, report, "static-analysis")

    def test_post_run_tests_report_is_tests_executed(self):
        # The one executing flow: the project's own test command
        # runs against the patched temp copy (the good patch changes
        # format_total's output, and the command checks exactly
        # that), so the honest level is tests-executed.
        bundle = helpers.make_bundle(self.tmp / "bundle",
                                     omit=("deploy.py",))
        check = ("python3 -c 'from formatting import format_total; "
                 "assert format_total(1.0).startswith(\"USD\")'")
        proc = helpers.run_cli(
            ["post", str(self.project), str(bundle), "--patch",
             str(helpers.PATCHES / "good_patch.diff"),
             "--run-tests", "--test-command", check])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = helpers.load_report(bundle)
        self.assertIsNotNone(report)
        self.assertEqual(report["post"]["tests"]["status"], "passed")
        assert_contract_fields(self, report, "tests-executed")
        self.assertIn("test command",
                      report["verification_level_basis"])


class TestLegacyReportStillReadable(unittest.TestCase):
    """A pre-contract report (no tool_version / verification_level /
    timestamp) must still parse in Verify's own readers: the
    markdown renderer and the ledger appender take reports as dicts
    and must not require the new fields."""

    def _legacy_report(self) -> dict:
        return {"tool": "ghostverify", "version": "0.0.9",
                "kind": "pre", "project": "/somewhere/project",
                "bundle": "/somewhere/bundle",
                "bundle_manifest_sha256": "deadbeef",
                "verdict": "sufficient", "exit_code": 0,
                "task": "legacy run", "gaps": [], "soft_gaps": [],
                "notes": []}

    def test_render_markdown_without_contract_fields(self):
        text = render_markdown(self._legacy_report())
        self.assertIn("GhostVerify report", text)
        self.assertIn("sufficient", text)

    def test_append_ledger_without_contract_fields(self):
        tmp = Path(tempfile.mkdtemp(prefix="gv-legacy-"))
        self.addCleanup(lambda: shutil.rmtree(tmp, ignore_errors=True))
        path = append_ledger(tmp, self._legacy_report())
        self.assertIsNotNone(path)
        record = json.loads(path.read_text().splitlines()[-1])
        self.assertEqual(record["verdict"], "sufficient")


if __name__ == "__main__":
    unittest.main()
