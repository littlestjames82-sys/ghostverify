"""Regression tests for the two GhostVerify seam defects found by
the Oct 14, 2026 ecosystem pass (ECOSYSTEM.md, Seams #5), via the
Fixture -> Slice -> Verify loop on a real bundle:

1. Phantom gap symbols. Verify's scanner registered assignments
   inside function bodies as `constant` symbols, so a local variable
   named e.g. `budget` became the symbol `find_budget.budget`, and
   task-word matching reported it as a MISSING-HARD gap -- a name
   that exists nowhere as a definition and that `fix` could never
   close. Pin: every gap symbol Verify reports must resolve to a
   symbol that actually exists in the project (computed independently
   here with ast) or in the bundle manifest; a task word that
   matches only a local variable is stated in plain words under
   `ungrounded_requirements`, never as a gap.

2. fix cited an `expanded_bundle` path it never wrote (when a round
   was recorded but no gap was upgradeable, nothing was exported).
   Pin: every path a fix report cites exists on disk after the run;
   when nothing was written, `expanded_bundle` is null and the
   output directory was not created.
"""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

EMERGENT_TASK = ("Fix the budget tier ladder in the pack builder so a "
                 "fittable tier ships instead of marking the pack "
                 "over budget")
REAL_SYMBOL_TASK = "Use apply_budget to fix the budget ladder"
FIX_TASK = "Fix checkout totals in app.py using compute_total and format_total"

BUDGETING = '''"""Budget helpers."""


def find_budget(manifest):
    budget = None
    estimate = None
    for key in ("budget_tokens",):
        budget = manifest.get(key)
    return budget, estimate


def check_budget(manifest):
    budget, estimate = find_budget(manifest)
    return budget is not None


def apply_budget(manifest):
    """Apply the budget tier ladder."""
    return find_budget(manifest)
'''

APP = '''"""App entry."""


def main():
    return "ok"
'''


def _est(text: str) -> int:
    return max(1, len(text) // 4)


def _write_project(dest: Path) -> Path:
    dest.mkdir(parents=True)
    (dest / "budgeting.py").write_text(BUDGETING, encoding="utf-8")
    (dest / "app.py").write_text(APP, encoding="utf-8")
    return dest


def _make_stripped_bundle(dest: Path) -> Path:
    """A Slice-shaped bundle: app.py full on disk; budgeting.py at
    mode 'strip-tests' with sliced_tokens 0 and NO bytes on disk --
    exactly what GhostSlice's default export did to the test files
    in the emergent run."""
    files_dir = dest / "files"
    files_dir.mkdir(parents=True)
    (files_dir / "app.py").write_text(APP, encoding="utf-8")
    manifest = {
        "tool": "ghostslice", "version": "fixture", "preset": "claude-code",
        "total_original_tokens": _est(APP) + _est(BUDGETING),
        "total_sliced_tokens": _est(APP),
        "files": [
            {"path": "app.py", "mode": "full", "language": "python",
             "original_tokens": _est(APP), "sliced_tokens": _est(APP),
             "sha256": hashlib.sha256(APP.encode()).hexdigest()},
            {"path": "budgeting.py", "mode": "strip-tests",
             "language": "python",
             "original_tokens": _est(BUDGETING), "sliced_tokens": 0,
             "sha256": hashlib.sha256(b"").hexdigest()},
        ],
    }
    (dest / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                        encoding="utf-8")
    (dest / "PROMPT.md").write_text("# fixture bundle\n", encoding="utf-8")
    return dest


def _real_symbols(project: Path) -> set:
    """Names that actually exist in the tree, computed independently
    of GhostVerify: every def/class at any nesting, module-level and
    class-level assignment targets, and every file path. Function
    locals are deliberately NOT in this set."""
    names: set = set()
    for py in sorted(Path(project).rglob("*.py")):
        rel = py.relative_to(project).as_posix()
        names.add(rel)
        tree = ast.parse(py.read_text(encoding="utf-8"))

        def targets_of(node):
            ts = node.targets if isinstance(node, ast.Assign) else [node.target]
            out = []
            for t in ts:
                if isinstance(t, ast.Name):
                    out.append(t.id)
                elif isinstance(t, (ast.Tuple, ast.List)):
                    out.extend(e.id for e in t.elts
                               if isinstance(e, ast.Name))
            return out

        def walk(body, prefix, in_class):
            for node in body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    q = f"{prefix}.{node.name}" if prefix else node.name
                    names.add(q)
                    walk(node.body, q, False)
                elif isinstance(node, ast.ClassDef):
                    q = f"{prefix}.{node.name}" if prefix else node.name
                    names.add(q)
                    walk(node.body, q, True)
                elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                    if not prefix or in_class:
                        for t in targets_of(node):
                            names.add(f"{prefix}.{t}" if prefix else t)

        walk(tree.body, "", False)
    return names


def _assert_all_gaps_grounded(case: unittest.TestCase, report: dict,
                              project: Path) -> None:
    real = _real_symbols(project)
    manifest_symbols = set()
    bundle = report.get("bundle")
    if bundle:
        mpath = Path(bundle) / "manifest.json"
        if mpath.is_file():
            for entry in json.loads(mpath.read_text())["files"]:
                manifest_symbols.update(entry.get("symbols_included") or [])
    for bucket in ("gaps", "soft_gaps"):
        for g in report.get(bucket) or []:
            sym = g.get("symbol", "")
            case.assertTrue(
                sym in real or sym in manifest_symbols,
                f"{bucket} names a symbol that exists nowhere: {sym!r} "
                f"(report kind={report.get('kind')})")


def _assert_fix_paths_exist(case: unittest.TestCase, report: dict) -> None:
    eb = report.get("expanded_bundle")
    if eb is not None:
        case.assertTrue(Path(eb).is_dir(),
                        f"fix cites expanded_bundle that does not "
                        f"exist on disk: {eb}")
        case.assertTrue((Path(eb) / "manifest.json").is_file(),
                        f"expanded_bundle has no manifest: {eb}")


@unittest.skipUnless(helpers.cli_available(),
                     "ghostverify CLI not discoverable (see REPORT_B.md)")
class TestPhantomGapSymbols(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-seam-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.project = _write_project(self.tmp / "project")
        self.bundle = _make_stripped_bundle(self.tmp / "bundle")

    def _pre(self, task: str):
        return helpers.run_cli(
            ["pre", str(self.project), str(self.bundle), "--task", task])

    def test_task_word_matching_only_a_local_is_not_a_gap(self):
        proc = self._pre(EMERGENT_TASK)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = helpers.load_report(self.bundle)
        self.assertIsNotNone(report)
        self.assertEqual(report.get("verdict"), "sufficient")
        blob = json.dumps(report)
        # The phantom symbols from the ecosystem run must not appear.
        self.assertNotIn("find_budget.budget", blob)
        self.assertNotIn("check_budget.budget", blob)
        for g in (report.get("gaps") or []) + (report.get("soft_gaps") or []):
            self.assertFalse(str(g.get("symbol", "")).endswith(".budget"),
                             f"phantom local-variable gap: {g}")
        _assert_all_gaps_grounded(self, report, self.project)
        # The requirement is stated in plain words instead.
        ungrounded = report.get("ungrounded_requirements") or []
        self.assertTrue(
            any(u.get("term") == "budget" for u in ungrounded),
            f"no ungrounded requirement for 'budget': {ungrounded}")
        text = helpers.report_text(self.bundle)
        self.assertIn("Ungrounded requirements", text)
        self.assertIn("local variable", text)

    def test_grounded_gap_is_still_reported_by_name(self):
        proc = self._pre(REAL_SYMBOL_TASK)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        report = helpers.load_report(self.bundle)
        self.assertIsNotNone(report)
        symbols = [g.get("symbol") for g in report.get("gaps") or []]
        self.assertIn("apply_budget", symbols,
                      f"real task-named gap lost: {symbols}")
        _assert_all_gaps_grounded(self, report, self.project)


@unittest.skipUnless(helpers.cli_available(),
                     "ghostverify CLI not discoverable (see REPORT_B.md)")
class TestFixReportPaths(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-seam-fix-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))

    def test_fix_cites_no_bundle_it_did_not_write(self):
        project = _write_project(self.tmp / "project")
        bundle = _make_stripped_bundle(self.tmp / "bundle")
        fixed = self.tmp / "bundle-v2"
        proc = helpers.run_cli(
            ["fix", str(project), str(bundle), "-o", str(fixed),
             "--task", REAL_SYMBOL_TASK])
        # apply_budget is a real gap, but its file's strip-tests mode
        # has no upgrade path, so fix can export nothing.
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        report = helpers.load_report(bundle)
        self.assertIsNotNone(report)
        self.assertEqual(report.get("verdict"), "gaps_remain")
        self.assertTrue(report.get("expansion_rounds"),
                        "expected the no-upgradeable round to be recorded")
        _assert_fix_paths_exist(self, report)
        self.assertIsNone(report.get("expanded_bundle"),
                          "fix cited an expanded bundle it never wrote: "
                          f"{report.get('expanded_bundle')}")
        self.assertFalse(fixed.exists(),
                         "fix output dir appeared without a report "
                         "path being citable")

    def test_fix_expanded_bundle_exists_when_written(self):
        project = helpers.copy_project(self.tmp / "project")
        bundle = helpers.make_bundle(
            self.tmp / "gap", modes={"pricing.py": "exclude"})
        fixed = self.tmp / "fixed"
        proc = helpers.run_cli(
            ["fix", str(project), str(bundle), "-o", str(fixed),
             "--task", FIX_TASK])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = helpers.load_report(bundle)
        self.assertIsNotNone(report)
        _assert_fix_paths_exist(self, report)
        self.assertIsNotNone(report.get("expanded_bundle"),
                             "successful fix must cite its expanded "
                             "bundle")
        self.assertEqual(Path(report["expanded_bundle"]), fixed.resolve())
        # Self-healing on the real gap is intact: the expanded
        # bundle passes pre.
        proc2 = helpers.run_cli(
            ["pre", str(project), str(fixed), "--task", FIX_TASK])
        self.assertEqual(proc2.returncode, 0,
                         proc2.stdout + proc2.stderr)


if __name__ == "__main__":
    unittest.main()
