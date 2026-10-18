"""Fixture-validity tests: no GhostVerify CLI required.

These prove the fixtures themselves encode the plan's verification
bar, so a later CLI failure is a product failure, not a bad fixture:

- the clean bundle covers every symbol the fixture app calls;
- the planted-gap bundles really do remove a called file
  (``pricing.py`` / ``compute_total``) in both gap shapes
  (mode ``exclude`` and manifest-absent);
- bundle manifests are internally consistent (sha256 + totals),
  the same invariant ``ghostslice check`` enforces;
- each patch fixture touches the file and symbol the CLI tests
  expect it to touch, and only that.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402


class TestFixtureProject(unittest.TestCase):
    def test_app_calls_the_symbols_the_gap_removes(self):
        calls = helpers.python_calls(helpers.MINI_PROJECT / "app.py")
        self.assertIn("compute_total", calls)
        self.assertIn("format_total", calls)
        defs = helpers.python_defs(helpers.MINI_PROJECT / "pricing.py")
        self.assertIn("compute_total", defs)

    def test_project_is_self_consistent(self):
        # The fixture project actually runs: checkout([10.0]) -> $10.80
        sys.path.insert(0, str(helpers.MINI_PROJECT))
        try:
            import app
            self.assertEqual(app.checkout([10.0]), "$10.80")
        finally:
            sys.path.remove(str(helpers.MINI_PROJECT))
            sys.modules.pop("app", None)
            sys.modules.pop("pricing", None)
            sys.modules.pop("formatting", None)


class TestBundleFixtures(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-fixtures-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))

    def _manifest(self, bundle: Path) -> dict:
        return json.loads((bundle / "manifest.json").read_text())

    def _check_manifest_consistency(self, bundle: Path):
        manifest = self._manifest(bundle)
        files = {entry["path"]: entry for entry in manifest["files"]}
        self.assertEqual(
            manifest["total_sliced_tokens"],
            sum(e["sliced_tokens"] for e in files.values()))
        self.assertEqual(
            manifest["total_original_tokens"],
            sum(e["original_tokens"] for e in files.values()))
        for entry in files.values():
            content_path = bundle / "files" / entry["path"]
            if entry["mode"] == "exclude":
                self.assertFalse(content_path.exists(),
                                 "excluded file must not ship content")
                self.assertEqual(entry["sliced_tokens"], 0)
            else:
                self.assertTrue(content_path.exists())
                digest = hashlib.sha256(content_path.read_bytes()).hexdigest()
                self.assertEqual(digest, entry["sha256"],
                                 f"sha256 mismatch for {entry['path']}")
        return files

    def test_clean_bundle_is_consistent_and_complete(self):
        bundle = helpers.make_bundle(self.tmp / "clean")
        files = self._check_manifest_consistency(bundle)
        for rel in ("app.py", "pricing.py", "formatting.py"):
            self.assertIn(rel, files)
            self.assertEqual(files[rel]["mode"], "full")
        # Every name app.py calls is defined by a file in the bundle.
        defined = set()
        for rel in files:
            if files[rel]["mode"] != "exclude":
                defined |= helpers.python_defs(bundle / "files" / rel)
        calls = helpers.python_calls(bundle / "files" / "app.py")
        self.assertTrue(calls <= defined | {"checkout"},
                        f"clean bundle leaves calls undefined: {calls - defined}")

    def test_planted_gap_excluded_mode(self):
        bundle = helpers.make_bundle(
            self.tmp / "gap", modes={"pricing.py": "exclude"})
        files = self._check_manifest_consistency(bundle)
        self.assertEqual(files["pricing.py"]["mode"], "exclude")
        self.assertFalse((bundle / "files" / "pricing.py").exists())
        # And the gap is real: app.py still calls compute_total.
        calls = helpers.python_calls(bundle / "files" / "app.py")
        self.assertIn("compute_total", calls)

    def test_planted_gap_absent_from_manifest(self):
        bundle = helpers.make_bundle(self.tmp / "gap2", omit=("pricing.py",))
        files = self._check_manifest_consistency(bundle)
        self.assertNotIn("pricing.py", files)
        calls = helpers.python_calls(bundle / "files" / "app.py")
        self.assertIn("compute_total", calls)

    def test_signatures_variant_keeps_no_body(self):
        bundle = helpers.make_bundle(
            self.tmp / "soft", modes={"pricing.py": "signatures"})
        content = (bundle / "files" / "pricing.py").read_text()
        self.assertIn("def compute_total", content)
        self.assertNotIn("subtotal * (1 + TAX_RATE)", content)
        self._check_manifest_consistency(bundle)


class TestPatchFixtures(unittest.TestCase):
    def _patch(self, name: str) -> str:
        return (helpers.PATCHES / name).read_text(encoding="utf-8")

    def _touched(self, name: str) -> list[str]:
        return [line[6:].strip() for line in self._patch(name).splitlines()
                if line.startswith("+++ b/")]

    def test_good_patch_touches_only_a_bundle_file(self):
        self.assertEqual(self._touched("good_patch.diff"), ["formatting.py"])
        self.assertIn("format_total", self._patch("good_patch.diff"))

    def test_unseen_symbol_patch_names_an_undefined_symbol(self):
        self.assertEqual(self._touched("unseen_symbol_patch.diff"), ["app.py"])
        text = self._patch("unseen_symbol_patch.diff")
        self.assertIn("process_refund", text)
        # process_refund is defined nowhere in the fixture project.
        for src in helpers.MINI_PROJECT.rglob("*.py"):
            self.assertNotIn("process_refund", helpers.python_defs(src))

    def test_test_edit_patch_touches_only_the_test(self):
        self.assertEqual(self._touched("test_edit_patch.diff"), ["test_app.py"])
        self.assertIn('"$0.00"', self._patch("test_edit_patch.diff"))

    def test_out_of_bundle_patch_touches_deploy_py(self):
        self.assertEqual(
            self._touched("out_of_bundle_patch.diff"), ["deploy.py"])
        # deploy.py exists in the project but in no bundle variant:
        # make_bundle only bundles the four app/test/pricing/format
        # files a bundle is built from in these tests... deploy.py IS
        # walked by make_bundle, so the CLI post tests build their
        # bundles with omit=("deploy.py",). Pin that contract here.
        self.assertTrue((helpers.MINI_PROJECT / "deploy.py").exists())


if __name__ == "__main__":
    unittest.main()
