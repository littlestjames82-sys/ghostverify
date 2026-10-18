"""Optional GhostSlice integration.

GhostVerify is stdlib-only and works standalone, but wherever
GhostSlice is importable its own machinery is authoritative and is
used instead of a second copy: token estimator, slicer, exporter,
profile loader, lint and the secrets guard (the PLAN forbids
re-implementing the lint/secret rules). Discovery, in order:

  1. an already-importable ``ghostslice`` package (installed, or
     PYTHONPATH pointing at a checkout's ``src/``);
  2. the ``GHOSTSLICE_SRC`` environment variable (path to its src);
  3. a sibling checkout at ``<workspace>/ghostslice/src`` relative
     to this package (the Ghost Developer Studio workspace layout).

Anything that fails to load degrades to ``None`` and callers fall
back to the built-in stdlib implementations, recording that they
did so in their report. Nothing here ever touches the network.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_CACHE: dict = {"tried": False, "mod": None}


def ghostslice_module():
    """The ghostslice package module, or None when unavailable."""
    if _CACHE["tried"]:
        return _CACHE["mod"]
    _CACHE["tried"] = True
    mod = _try_import()
    if mod is None:
        candidates = []
        env = os.environ.get("GHOSTSLICE_SRC")
        if env:
            candidates.append(Path(env))
        # src/ghostverify/gsload.py -> parents: ghostverify, src,
        # ghostverify project root, workspace root.
        try:
            workspace = Path(__file__).resolve().parents[3]
            candidates.append(workspace / "ghostslice" / "src")
        except IndexError:
            pass
        for cand in candidates:
            if (cand / "ghostslice" / "__init__.py").is_file():
                sys.path.insert(0, str(cand))
                mod = _try_import()
                if mod is not None:
                    break
    _CACHE["mod"] = mod
    return mod


def _try_import():
    try:
        import ghostslice

        return ghostslice
    except Exception:
        return None


def submodule(name: str):
    """A ghostslice submodule (e.g. 'tokens'), or None."""
    if ghostslice_module() is None:
        return None
    try:
        import importlib

        return importlib.import_module(f"ghostslice.{name}")
    except Exception:
        return None


def estimate_tokens(text: str, language: str | None = None) -> int:
    """Token estimate: GhostSlice's calibrated estimator when
    available (so Verify totals agree with Slice totals), else a
    documented local fallback (raw BPE-like split, factor 1.0)."""
    tokens = submodule("tokens")
    if tokens is not None:
        try:
            return tokens.estimate_tokens(text, language)
        except Exception:
            pass
    import math
    import re

    total = 0
    for match in re.findall(r"[A-Za-z0-9_]+|[^\w\s]", text):
        total += 1 if len(match) <= 4 else int(math.ceil(len(match) / 4))
    return total


def token_method() -> str:
    tokens = submodule("tokens")
    if tokens is not None:
        return getattr(tokens, "METHOD", "ghostslice estimator")
    return "estimates (local fallback BPE-like split, factor 1.0, not exact tokenizer counts)"
