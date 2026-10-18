"""Report writers: verify-report.json, VERIFY.md, the ledger.

Reports sit NEXT TO the bundle by default (the bundle's parent
directory), never inside the project source tree and never inside
the bundle itself, so a bundle stays byte-identical to what
GhostSlice wrote and can still be validated with
``ghostslice check``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

HONESTY = ("no gaps found by static check is evidence, not a "
           "guarantee an agent run will succeed")


def default_out_dir(bundle_path) -> Path:
    """Default report location: the bundle's parent directory
    (next to the bundle, never inside it)."""
    return Path(bundle_path).resolve().parent


def write_reports(report: dict, out_dir) -> tuple[Path, Path]:
    """Write ``verify-report.json`` and ``VERIFY.md`` into
    ``out_dir`` (created if needed); returns both paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / "verify-report.json"
    md_path = out / "VERIFY.md"
    json_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path


def render_markdown(report: dict) -> str:
    """Render a report dict as the human-readable ``VERIFY.md``."""
    lines = [
        "# GhostVerify report",
        "",
        f"- Verdict: **{report.get('verdict', 'unknown')}** "
        f"(exit {report.get('exit_code', '?')})",
        f"- Project: `{report.get('project', '')}`",
        f"- Bundle: `{report.get('bundle', '')}`",
        f"- Bundle manifest sha256: `{report.get('bundle_manifest_sha256', '')}`",
        f"- Task: {report.get('task') or '(none given)'}",
        f"- Token counts are estimates ({report.get('token_method', '')}).",
        f"- Note: {HONESTY}.",
        "",
    ]
    tokens = report.get("tokens") or {}
    if tokens:
        lines += ["## Tokens (estimated)", "",
                  f"- Bundle original: {tokens.get('bundle_original', 0)}",
                  f"- Bundle sliced: {tokens.get('bundle_sliced', 0)}",
                  f"- Whole project: {tokens.get('project_total', 0)}", ""]
    conf = report.get("confidence") or {}
    if conf:
        lines += ["## Reachability confidence by language", ""]
        for lang, level in sorted(conf.items()):
            lines.append(f"- {lang}: {level}")
        lines.append("")
    gaps = report.get("gaps") or []
    lines += ["## Hard gaps (MISSING-HARD)", ""]
    if gaps:
        for g in gaps:
            lines.append(f"- `{g.get('symbol', '')}` in `{g.get('file', '')}`: "
                         f"{g.get('reason', '')} (via {g.get('via', '')})")
    else:
        lines.append("- None found by static check.")
    lines.append("")
    soft = report.get("soft_gaps") or []
    lines += ["## Soft gaps (MISSING-SOFT, heuristic)", ""]
    if soft:
        for g in soft:
            lines.append(f"- `{g.get('symbol', '')}` in `{g.get('file', '')}`: "
                         f"{g.get('reason', '')}")
    else:
        lines.append("- None.")
    lines += ["", f"OK-STUB symbols (signatures kept, no body needed): "
              f"{report.get('ok_stub_count', 0)}", ""]
    if report.get("fixture_references") is not None:
        lines += [f"Test-fixture references followed (parameter "
                  f"injection + usefixtures): "
                  f"{report['fixture_references']}", ""]
    ungrounded = report.get("ungrounded_requirements") or []
    if ungrounded:
        lines += ["## Ungrounded requirements (not gaps)", "",
                  "Requirements the task derives that Verify cannot "
                  "ground in a real symbol. They are stated in plain "
                  "words, are not gaps, and do not affect the verdict.",
                  ""]
        for u in ungrounded:
            lines.append(f"- {u.get('detail', '')}")
        lines.append("")
    carry = report.get("carryover") or {}
    if carry:
        lines += ["## GhostSlice carry-over (lint + secrets, bundle files only)", ""]
        if carry.get("unavailable"):
            lines.append(f"- Unavailable: {carry['unavailable']}")
        for f in carry.get("secrets", []):
            lines.append(f"- secret: `{f.get('path')}` line {f.get('line')} "
                         f"type {f.get('type')} (value redacted)")
        for f in carry.get("lint", []):
            lines.append(f"- lint {f.get('rule')}: `{f.get('path')}` "
                         f"{f.get('message', '')}")
        if not carry.get("secrets") and not carry.get("lint") \
                and not carry.get("unavailable"):
            lines.append("- None.")
        lines.append("")
    rounds = report.get("expansion_rounds") or []
    if rounds:
        lines += ["## Expansion rounds", ""]
        for r in rounds:
            lines.append(f"- Round {r.get('round')}: added/upgraded "
                         f"{', '.join(r.get('changed', [])) or 'nothing'}; "
                         f"bundle sliced tokens {r.get('sliced_tokens')}; "
                         f"hard gaps {r.get('hard_gaps')}")
        if report.get("expanded_bundle"):
            lines.append(f"- Expanded bundle: `{report['expanded_bundle']}`")
        else:
            lines.append("- Expanded bundle: none written (no "
                         "upgradeable hard gaps)")
        lines.append("")
    post = report.get("post")
    if post:
        lines += ["## Post-run patch check", "",
                  f"- Patch verdict: **{post.get('verdict')}**",
                  f"- Applied to a temporary copy only: {post.get('applied_temp_copy')}",
                  f"- Tests: {post.get('tests', {}).get('status', 'not run')}",
                  f"- Diagnosis: {post.get('diagnosis', '')}"]
        for f in post.get("findings", []):
            lines.append(f"- {f.get('type')}: `{f.get('file', '')}` "
                         f"{f.get('detail', '')}")
        lines.append("")
    for note in report.get("notes", []):
        lines.append(f"- Note: {note}")
    return "\n".join(lines) + "\n"


def append_ledger(project_root, report: dict) -> Path | None:
    """Append one verdict line to <project>/.ghostverify-history.jsonl.

    The Slice savings ledger is never touched; this is a sibling.
    Best-effort: a ledger write failure never changes a verdict.
    """
    try:
        path = Path(project_root) / ".ghostverify-history.jsonl"
        record = {
            "at": datetime.now(timezone.utc).isoformat(),
            "tool": "ghostverify",
            "task": report.get("task"),
            "bundle": report.get("bundle"),
            "bundle_manifest_sha256": report.get("bundle_manifest_sha256"),
            "verdict": report.get("verdict"),
            "hard_gaps": len(report.get("gaps") or []),
            "soft_gaps": len(report.get("soft_gaps") or []),
            "tokens_before": (report.get("tokens") or {}).get("bundle_sliced"),
            "tokens_after": (report.get("tokens") or {}).get("expanded_sliced"),
            "patch_result": (report.get("post") or {}).get("verdict"),
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\n")
        return path
    except OSError:
        return None
