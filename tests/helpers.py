"""Shared helpers for the GhostVerify test suite.

The suite is black-box: it drives the PLAN-specified CLI

    ghostverify pre  <project> <bundle> --task "..."
    ghostverify fix  <project> <bundle> -o <bundle-v2>
    ghostverify post <project> <bundle> --patch <file.diff>

and never imports GhostVerify internals, so it stays valid whatever
module layout the core build uses. CLI discovery order:

1. ``GHOSTVERIFY_CMD`` env var (a shell-style command prefix).
2. A ``ghostverify`` executable on PATH (installed console script).
3. ``python -m ghostverify`` with ``PYTHONPATH`` set to ``src/`` and
   then to the repo root (covers a ``src/`` layout and a flat layout).

If none of those responds to ``--help``, CLI tests skip with an
explicit reason instead of failing: the fixtures tests still run and
still validate the planted-gap / clean-bundle / patch fixtures
against the bundle format GhostVerify consumes.

Assumptions are recorded in REPORT_B.md.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
MINI_PROJECT = FIXTURES / "mini_project"
PATCHES = FIXTURES / "patches"

_CLI_PREFIX: list[str] | None = None
_CLI_ENV: dict | None = None
_CLI_PROBED = False


def _probe(prefix: list[str], env_extra: dict | None = None) -> bool:
    env = dict(os.environ)
    if env_extra:
        env.update(env_extra)
    try:
        proc = subprocess.run(
            prefix + ["--help"],
            capture_output=True, text=True, timeout=20, env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and "ghostverify" in (
        proc.stdout + proc.stderr).lower()


def cli_prefix() -> list[str] | None:
    """Return the command prefix for the ghostverify CLI, or None."""
    global _CLI_PREFIX, _CLI_ENV, _CLI_PROBED
    if _CLI_PROBED:
        return _CLI_PREFIX
    _CLI_PROBED = True

    env_cmd = os.environ.get("GHOSTVERIFY_CMD")
    if env_cmd and _probe(shlex.split(env_cmd)):
        _CLI_PREFIX = shlex.split(env_cmd)
        _CLI_ENV = {}
        return _CLI_PREFIX

    exe = shutil.which("ghostverify")
    if exe and _probe([exe]):
        _CLI_PREFIX = [exe]
        _CLI_ENV = {}
        return _CLI_PREFIX

    for base in (REPO_ROOT / "src", REPO_ROOT):
        if (base / "ghostverify").is_dir() or (base / "ghostverify.py").exists():
            env = {"PYTHONPATH": str(base)}
            if _probe([sys.executable, "-m", "ghostverify"], env):
                _CLI_PREFIX = [sys.executable, "-m", "ghostverify"]
                _CLI_ENV = env
                return _CLI_PREFIX
    return None


def cli_available() -> bool:
    return cli_prefix() is not None


def cli_env() -> dict:
    """Environment a CLI subprocess needs (PYTHONPATH for src runs)."""
    cli_prefix()
    env = dict(os.environ)
    if _CLI_ENV:
        env.update(_CLI_ENV)
    return env


def run_cli(args: list[str], cwd: Path | None = None,
            timeout: int = 60) -> subprocess.CompletedProcess:
    prefix = cli_prefix()
    assert prefix is not None, "ghostverify CLI not available"
    env = dict(os.environ)
    if _CLI_ENV:
        env.update(_CLI_ENV)
    return subprocess.run(
        prefix + args, capture_output=True, text=True,
        timeout=timeout, cwd=str(cwd) if cwd else None, env=env,
    )


def est_tokens(text: str) -> int:
    """Rough fixture token count (fixtures only, not a product claim)."""
    return max(1, len(text) // 4)


SIGNATURES_CONTENT = {
    # What a GhostSlice `signatures` slice of pricing.py looks like:
    # constant + signature + first docstring line, no body.
    "pricing.py": (
        '"""Pricing rules for the mini fixture project."""\n\n'
        "TAX_RATE = 0.08\n\n\n"
        "def compute_total(items):\n"
        '    """Sum item prices and apply the tax rate."""\n'
        "    ...\n"
    ),
}


def make_bundle(dest: Path, project: Path = MINI_PROJECT,
                modes: dict[str, str] | None = None,
                omit: tuple[str, ...] = ()) -> Path:
    """Build a GhostSlice-shaped bundle for the fixture project.

    Files land in ``dest/files/<rel>`` and ``dest/manifest.json``
    carries the same per-file fields GhostSlice v1.0.x writes
    (path, mode, language, package, original_tokens, sliced_tokens,
    sha256) plus bundle totals. ``modes`` overrides the per-file
    mode (default ``full``); a file whose mode is ``exclude`` gets a
    manifest entry but no file in the bundle. ``omit`` drops a file
    from the manifest entirely (the absent-file gap variant).
    """
    modes = modes or {}
    dest = Path(dest)
    files_dir = dest / "files"
    files_dir.mkdir(parents=True, exist_ok=True)

    manifest_files = []
    total_original = 0
    total_sliced = 0
    prompt_parts = []
    for src in sorted(Path(project).rglob("*.py")):
        rel = src.relative_to(project).as_posix()
        if rel in omit:
            continue
        original = src.read_text(encoding="utf-8")
        mode = modes.get(rel, "full")
        if mode == "exclude":
            content = ""
        elif mode == "signatures" and rel in SIGNATURES_CONTENT:
            content = SIGNATURES_CONTENT[rel]
        else:
            content = original
        entry = {
            "path": rel,
            "mode": mode,
            "language": "python",
            "package": None,
            "original_tokens": est_tokens(original),
            "sliced_tokens": 0 if mode == "exclude" else est_tokens(content),
            "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        }
        manifest_files.append(entry)
        total_original += entry["original_tokens"]
        total_sliced += entry["sliced_tokens"]
        if mode != "exclude" and content:
            out = files_dir / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(content.encode("utf-8"))
            prompt_parts.append(f"\n\n---\n\n# File: {rel} (mode: {mode})\n\n"
                                f"{content.rstrip()}\n")

    manifest = {
        "tool": "ghostslice",
        "version": "fixture",
        "preset": "claude-code",
        "generated_at": "2026-10-14T00:00:00+00:00",
        "root": str(Path(project).resolve()),
        "token_method": "fixture estimate (chars/4), tests only",
        "total_original_tokens": total_original,
        "total_sliced_tokens": total_sliced,
        "total_saved_tokens": total_original - total_sliced,
        "files": manifest_files,
    }
    (dest / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8")
    (dest / "PROMPT.md").write_text(
        "# Fixture bundle (GhostVerify tests)\n" + "".join(prompt_parts),
        encoding="utf-8")
    return dest


def copy_project(dest: Path) -> Path:
    dest = Path(dest)
    shutil.copytree(MINI_PROJECT, dest,
                    ignore=shutil.ignore_patterns("__pycache__"))
    return dest


def find_report(bundle: Path) -> tuple[Path | None, Path | None]:
    """Locate (verify-report.json, VERIFY.md) for a bundle.

    The plan says reports are written "next to the bundle". That is
    read leniently: inside the bundle dir or in its parent.
    """
    report = md = None
    for base in (Path(bundle), Path(bundle).parent):
        cand_r = base / "verify-report.json"
        cand_m = base / "VERIFY.md"
        if report is None and cand_r.exists():
            report = cand_r
        if md is None and cand_m.exists():
            md = cand_m
    return report, md


def load_report(bundle: Path) -> dict | None:
    report, _ = find_report(bundle)
    if report is None:
        return None
    return json.loads(report.read_text(encoding="utf-8"))


def report_text(bundle: Path) -> str:
    """All human/machine report text + nothing else, for assertions."""
    report, md = find_report(bundle)
    out = []
    if report is not None:
        out.append(report.read_text(encoding="utf-8"))
    if md is not None:
        out.append(md.read_text(encoding="utf-8"))
    return "\n".join(out)


def gaps_of(report: dict) -> list:
    """Pull the gap list out of a verify-report.json, leniently."""
    for key in ("gaps", "findings", "missing", "issues"):
        value = report.get(key)
        if isinstance(value, list):
            return value
    nested = report.get("pre") or report.get("result") or {}
    if isinstance(nested, dict):
        return gaps_of(nested)
    return []


def tree_hashes(root: Path) -> dict[str, str]:
    """sha256 per file under root, for working-tree safety checks."""
    out = {}
    for path in sorted(Path(root).rglob("*")):
        if path.is_file():
            out[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    return out


def python_calls(path: Path) -> set[str]:
    """Names called anywhere in a Python file (fixture analysis)."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    return {node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}


def python_defs(path: Path) -> set[str]:
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    return {node.name for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
