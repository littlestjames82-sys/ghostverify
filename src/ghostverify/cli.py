"""GhostVerify CLI.

  ghostverify pre  <project> <bundle> [--task ... | --task-file f]
  ghostverify fix  <project> <bundle> -o <bundle-v2> [--task ...]
  ghostverify post <project> <bundle> --patch <file.diff>
                   [--run-tests] [--test-command ...] [--timeout 120]
  ghostverify mcp

Exit codes: 0 sufficient / clean, 1 hard gaps / patch flagged or
failed, 2 could not verify. Reports (verify-report.json + VERIFY.md)
are written next to the bundle unless --out-dir / --no-report says
otherwise, and one line is appended to the project's
.ghostverify-history.jsonl unless --no-ledger.
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
        write_reports(report, out_dir)
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
    p.add_argument("--task", default="", help="task text the bundle is for")
    p.add_argument("--task-file", help="read the task text from this file")
    p.add_argument("--out-dir", help="where to write verify-report.json + VERIFY.md "
                                     "(default: next to the bundle)")
    p.add_argument("--no-report", action="store_true")
    p.add_argument("--no-ledger", action="store_true")
    p.add_argument("--json", action="store_true", help="print the full report as JSON")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ghostverify",
                                     description="Self-healing slice checker for GhostSlice")
    parser.add_argument("--version", action="version",
                        version=f"ghostverify {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("pre", help="pre-flight sufficiency check")
    p.add_argument("project")
    p.add_argument("bundle")
    _add_common(p)

    p = sub.add_parser("fix", help="auto-expand a bundle via GhostSlice re-export")
    p.add_argument("project")
    p.add_argument("bundle")
    p.add_argument("-o", "--output", required=True, help="expanded bundle directory")
    _add_common(p)

    p = sub.add_parser("post", help="post-run patch check (temp copy only)")
    p.add_argument("project")
    p.add_argument("bundle")
    p.add_argument("--patch", required=True)
    p.add_argument("--run-tests", action="store_true",
                   help="opt-in: run the configured test command in the temp copy")
    p.add_argument("--test-command", help="test command (overrides .ghostverify.json)")
    p.add_argument("--timeout", type=int, default=120)
    _add_common(p)

    sub.add_parser("mcp", help="run the stdio MCP server")

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
