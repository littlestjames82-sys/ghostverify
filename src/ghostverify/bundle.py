"""GhostSlice bundle reader and the kept-set.

A bundle is the on-disk artifact GhostSlice writes: ``manifest.json``
(per-file path/mode/language/original_tokens/sliced_tokens/sha256,
plus totals), ``PROMPT.md`` and ``files/<rel>`` holding the sliced
text. Verify never trusts the manifest alone: a file whose sliced
bytes are absent under ``files/`` is not kept, whatever its mode
says, and a manifest that does not parse means the bundle cannot be
verified (callers map that to exit 2, never to a pass).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

# Modes that keep a body an agent can actually work from.
BODY_MODES = {"full", "strip-tests", "symbols"}
# Modes that keep a surface but no bodies: symbols in these files
# are stubs (OK-STUB when nothing needs the body, MISSING-SOFT when
# a kept caller needs it).
STUB_MODES = {"signatures", "interfaces", "structure", "headings"}


class BundleError(ValueError):
    """The bundle cannot be read well enough to verify anything."""


@dataclass
class BundleFile:
    path: str
    mode: str
    language: str = "text"
    original_tokens: int = 0
    sliced_tokens: int = 0
    sha256: str = ""
    symbols_included: list = field(default_factory=list)
    on_disk: bool = False  # sliced bytes present under files/

    @property
    def kept(self) -> bool:
        return self.mode != "exclude" and self.on_disk

    @property
    def body_kept(self) -> bool:
        return self.kept and self.mode in BODY_MODES

    @property
    def stub(self) -> bool:
        return self.kept and self.mode in STUB_MODES


@dataclass
class Bundle:
    path: Path
    manifest: dict
    manifest_sha256: str
    files: dict  # path -> BundleFile
    preset: str = "claude-code"

    @property
    def total_original_tokens(self) -> int:
        return int(self.manifest.get("total_original_tokens") or 0)

    @property
    def total_sliced_tokens(self) -> int:
        return int(self.manifest.get("total_sliced_tokens") or 0)

    def status_of(self, rel: str) -> str:
        """'body' | 'stub' | 'excluded' | 'missing' for a project path."""
        entry = self.files.get(rel)
        if entry is None:
            return "missing"
        if entry.body_kept:
            return "body"
        if entry.stub:
            return "stub"
        if entry.kept:
            # An unrecognised mode with bytes on disk: treat the
            # bytes as kept content rather than inventing a verdict.
            return "body"
        return "excluded"

    def sliced_text(self, rel: str) -> str:
        f = self.path / "files" / rel
        try:
            return f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""


def load_bundle(bundle_path) -> Bundle:
    bundle = Path(bundle_path)
    if not bundle.is_dir():
        raise BundleError(f"bundle is not a directory: {bundle}")
    mpath = bundle / "manifest.json"
    try:
        raw = mpath.read_bytes()
    except OSError as exc:
        raise BundleError(f"manifest.json: cannot read ({exc})") from exc
    manifest_sha = hashlib.sha256(raw).hexdigest()
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BundleError(f"manifest.json: invalid ({exc})") from exc
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list):
        raise BundleError("manifest.json: top level must be an object with a 'files' list")
    files: dict[str, BundleFile] = {}
    for i, entry in enumerate(manifest["files"]):
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise BundleError(f"manifest files[{i}]: not an object with a string 'path'")
        rel = entry["path"]
        on_disk = (bundle / "files" / rel).is_file()
        files[rel] = BundleFile(
            path=rel,
            mode=str(entry.get("mode") or "full"),
            language=str(entry.get("language") or "text"),
            original_tokens=int(entry.get("original_tokens") or 0),
            sliced_tokens=int(entry.get("sliced_tokens") or 0),
            sha256=str(entry.get("sha256") or ""),
            symbols_included=list(entry.get("symbols_included") or []),
            on_disk=on_disk,
        )
    return Bundle(path=bundle, manifest=manifest,
                  manifest_sha256=manifest_sha, files=files,
                  preset=str(manifest.get("preset") or "claude-code"))
