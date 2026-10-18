"""Hardening regression tests (improve lane: harden, 2026-10-18).

Each test pins a real bug found by auditing the source: a crash
where the exit-code contract promises a clean 2, a patch path that
escaped the temp copy, a manifest that could talk its way to a
pass, a diff the parser misread, or a false-positive flag. These
tests import the package directly (unlike the black-box CLI suite)
because several bugs live below the CLI surface.
"""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import helpers  # noqa: E402

from ghostverify.bundle import BundleError, load_bundle  # noqa: E402
from ghostverify.patch import apply_patch, parse_patch  # noqa: E402
import ghostverify.core as core  # noqa: E402
from ghostverify.core import run_post, run_pre  # noqa: E402


def _write_bundle(dest: Path, entries: list, totals: dict | None = None) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "files").mkdir(exist_ok=True)
    manifest = {"preset": "claude-code",
                "total_original_tokens": 10, "total_sliced_tokens": 10,
                "files": entries}
    if totals:
        manifest.update(totals)
    (dest / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (dest / "PROMPT.md").write_text("x", encoding="utf-8")
    return dest


def _entry(path: str, mode: str = "full", **kw) -> dict:
    e = {"path": path, "mode": mode, "language": "python",
         "original_tokens": 5, "sliced_tokens": 5, "sha256": "0" * 64}
    e.update(kw)
    return e


class TestManifestValidation(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-harden-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))

    def test_non_integer_file_token_count_is_bundle_error(self):
        b = _write_bundle(self.tmp / "b", [_entry("a.py", original_tokens="abc")])
        with self.assertRaises(BundleError):
            load_bundle(b)

    def test_non_integer_total_is_bundle_error(self):
        b = _write_bundle(self.tmp / "b", [_entry("a.py")],
                          totals={"total_original_tokens": "xyz"})
        with self.assertRaises(BundleError):
            load_bundle(b)

    def test_run_pre_maps_corrupt_manifest_to_exit_2_not_traceback(self):
        project = helpers.copy_project(self.tmp / "project")
        bundle = helpers.make_bundle(self.tmp / "bundle")
        man = json.loads((bundle / "manifest.json").read_text())
        man["files"][0]["sliced_tokens"] = "not-a-number"
        (bundle / "manifest.json").write_text(json.dumps(man))
        report = run_pre(project, bundle, "anything")
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["verdict"], "could_not_verify")

    def test_symbols_included_as_string_is_bundle_error(self):
        # list("compute_total") used to shred the string into
        # single characters, silently disabling the kept-symbol logic.
        b = _write_bundle(self.tmp / "b",
                          [_entry("a.py", symbols_included="compute_total")])
        with self.assertRaises(BundleError):
            load_bundle(b)

    def test_symbols_included_as_int_is_bundle_error(self):
        b = _write_bundle(self.tmp / "b", [_entry("a.py", symbols_included=42)])
        with self.assertRaises(BundleError):
            load_bundle(b)

    def test_manifest_path_traversal_is_bundle_error(self):
        b = _write_bundle(self.tmp / "b", [_entry("../outside.py")])
        with self.assertRaises(BundleError):
            load_bundle(b)

    def test_manifest_absolute_path_is_bundle_error(self):
        b = _write_bundle(self.tmp / "b", [_entry("/etc/passwd")])
        with self.assertRaises(BundleError):
            load_bundle(b)

    def test_duplicate_manifest_path_is_bundle_error(self):
        b = _write_bundle(self.tmp / "b", [_entry("a.py", mode="exclude"),
                                           _entry("a.py", mode="full")])
        with self.assertRaises(BundleError):
            load_bundle(b)

    def test_mode_matching_is_case_insensitive(self):
        dest = self.tmp / "b"
        _write_bundle(dest, [_entry("a.py", mode="Signatures"),
                             _entry("b.py", mode="EXCLUDE")])
        (dest / "files" / "a.py").write_text("def f():\n    ...\n")
        (dest / "files" / "b.py").write_text("x = 1\n")
        bundle = load_bundle(dest)
        self.assertEqual(bundle.status_of("a.py"), "stub")
        self.assertEqual(bundle.status_of("b.py"), "excluded")


class TestPatchContainment(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-harden-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.root = self.tmp / "work"
        self.root.mkdir()
        (self.root / "keep.txt").write_text("safe\n")

    def test_dotdot_path_cannot_escape_temp_copy(self):
        patches = parse_patch(
            "--- a/../escape.txt\n+++ b/../escape.txt\n"
            "@@ -0,0 +1 @@\n+pwned\n")
        result = apply_patch(self.root, patches)
        self.assertFalse(result["applied_all"])
        self.assertFalse((self.tmp / "escape.txt").exists())

    def test_absolute_path_cannot_escape_temp_copy(self):
        target = self.tmp / "abs_escape.txt"
        patches = parse_patch(
            f"--- a/x\n+++ b/{target}\n@@ -0,0 +1 @@\n+abs\n")
        result = apply_patch(self.root, patches)
        self.assertFalse(result["applied_all"])
        self.assertFalse(target.exists())

    def test_symlinked_parent_cannot_escape_temp_copy(self):
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.root / "link").symlink_to(outside, target_is_directory=True)
        patches = parse_patch(
            "--- a/link/evil.txt\n+++ b/link/evil.txt\n"
            "@@ -0,0 +1 @@\n+evil\n")
        result = apply_patch(self.root, patches)
        self.assertFalse(result["applied_all"])
        self.assertFalse((outside / "evil.txt").exists())

    def test_normal_path_still_applies(self):
        (self.root / "a.txt").write_text("one\ntwo\n")
        patches = parse_patch(
            "--- a/a.txt\n+++ b/a.txt\n@@ -1,2 +1,2 @@\n one\n-two\n+2\n")
        result = apply_patch(self.root, patches)
        self.assertTrue(result["applied_all"])
        self.assertEqual((self.root / "a.txt").read_text(), "one\n2\n")


class TestPatchParsing(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-harden-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))

    def test_removed_line_that_looks_like_a_header_stays_content(self):
        diff = ("--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,2 @@\n"
                " line1\n--- not a header, a removed line\n line3\n")
        patches = parse_patch(diff)
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0].path, "app.py")
        work = self.tmp / "w"
        work.mkdir()
        (work / "app.py").write_text(
            "line1\n-- not a header, a removed line\nline3\n")
        result = apply_patch(work, patches)
        self.assertTrue(result["applied_all"], result)
        self.assertEqual((work / "app.py").read_text(), "line1\nline3\n")

    def test_added_line_that_looks_like_a_header_stays_content(self):
        diff = ("--- a/app.py\n+++ b/app.py\n@@ -1,2 +1,3 @@\n"
                " line1\n+++ also not a header\n line2\n")
        patches = parse_patch(diff)
        self.assertEqual(len(patches), 1)
        self.assertEqual(patches[0].new_path, "app.py")
        self.assertEqual(patches[0].added_lines, ["++ also not a header"])

    def test_empty_context_line_is_kept(self):
        diff = ("--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,3 @@\n"
                " line1\n\n-line3\n+line3x\n")
        patches = parse_patch(diff)
        work = self.tmp / "w"
        work.mkdir()
        (work / "app.py").write_text("line1\n\nline3\n")
        result = apply_patch(work, patches)
        self.assertTrue(result["applied_all"], result)
        self.assertEqual((work / "app.py").read_text(), "line1\n\nline3x\n")


class TestKeptSetHonesty(unittest.TestCase):
    """symbols_included must not promote a symbol whose file
    shipped no bytes (the F2 false-pass)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-harden-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.project = self.tmp / "project"
        self.project.mkdir()
        (self.project / "app.py").write_text("def main():\n    return 1\n")
        (self.project / "pricing.py").write_text(
            "def compute_total(items):\n    return sum(items)\n")

    def _bundle(self, name: str, symbols) -> Path:
        dest = self.tmp / name
        entries = []
        for rel, mode in (("app.py", "full"), ("pricing.py", "exclude")):
            content = (self.project / rel).read_text() if mode != "exclude" else ""
            e = {"path": rel, "mode": mode, "language": "python",
                 "original_tokens": 10,
                 "sliced_tokens": 0 if mode == "exclude" else 10,
                 "sha256": hashlib.sha256(content.encode()).hexdigest()}
            if symbols and rel == "pricing.py":
                e["symbols_included"] = symbols
            entries.append(e)
            if mode != "exclude":
                (dest / "files").mkdir(parents=True, exist_ok=True)
                (dest / "files" / rel).write_text(content)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "manifest.json").write_text(json.dumps(
            {"preset": "claude-code", "total_original_tokens": 20,
             "total_sliced_tokens": 10, "files": entries}))
        (dest / "PROMPT.md").write_text("x")
        return dest

    def test_excluded_file_symbol_stays_a_gap_despite_symbols_included(self):
        bundle = self._bundle("promoted", ["compute_total"])
        report = run_pre(self.project, bundle, "use compute_total please")
        self.assertEqual(report["exit_code"], 1)
        self.assertIn("compute_total",
                      [g["symbol"] for g in report["gaps"]])

    def test_control_without_symbols_included_also_gaps(self):
        bundle = self._bundle("plain", None)
        report = run_pre(self.project, bundle, "use compute_total please")
        self.assertEqual(report["exit_code"], 1)


class TestPostSymbolVisibility(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-harden-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.project = self.tmp / "project"
        self.project.mkdir()
        (self.project / "a.py").write_text(
            "def helper():\n    return 1\n\ndef main():\n    return helper()\n")
        (self.project / "b.py").write_text("def helper():\n    return 2\n")
        (self.project / "main.py").write_text("from a import main\nprint(main())\n")

    def _bundle(self, name: str, modes: dict) -> Path:
        dest = self.tmp / name
        (dest / "files").mkdir(parents=True)
        entries = []
        for rel, mode in modes.items():
            content = (self.project / rel).read_text() if mode != "exclude" else ""
            entries.append(
                {"path": rel, "mode": mode, "language": "python",
                 "original_tokens": 10,
                 "sliced_tokens": 0 if mode == "exclude" else 10,
                 "sha256": hashlib.sha256(content.encode()).hexdigest()})
            if mode != "exclude":
                (dest / "files" / rel).write_text(content)
        (dest / "manifest.json").write_text(json.dumps(
            {"preset": "claude-code", "total_original_tokens": 30,
             "total_sliced_tokens": 20, "files": entries}))
        (dest / "PROMPT.md").write_text("x")
        return dest

    def _patch(self) -> Path:
        p = self.tmp / "p.diff"
        p.write_text("--- a/main.py\n+++ b/main.py\n@@ -1,2 +1,2 @@\n"
                     " from a import main\n-print(main())\n"
                     "+print(main() + helper())\n")
        return p

    def test_no_flag_when_one_definition_was_visible(self):
        bundle = self._bundle("mixed",
                              {"a.py": "full", "b.py": "exclude",
                               "main.py": "full"})
        report = run_post(self.project, bundle, self._patch())
        unseen = [f for f in report["post"]["findings"]
                  if f["type"] == "references_unseen_symbol"]
        self.assertEqual(unseen, [])

    def test_flag_when_every_definition_is_unseen(self):
        bundle = self._bundle("allunseen",
                              {"a.py": "exclude", "b.py": "exclude",
                               "main.py": "full"})
        report = run_post(self.project, bundle, self._patch())
        unseen = [f for f in report["post"]["findings"]
                  if f["type"] == "references_unseen_symbol"]
        self.assertTrue(unseen)


class TestPostConfigHardening(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-harden-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))
        self.project = helpers.copy_project(self.tmp / "project")
        self.bundle = helpers.make_bundle(self.tmp / "bundle")
        self.good = (Path(__file__).resolve().parent / "fixtures"
                     / "patches" / "good_patch.diff")

    def test_non_string_test_command_does_not_crash(self):
        (self.project / ".ghostverify.json").write_text(
            json.dumps({"test_command": 123}))
        report = run_post(self.project, self.bundle, self.good,
                          run_tests=True)
        self.assertIn("not a string", report["post"]["tests"]["status"])

    def test_negative_timeout_does_not_manufacture_a_timeout(self):
        (self.project / ".ghostverify.json").write_text(
            json.dumps({"test_command": "true"}))
        report = run_post(self.project, self.bundle, self.good,
                          run_tests=True, timeout=-1)
        self.assertEqual(report["post"]["tests"]["status"], "passed")


class TestFixFailureHandling(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-harden-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))

    def test_exporter_failure_is_could_not_verify_not_traceback(self):
        project = helpers.copy_project(self.tmp / "project")
        bundle = helpers.make_bundle(self.tmp / "bundle",
                                     modes={"pricing.py": "exclude"})

        class FakeExporter:
            @staticmethod
            def build_bundle(*a, **k):
                raise RuntimeError("disk exploded")

        class FakeProfile:
            @staticmethod
            def load_profile(_p):
                class P:
                    entry_points = []
                return P()

        orig = core.submodule

        def fake(name):
            return {"exporter": FakeExporter,
                    "profile": FakeProfile}.get(name, orig(name))

        with mock.patch.object(core, "submodule", fake):
            report = core.run_fix(project, bundle, self.tmp / "out",
                                  "compute_total")
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["verdict"], "could_not_verify")
        self.assertTrue(any("exporter failed" in n
                            for n in report["notes"]))


@unittest.skipUnless(helpers.cli_available(), "ghostverify CLI not discoverable")
class TestCliReportWriteFailure(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gv-harden-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.tmp, ignore_errors=True))

    def test_unwritable_out_dir_keeps_verdict_and_explains(self):
        project = helpers.copy_project(self.tmp / "project")
        bundle = helpers.make_bundle(self.tmp / "bundle")
        blocker = self.tmp / "afile"
        blocker.write_text("x")
        proc = helpers.run_cli(
            ["pre", str(project), str(bundle), "--out-dir", str(blocker)])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("could not write reports", proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)


class TestGsloadDiscovery(unittest.TestCase):
    def test_ancestor_candidates_include_the_historical_depth(self):
        from ghostverify import gsload
        cands = gsload._sibling_candidates()
        historical = (Path(gsload.__file__).resolve().parents[3]
                      / "ghostslice" / "src")
        self.assertIn(historical, cands)
        self.assertGreater(len(cands), 1)


class TestMcpProtocolErrors(unittest.TestCase):
    def _run(self, text: str) -> list:
        from ghostverify.mcp_server import run_stdio
        out = io.StringIO()
        run_stdio(io.StringIO(text), out)
        return [json.loads(l) for l in out.getvalue().splitlines() if l.strip()]

    def test_malformed_json_gets_a_parse_error_response(self):
        msgs = self._run("{not json\n")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["error"]["code"], -32700)
        self.assertIsNone(msgs[0]["id"])

    def test_non_object_json_gets_an_invalid_request_response(self):
        msgs = self._run("[1, 2]\n")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["error"]["code"], -32600)

    def test_valid_initialize_still_answers(self):
        msgs = self._run(json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {}}) + "\n")
        self.assertEqual(len(msgs), 1)
        self.assertIn("result", msgs[0])


if __name__ == "__main__":
    unittest.main()
