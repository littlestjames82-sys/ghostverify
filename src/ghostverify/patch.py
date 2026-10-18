"""Unified-diff parsing and application to a TEMPORARY copy.

Stdlib only. The applier is deliberately small: it understands
standard unified hunks (``@@ -a,b +c,d @@`` with context, ``+``,
``-`` and ``\\`` lines) and applies them with an exact-context
search around the stated line. Anything it cannot place is
reported as a failed hunk; it never guesses. The working tree a
patch names is never the target: callers pass a temp copy root.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Hunk:
    old_start: int
    lines: list = field(default_factory=list)  # (tag, text) tag in ' +-\\'


@dataclass
class FilePatch:
    old_path: str | None
    new_path: str | None
    hunks: list = field(default_factory=list)
    added_lines: list = field(default_factory=list)   # text without '+'
    is_new: bool = False
    is_delete: bool = False

    @property
    def path(self) -> str:
        return self.new_path or self.old_path or ""


def _clean_path(raw: str) -> str | None:
    raw = raw.strip().split("\t")[0].strip()
    if raw == "/dev/null":
        return None
    if raw.startswith(("a/", "b/")):
        raw = raw[2:]
    return raw


def parse_patch(text: str) -> list[FilePatch]:
    patches: list[FilePatch] = []
    current: FilePatch | None = None
    hunk: Hunk | None = None
    for line in text.splitlines():
        if line.startswith("--- "):
            current = FilePatch(old_path=_clean_path(line[4:]), new_path=None)
            patches.append(current)
            hunk = None
            continue
        if current is None:
            continue
        if line.startswith("+++ "):
            current.new_path = _clean_path(line[4:])
            current.is_new = current.old_path is None
            current.is_delete = current.new_path is None
            continue
        m = _HUNK_RE.match(line)
        if m:
            hunk = Hunk(old_start=int(m.group(1)))
            current.hunks.append(hunk)
            continue
        if hunk is not None and line[:1] in (" ", "+", "-", "\\"):
            hunk.lines.append((line[0], line[1:]))
            if line[0] == "+":
                current.added_lines.append(line[1:])
    return [p for p in patches if p.path]


def apply_patch(root: Path, patches: list[FilePatch]) -> dict:
    """Apply to `root` (a temp copy). Returns per-file results.
    Never raises on a bad hunk: failures are data."""
    results = []
    for fp in patches:
        rel = fp.path
        target = root / rel
        if fp.is_delete:
            try:
                target.unlink()
                results.append({"file": rel, "applied": True})
            except OSError as exc:
                results.append({"file": rel, "applied": False, "error": str(exc)})
            continue
        original = ""
        if target.is_file():
            original = target.read_text(encoding="utf-8", errors="replace")
        lines = original.splitlines()
        offset = 0
        errors: list[str] = []
        for h in fp.hunks:
            old_block = [t for tag, t in h.lines if tag in (" ", "-")]
            new_block = [t for tag, t in h.lines if tag in (" ", "+")]
            start = h.old_start - 1 + offset
            found = _find(lines, old_block, start)
            if found is None:
                errors.append(f"hunk at line {h.old_start} does not apply")
                continue
            lines[found:found + len(old_block)] = new_block
            offset += len(new_block) - len(old_block)
        if errors:
            results.append({"file": rel, "applied": False, "error": "; ".join(errors)})
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n".join(lines) + ("\n" if lines else ""),
                              encoding="utf-8")
            results.append({"file": rel, "applied": True})
        except OSError as exc:
            results.append({"file": rel, "applied": False, "error": str(exc)})
    return {"files": results,
            "applied_all": all(r["applied"] for r in results) if results else False}


def _find(lines: list[str], block: list[str], near: int) -> int | None:
    if not block:
        return max(0, min(near, len(lines)))
    for start in _search_order(near, len(lines) - len(block) + 1):
        if lines[start:start + len(block)] == block:
            return start
    return None


def _search_order(near: int, count: int):
    if count <= 0:
        return
    near = max(0, min(near, count - 1))
    yield near
    for step in range(1, count):
        if near + step < count:
            yield near + step
        if near - step >= 0:
            yield near - step
