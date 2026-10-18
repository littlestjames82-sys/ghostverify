"""GhostVerify command-line interface.

Usage:
  ghostverify pre  <project> <bundle> [--task ... | --task-file f]
  ghostverify fix  <project> <bundle> -o <bundle-v2> [--task ...]
  ghostverify post <project> <bundle> --patch <file.diff>
                   [--run-tests] [--test-command ...] [--timeout 120]
  ghostverify mcp

``<project>`` is the source tree the bundle was sliced from and
``<bundle>`` is the GhostSlice bundle directory (the folder that
contains ``manifest.json``).

Exit codes (all commands): 0 = sufficient / clean, 1 = hard gaps
found / patch flagged or tests failed, 2 = could not verify. A
bundle that could not be verified never exits 0.

Reports (``verify-report.json`` + ``VERIFY.md``) are written next
to the bundle unless ``--out-dir`` / ``--no-report`` says
otherwise, and one line is appended to the project's
``.ghostverify-history.jsonl`` unless ``--no-ledger`` is given.

Run ``ghostverify <command> --help`` for per-command examples.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .core import run_fix, run_post, run_pre
from .reports import append_ledger, default_out_dir, write_reports


def _task_from(args) -> str:
    if getattr(args, "task_file", None):
        return Path(args.task_file).read_text(encoding="utf-8", errors="replace")
    return getattr(args, "task", "") or ""


def _emit(args, report: dict, bundle_path: str) -> int:
    if not getattr(args, "no_report", False):
        out_dir = getattr(args, "out_dir", None) or default_out_dir(bundle_path)
        try:
            write_reports(report, out_dir)
        except OSError as exc:
            # The verdict was computed; an unwritable report
            # destination (a file in the way, a read-only directory)
            # must not bury it under a traceback or change its exit
            # code. Say so on stderr and still print the summary.
            print(f"error: could not write reports to {out_dir}: {exc}",
                  file=sys.stderr)
    if not getattr(args, "no_ledger", False) and report.get("project"):
        append_ledger(report["project"], report)
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
    else:
        _print_summary(report)
    return int(report.get("exit_code", 2))


def _print_summary(report: dict) -> None:
    print(f"ghostverify {report.get('kind', 'pre')}: "
          f"{report.get('verdict', 'unknown')} (exit {report.get('exit_code', '?')})")
    for g in report.get("gaps", []):
        print(f"  MISSING-HARD {g.get('symbol')} in {g.get('file')}: {g.get('reason')}")
    for g in report.get("soft_gaps", [])[:10]:
        print(f"  MISSING-SOFT {g.get('symbol')} in {g.get('file')}: {g.get('reason')}")
    if len(report.get("soft_gaps", [])) > 10:
        print(f"  ... and {len(report['soft_gaps']) - 10} more soft gaps (see VERIFY.md)")
    for u in report.get("ungrounded_requirements", []):
        print(f"  UNGROUNDED {u.get('term')}: {u.get('detail')}")
    for r in report.get("expansion_rounds", []):
        print(f"  round {r.get('round')}: {', '.join(r.get('changed', [])) or 'no change'}"
              f" -> sliced {r.get('sliced_tokens')} tokens, hard gaps {r.get('hard_gaps')}")
    post = report.get("post")
    if post:
        print(f"  patch: {post.get('verdict')}; tests: "
              f"{post.get('tests', {}).get('status')}; {post.get('diagnosis')}")
        for f in post.get("findings", []):
            print(f"  {f.get('type')}: {f.get('file')} {f.get('detail')}")
    for note in report.get("notes", []):
        print(f"  note: {note}")


def _add_common(p) -> None:
    p.add_argument("--task", default="",
                   help="task text the bundle is for; symbols or files it "
                        "names are checked against the bundle")
    p.add_argument("--task-file",
                   help="read the task text from this file instead of --task")
    p.add_argument("--out-dir", help="where to write verify-report.json + VERIFY.md "
                                     "(default: next to the bundle)")
    p.add_argument("--no-report", action="store_true",
                   help="do not write verify-report.json or VERIFY.md")
    p.add_argument("--no-ledger", action="store_true",
                   help="do not append a verdict line to the project's "
                        ".ghostverify-history.jsonl")
    p.add_argument("--json", action="store_true",
                   help="print the full report as JSON instead of the "
                        "short human summary")


_EXIT_CODES = (
    "exit codes:\n"
    "  0  sufficient (pre), expanded bundle passes (fix), patch clean (post)\n"
    "  1  hard gaps found, expansion did not pass, or patch flagged/failed\n"
    "  2  could not verify (missing/corrupt bundle, unparseable patch,\n"
    "     or no graph could be built); never a pass"
)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ghostverify",
        description="Self-healing slice checker for GhostSlice: check a "
                    "context bundle was enough before an agent run (pre), "
                    "expand it if it was not (fix), and check the patch "
                    "that comes back (post). A pass means 'no gaps found "
                    "by static check', not a guarantee.",
        epilog="Run 'ghostverify <command> --help' for per-command help "
               "and examples.")
    parser.add_argument("--version", action="version",
                        version=f"ghostverify {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser(
        "pre", help="pre-flight sufficiency check (before the agent run)",
        description="Check that a GhostSlice bundle is sufficient for a "
                    "task: every symbol a kept file calls or imports, and "
                    "every symbol or file the task names, is present in "
                    "the bundle. Static only; nothing is run.",
        epilog="example:\n"
               "  ghostverify pre ~/my-project /tmp/my-context \\\n"
               "    --task \"Fix checkout totals using compute_total\"\n\n"
               + _EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("project", help="path to the project source tree")
    p.add_argument("bundle", help="path to the GhostSlice bundle directory "
                                  "(contains manifest.json)")
    _add_common(p)

    p = sub.add_parser(
        "fix", help="auto-expand a bundle via GhostSlice re-export",
        description="Expand a bundle that failed 'pre': add the missing "
                    "items at the lowest sufficient mode and re-check, "
                    "for up to 3 rounds. Writes a NEW bundle to -o; the "
                    "source bundle is never edited. Requires GhostSlice "
                    "(there is no fallback exporter); without it, fix "
                    "exits 2.",
        epilog="example:\n"
               "  ghostverify fix ~/my-project /tmp/my-context \\\n"
               "    -o /tmp/my-context-v2 --task \"Fix checkout totals\"\n\n"
               + _EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("project", help="path to the project source tree")
    p.add_argument("bundle", help="path to the GhostSlice bundle directory "
                                  "(contains manifest.json)")
    p.add_argument("-o", "--output", required=True,
                   help="directory for the expanded bundle (new bundle; "
                        "the source bundle is left untouched)")
    _add_common(p)

    p = sub.add_parser(
        "post", help="post-run patch check (temp copy only)",
        description="Check a patch returned by an agent against the "
                    "bundle: files touched, symbols referenced, and "
                    "test-file edits are flagged. The patch is applied "
                    "only to a temporary copy; the working tree is "
                    "never touched. Static by default: tests run only "
                    "with --run-tests and a configured test command.",
        epilog="example:\n"
               "  ghostverify post ~/my-project /tmp/my-context-v2 \\\n"
               "    --patch agent.diff\n\n"
               + _EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("project", help="path to the project source tree")
    p.add_argument("bundle", help="path to the GhostSlice bundle directory "
                                  "(contains manifest.json)")
    p.add_argument("--patch", required=True,
                   help="path to the unified-diff patch file to check")
    p.add_argument("--run-tests", action="store_true",
                   help="opt-in: run the configured test command in the temp copy")
    p.add_argument("--test-command",
                   help="test command to run in the temp copy (overrides "
                        "the test_command in the project's .ghostverify.json)")
    p.add_argument("--timeout", type=int, default=120,
                   help="timeout in seconds for the test command "
                        "(default: 120)")
    _add_common(p)

    sub.add_parser(
        "mcp", help="run the stdio MCP server",
        description="Run the local stdio MCP server (no network). Tools: "
                    "verify_bundle, expand_bundle, check_patch. Summaries "
                    "are capped and no raw file bodies are returned.")

    args = parser.parse_args(argv)
    if args.command == "mcp":
        from .mcp_server import run_stdio

        run_stdio()
        return 0
    try:
        task = _task_from(args)
    except OSError as exc:
        print(f"error: cannot read task file: {exc}", file=sys.stderr)
        return 2
    if args.command == "pre":
        report = run_pre(args.project, args.bundle, task)
        return _emit(args, report, args.bundle)
    if args.command == "fix":
        report = run_fix(args.project, args.bundle, args.output, task)
        return _emit(args, report, args.bundle)
    if args.command == "post":
        report = run_post(args.project, args.bundle, args.patch, task,
                          run_tests=args.run_tests,
                          test_command=args.test_command,
                          timeout=args.timeout)
        return _emit(args, report, args.bundle)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
