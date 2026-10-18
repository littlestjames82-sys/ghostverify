"""CLI tests for pytest-fixture reachability in `ghostverify pre`.

Fixtures are referenced by NAME (parameter injection and
usefixtures markers), never by a call or an import, so a bundle
that excludes the file defining a fixture a kept test needs used
to pass `pre` silently and only fail when pytest ran. These tests
pin the fixture edges: excluded/absent fixture definitions are
MISSING-HARD by name, a signatures-only fixture is MISSING-SOFT,
fixture-to-fixture chains are followed, and look-alikes that are
NOT fixture references (an ordinary function's parameter, an
ambiguous same-name pair) produce no gap.
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

APP = '''def checkout(items):
    return sum(items)
'''

CONFTEST = '''import pytest


@pytest.fixture
def sample_items():
    return [10.0]


@pytest.fixture
def priced_items(sample_items):
    return sample_items
'''

TEST_APP = '''import pytest

from app import checkout


def test_checkout(sample_items):
    assert checkout(sample_items) == 10.0


@pytest.mark.usefixtures("priced_items")
def test_priced():
    assert True
'''


def write_project(dest: Path, files: dict[str, str]) -> Path:
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for rel, text in files.items():
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return dest


@unittest.skipUnless(helpers.cli_available(),
                     "ghostverify CLI not discoverable (see REPORT_B.md)")
class TestFixtureReachability(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-fixture-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))
        self.project = write_project(self.tmp / "project", {
            "app.py": APP, "conftest.py": CONFTEST,
            "test_app.py": TEST_APP})

    def _pre(self, bundle: Path, project: Path | None = None):
        return helpers.run_cli(
            ["pre", str(project or self.project), str(bundle),
             "--task", "Fix checkout totals"])

    def test_excluded_conftest_fixture_fails_by_name(self):
        bundle = helpers.make_bundle(
            self.tmp / "gap", project=self.project,
            modes={"conftest.py": "exclude"})
        proc = self._pre(bundle)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        report = helpers.load_report(bundle)
        self.assertIsNotNone(report)
        blob = json.dumps(helpers.gaps_of(report))
        # Parameter injection (sample_items) AND the usefixtures
        # marker (priced_items) are both named as hard gaps.
        self.assertIn("sample_items", blob)
        self.assertIn("priced_items", blob)
        self.assertIn("MISSING-HARD", blob)

    def test_absent_conftest_fixture_fails_by_name(self):
        bundle = helpers.make_bundle(
            self.tmp / "gap", project=self.project,
            omit=("conftest.py",))
        proc = self._pre(bundle)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("sample_items", proc.stdout + proc.stderr
                      + helpers.report_text(bundle))

    def test_clean_bundle_with_fixtures_passes_and_counts_refs(self):
        bundle = helpers.make_bundle(self.tmp / "clean",
                                     project=self.project)
        proc = self._pre(bundle)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = helpers.load_report(bundle)
        self.assertIsNotNone(report)
        # test -> sample_items, test -> priced_items (usefixtures),
        # priced_items -> sample_items (fixture chain): >= 2.
        self.assertGreaterEqual(report.get("fixture_references", 0), 2)

    def test_fixture_chain_into_excluded_file_fails_by_name(self):
        project = write_project(self.tmp / "chain_project", {
            "app.py": APP,
            "conftest.py": (
                "import pytest\n\n\n"
                "@pytest.fixture\n"
                "def priced_items(base_items):\n"
                "    return base_items\n"),
            "more_fixtures.py": (
                "import pytest\n\n\n"
                "@pytest.fixture\n"
                "def base_items():\n"
                "    return [10.0]\n"),
            "test_app.py": (
                "from app import checkout\n\n\n"
                "def test_checkout(priced_items):\n"
                "    assert checkout(priced_items) == 10.0\n"),
        })
        bundle = helpers.make_bundle(
            self.tmp / "chain", project=project,
            modes={"more_fixtures.py": "exclude"})
        proc = self._pre(bundle, project=project)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        blob = json.dumps(helpers.gaps_of(helpers.load_report(bundle)))
        self.assertIn("base_items", blob)

    def test_signatures_only_fixture_is_soft_not_hard(self):
        bundle = helpers.make_bundle(
            self.tmp / "soft", project=self.project,
            modes={"conftest.py": "signatures"})
        proc = self._pre(bundle)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        report = helpers.load_report(bundle)
        self.assertIsNotNone(report)
        soft = json.dumps(report.get("soft_gaps", []))
        self.assertIn("sample_items", soft)
        self.assertNotIn("MISSING-HARD",
                         json.dumps(helpers.gaps_of(report)))

    def test_class_level_usefixtures_fails_by_name(self):
        project = write_project(self.tmp / "cls_project", {
            "app.py": APP, "conftest.py": CONFTEST,
            "test_app.py": (
                "import pytest\n\n\n"
                '@pytest.mark.usefixtures("sample_items")\n'
                "class TestCheckout:\n"
                "    def test_one(self):\n"
                "        assert True\n"),
        })
        bundle = helpers.make_bundle(
            self.tmp / "cls", project=project,
            modes={"conftest.py": "exclude"})
        proc = self._pre(bundle, project=project)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("sample_items", proc.stdout + proc.stderr
                      + helpers.report_text(bundle))

    def test_ordinary_parameter_sharing_a_fixture_name_is_not_a_gap(self):
        # pytest never injects into ordinary functions: a plain
        # parameter named like a fixture is not a reference, so
        # excluding conftest.py must not fail this bundle.
        project = write_project(self.tmp / "plain_project", {
            "app.py": (
                "def helper(sample_items):\n"
                "    return sample_items\n\n\n"
                "def checkout(items):\n"
                "    return sum(items)\n"),
            "conftest.py": CONFTEST,
            "test_app.py": (
                "from app import checkout\n\n\n"
                "def test_checkout():\n"
                "    assert checkout([1.0]) == 1.0\n"),
        })
        bundle = helpers.make_bundle(
            self.tmp / "plain", project=project,
            modes={"conftest.py": "exclude"})
        proc = self._pre(bundle, project=project)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_ambiguous_same_name_fixtures_are_not_fanned_out(self):
        # Two unrelated modules define a fixture with the same
        # name and neither is in the requesting file or a
        # conftest: Verify leaves it unresolved rather than
        # inventing gaps against both.
        fixture = ("import pytest\n\n\n@pytest.fixture\n"
                   "def shared_items():\n    return [1.0]\n")
        project = write_project(self.tmp / "amb_project", {
            "app.py": APP,
            "a_fixtures.py": fixture, "b_fixtures.py": fixture,
            "test_app.py": (
                "from app import checkout\n\n\n"
                "def test_checkout(shared_items):\n"
                "    assert checkout(shared_items) == 1.0\n"),
        })
        bundle = helpers.make_bundle(
            self.tmp / "amb", project=project,
            modes={"a_fixtures.py": "exclude",
                   "b_fixtures.py": "exclude"})
        proc = self._pre(bundle, project=project)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
