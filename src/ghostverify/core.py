"""GhostVerify core: pre (sufficiency), fix (auto-expand), post.

Exit-code contract (PLAN):
  0  sufficient / patch clean
  1  hard gaps found / patch flagged or tests failed
  2  could not verify (bad bundle, bad project, parse failure)

A verdict of 0 is only ever returned on positive evidence: a bundle
that could not be analysed is a 2, never a quiet pass.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .bundle import BundleError, load_bundle
from .gsload import ghostslice_module, submodule, token_method
from .patch import apply_patch, parse_patch
from .project import is_test_path, scan_project

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _task_terms(task: str) -> set[str]:
    """Split task text into checkable terms: whole words, the
    snake_case / camelCase parts of each word (lowercased), and the
    file name of any path-like mention such as ``pricing.py``."""
    terms: set[str] = set()
    for tok in _WORD_RE.findall(task or ""):
        terms.add(tok.lower())
        for part in re.findall(r"[a-z]+|[A-Z]+(?![a-z])|[A-Z][a-z]+",
                               tok.replace("_", " ")):
            terms.add(part.lower())
    # path-like mentions keep their full file name too
    for tok in re.findall(r"[\w./-]+\.\w+", task or ""):
        terms.add(Path(tok).name.lower())
    return terms


def _carryover(project_root: Path, bundle) -> dict:
    """GhostSlice lint + secrets findings, filtered to the files
    the bundle actually contains. Uses GhostSlice's own engines
    (never re-implemented); unavailable when GhostSlice is absent."""
    if ghostslice_module() is None:
        return {"lint": [], "secrets": [],
                "unavailable": "ghostslice not importable; lint/secrets "
                               "carry-over skipped"}
    kept = {p for p, f in bundle.files.items() if f.kept}
    out: dict = {"lint": [], "secrets": []}
    try:
        secrets_mod = submodule("secrets_scan")
        findings = secrets_mod.scan_path(str(project_root))
        out["secrets"] = [f for f in findings if f.get("path") in kept][:50]
    except Exception as exc:
        out["secrets_error"] = str(exc)
    try:
        lint_mod = submodule("lint")
        payload = lint_mod.lint_project(str(project_root))
        out["lint"] = [
            {"rule": f.get("rule"), "path": f.get("path"),
             "severity": f.get("severity"), "message": f.get("message")}
            for f in payload.get("findings", []) if f.get("path") in kept
        ][:50]
    except Exception as exc:
        out["lint_error"] = str(exc)
    return out


# ------------------------------------------------------- evidence contract
# Shared evidence contract fields (see the ecosystem
# EVIDENCE_CONTRACT_DRAFT): every verify-report.json states which
# tool version produced it, when (UTC), and how strong the evidence
# is. GhostVerify's honest level is "static-analysis": it compares a
# bundle against the project statically and executes no project code
# and no agent. The single exception is `post --run-tests`, which
# runs the project's own configured test command against the patched
# temporary copy; only when those tests actually ran does the report
# state "tests-executed" (see run_post).

LEVEL_STATIC = "static-analysis"
LEVEL_TESTS = "tests-executed"
LEVEL_BASIS_STATIC = (
    "static-analysis: GhostVerify compares the bundle against the "
    "project statically; it executes no project code and runs no "
    "agent, so no stronger level is claimed")
LEVEL_BASIS_TESTS = (
    "tests-executed: post ran the project's own configured test "
    "command against the patched temporary copy; the bundle and "
    "patch checks themselves remain static analysis, and no agent "
    "ran")


def _utc_now() -> str:
    """ISO 8601 UTC creation time for a report."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _contract_fields() -> dict:
    """Evidence-contract fields shared by every report."""
    return {
        "tool_version": __version__,
        "verification_level": LEVEL_STATIC,
        "verification_level_basis": LEVEL_BASIS_STATIC,
        "timestamp": _utc_now(),
    }


def _base_report(project_root, bundle, task) -> dict:
    """Report fields shared by every command, before verdicts are
    filled in. ``bundle_manifest_sha256`` makes a verdict
    reproducible from the report plus the manifest alone."""
    return {
        "tool": "ghostverify",
        "version": __version__,
        **_contract_fields(),
        "kind": "pre",
        "project": str(Path(project_root).resolve()),
        "bundle": str(bundle.path.resolve()),
        "bundle_manifest_sha256": bundle.manifest_sha256,
        "preset": bundle.preset,
        "task": task or "",
        "token_method": token_method(),
        "ghostslice_available": ghostslice_module() is not None,
        "notes": [],
    }


def run_pre(project_root, bundle_path, task: str = "") -> dict:
    """Pre-flight sufficiency check. Returns the report dict with
    'exit_code' and 'verdict' filled in.

    The report's ``gaps`` are MISSING-HARD findings (exit 1),
    ``soft_gaps`` are MISSING-SOFT heuristics that do not fail the
    verdict on their own, and ``ungrounded_requirements`` are
    task-derived terms that match no real symbol (reported in plain
    words, never counted as gaps). A bundle or project that cannot
    be analysed yields verdict ``could_not_verify``, exit 2."""
    project_root = Path(project_root)
    try:
        bundle = load_bundle(bundle_path)
    except BundleError as exc:
        return {"tool": "ghostverify", "version": __version__,
                **_contract_fields(), "kind": "pre",
                "verdict": "could_not_verify", "exit_code": 2,
                "task": task or "", "gaps": [], "soft_gaps": [],
                "ungrounded_requirements": [],
                "notes": [f"bundle unreadable: {exc}"]}
    report = _base_report(project_root, bundle, task)
    try:
        proj = scan_project(project_root)
    except (FileNotFoundError, OSError) as exc:
        report.update(verdict="could_not_verify", exit_code=2,
                      gaps=[], soft_gaps=[], ungrounded_requirements=[],
                      notes=[f"project unusable: {exc}"])
        return report
    if not proj.files or not proj.symbols:
        report.update(verdict="could_not_verify", exit_code=2, gaps=[],
                      soft_gaps=[], ungrounded_requirements=[],
                      notes=["no analysable files or symbols in the "
                             "project; refusing to pass an unverified "
                             "bundle"])
        return report
    if proj.parse_failures:
        report["notes"].append(
            "python files that failed to parse (analysed parser-lite): "
            + ", ".join(sorted(proj.parse_failures)))

    # ---- kept set -------------------------------------------------
    # state per project file: 'body' | 'stub' | 'excluded' | 'missing'
    state = {rel: bundle.status_of(rel) for rel in proj.files}
    # Symbols explicitly listed by a symbols-mode manifest entry are
    # body-kept even in an otherwise stub file -- but only when the
    # entry actually shipped bytes. Promoting a symbol from an
    # excluded (or on-disk-absent) entry on the manifest's say-so
    # alone would let a bundle claim its way to "sufficient" for
    # content the agent never received (bundle.py's own rule: a
    # file whose sliced bytes are absent is not kept).
    symbol_state: dict[str, str] = {}
    for sym in proj.symbols.values():
        fstate = state.get(sym.path, "missing")
        bf = bundle.files.get(sym.path)
        if bf is not None and bf.kept \
                and sym.qualified in bf.symbols_included:
            symbol_state[sym.id] = "body"
        else:
            symbol_state[sym.id] = fstate

    # ---- reachability ---------------------------------------------
    adjacency: dict[str, list[dict]] = {}
    for edge in proj.edges:
        adjacency.setdefault(edge["caller"], []).append(edge)
    seeds: set[str] = set()
    for rel, st in state.items():
        if st in ("body", "stub"):
            seeds.add(f"{rel}:<module>")
            for sym in proj.symbols_by_path.get(rel, []):
                if symbol_state.get(sym.id) in ("body", "stub"):
                    seeds.add(sym.id)
    visited: set[str] = set()
    stack = list(seeds)
    while stack:
        cur = stack.pop()
        if cur in visited:
            continue
        visited.add(cur)
        for edge in adjacency.get(cur, []):
            if edge["callee"] not in visited:
                stack.append(edge["callee"])

    gaps: dict[str, dict] = {}
    soft: dict[str, dict] = {}
    ok_stub = 0

    def hard(callee_id, via, reason):
        sym = proj.symbols.get(callee_id)
        key = callee_id
        if key not in gaps:
            gaps[key] = {"symbol": sym.qualified if sym else callee_id,
                         "file": sym.path if sym else "",
                         "via": via, "reason": reason,
                         "type": "MISSING-HARD"}

    for edge in proj.edges:
        callee = edge["callee"]
        if callee not in visited:
            continue
        cstate = symbol_state.get(callee, "missing")
        csym = proj.symbols.get(callee)
        if csym is None or csym.kind == "module":
            # File-level: a kept file importing an absent file.
            if edge["kind"] == "import" and state.get(edge["callee_path"]) \
                    in ("excluded", "missing"):
                key = f"file:{edge['callee_path']}"
                if key not in gaps:
                    gaps[key] = {"symbol": edge["callee_path"],
                                 "file": edge["callee_path"],
                                 "via": f"{edge['path']} (import)",
                                 "reason": "imported by a kept file but "
                                           "absent or excluded in the bundle",
                                 "type": "MISSING-HARD"}
            continue
        if cstate in ("excluded", "missing"):
            if edge["kind"] in ("exact", "heuristic", "attribute",
                                "reference", "fixture"):
                hard(callee, edge["caller"],
                     f"{edge['kind']} reference from a kept symbol, but "
                     f"this symbol is {cstate} in the bundle")
        elif cstate == "stub":
            if edge["kind"] in ("exact", "attribute", "fixture"):
                key = callee
                if key not in soft:
                    reason = ("heuristic: directly called by a "
                              "kept symbol but only signatures/"
                              "interfaces kept, no body")
                    if edge["kind"] == "fixture":
                        reason = ("heuristic: requested by name as a "
                                  "test fixture by a kept test or "
                                  "fixture, but only signatures/"
                                  "interfaces kept, no body to run")
                    soft[key] = {"symbol": csym.qualified, "file": csym.path,
                                 "via": edge["caller"],
                                 "reason": reason,
                                 "type": "MISSING-SOFT"}
            else:
                ok_stub += 1

    # ---- task-term check -------------------------------------------
    terms = _task_terms(task)
    if terms:
        for sym in proj.symbols.values():
            if sym.kind == "module":
                continue
            names = {sym.name.lower(), sym.qualified.lower(),
                     sym.qualified.rsplit(".", 1)[-1].lower()}
            file_hit = Path(sym.path).name.lower() in terms
            if names & terms or file_hit:
                st = symbol_state.get(sym.id, "missing")
                if st in ("excluded", "missing"):
                    hard(sym.id, "task",
                         "named by the task but absent or excluded in "
                         "the bundle")

    # ---- ungrounded task requirements ------------------------------
    # A task word that matches only a function-local variable name
    # derives a requirement Verify cannot ground in a real symbol: a
    # slice can include or exclude a definition, never a local. Say
    # so in plain words instead of inventing a symbol name (the old
    # scanner registered locals as constants, which produced phantom
    # gaps like `find_budget.budget` that no expansion could close).
    ungrounded: list[dict] = []
    if terms:
        real_names = {s.name.lower() for s in proj.symbols.values()
                      if s.kind != "module"}
        for lname, places in sorted(proj.local_names.items()):
            if lname.lower() not in terms or lname.lower() in real_names:
                continue
            first = places[0]
            ungrounded.append({
                "term": lname,
                "file": first["path"],
                "detail": (
                    f"the task mentions '{lname}', but in this project "
                    f"'{lname}' is only a local variable (for example "
                    f"inside {first['enclosing']} in {first['path']}), "
                    f"not a definition a slice can include or exclude; "
                    f"this requirement cannot be grounded in a real "
                    f"symbol and is not counted as a gap"),
            })

    # ---- grounding net ----------------------------------------------
    # Invariant: a reported gap must name something real. A symbol
    # gap is grounded when the symbol exists in the project scan or
    # is listed in the bundle manifest's symbols_included; a
    # file-level gap is grounded when the file exists in the
    # project. Anything else is a derived requirement Verify cannot
    # ground: it moves to ungrounded_requirements in plain words and
    # never counts toward the verdict.
    manifest_symbols = {s for f in bundle.files.values()
                        for s in f.symbols_included}
    qualified_names = {s.qualified for s in proj.symbols.values()}

    def _grounded(g: dict) -> bool:
        if g["symbol"] == g["file"] and g["file"] in proj.files:
            return True  # file-level gap: the file itself is the gap
        return (g["symbol"] in qualified_names
                or g["symbol"] in manifest_symbols)

    for bucket in (gaps, soft):
        for key in [k for k, g in bucket.items() if not _grounded(g)]:
            g = bucket.pop(key)
            ungrounded.append({
                "term": g["symbol"],
                "file": g["file"],
                "detail": (
                    f"Verify derived a requirement on '{g['symbol']}' "
                    f"(via {g['via']}), but no symbol with that name "
                    f"exists in the project or in the bundle manifest, "
                    f"so it is reported here in plain words instead of "
                    f"as a gap"),
            })
    report["ungrounded_requirements"] = ungrounded
    if ungrounded:
        report["notes"].append(
            f"{len(ungrounded)} task-derived requirement(s) could not "
            "be grounded in a real symbol; they are listed under "
            "ungrounded_requirements and are not counted as gaps")
    report["gaps"] = sorted(gaps.values(), key=lambda g: (g["file"], g["symbol"]))
    report["soft_gaps"] = sorted(soft.values(), key=lambda g: (g["file"], g["symbol"]))
    report["ok_stub_count"] = ok_stub
    # Distinct fixture references (parameter injection + usefixtures)
    # the reachability walk followed; 0 on projects without pytest
    # fixtures. Evidence for how much of the verdict rests on the
    # fixture graph rather than the call graph.
    report["fixture_references"] = len({
        (e["caller"], e["callee"]) for e in proj.edges
        if e["kind"] == "fixture"})
    report["visited_symbols"] = len(visited)
    report["confidence"] = {
        lang: proj.confidence_for(lang)
        for lang in sorted({proj.languages[p] for p in proj.files})
    }
    report["tokens"] = {
        "bundle_original": bundle.total_original_tokens,
        "bundle_sliced": bundle.total_sliced_tokens,
        "project_total": proj.total_tokens,
    }
    report["carryover"] = _carryover(project_root, bundle)
    if report["gaps"]:
        report["verdict"] = "gaps_found"
        report["exit_code"] = 1
    else:
        report["verdict"] = "sufficient"
        report["exit_code"] = 0
        report["notes"].append("no hard gaps found by static check")
    return report


# ---------------------------------------------------------------------------
# fix: auto-expand by re-exporting through GhostSlice
# ---------------------------------------------------------------------------

_UPGRADE = {"exclude": "signatures", "missing": "signatures",
            "signatures": "full", "interfaces": "full",
            "structure": "full", "headings": "full"}


def run_fix(project_root, bundle_path, out_path, task: str = "") -> dict:
    """Expand a bundle until pre passes or no progress (max 3 rounds).

    Expansion is a fresh GhostSlice export with added/upgraded
    selections. Sliced files are never hand-edited. Without
    GhostSlice this is a could_not_verify (exit 2): faking an
    exporter would break the manifest contract."""
    report = run_pre(project_root, bundle_path, task)
    report["kind"] = "fix"
    if report["exit_code"] == 2:
        return report
    exporter = submodule("exporter")
    profile_mod = submodule("profile")
    if exporter is None or profile_mod is None:
        report.update(verdict="could_not_verify", exit_code=2)
        report["notes"].append("ghostslice exporter unavailable; fix "
                               "requires GhostSlice (no fallback exporter)")
        return report
    try:
        bundle = load_bundle(bundle_path)
        proj = scan_project(project_root)
        profile = profile_mod.load_profile(str(project_root))
    except Exception as exc:
        report.update(verdict="could_not_verify", exit_code=2)
        report["notes"].append(f"fix setup failed: {exc}")
        report["expansion_rounds"] = []
        return report
    protected = set(profile.entry_points or [])
    selections = {p: f.mode for p, f in bundle.files.items()}
    rounds = []
    current_out = Path(out_path)
    current_report = report
    expanded_path: Path | None = None  # set only when an export lands
    for rnd in range(1, 4):
        hard_files = sorted({g["file"] for g in current_report["gaps"] if g["file"]})
        if not hard_files:
            break
        changed = []
        for rel in hard_files:
            cur = selections.get(rel) or bundle.status_of(rel)
            nxt = _UPGRADE.get(cur)
            if rel in protected and cur not in ("exclude", "missing"):
                continue  # protected modes are never downgraded; an
                # upgrade is fine, but a protected file already kept
                # is not the gap.
            if nxt is None:
                continue
            selections[rel] = nxt
            changed.append(f"{rel} ({cur} -> {nxt})")
        if not changed:
            rounds.append({"round": rnd, "changed": [],
                           "note": "no upgradeable hard gaps left",
                           "sliced_tokens": (current_report.get("tokens")
                                             or {}).get("bundle_sliced", 0),
                           "hard_gaps": len(current_report["gaps"])})
            break
        include = set(bundle.files) | set(hard_files)
        try:
            manifest = exporter.build_bundle(str(project_root), str(current_out),
                                             selections=selections, profile=profile,
                                             include=include)
        except Exception as exc:
            # An exporter failure (disk full, project changed under
            # us, a GhostSlice plugin error) is a could-not-verify,
            # not a traceback: the exit-code contract says a bundle
            # that could not be verified exits 2.
            report.update(verdict="could_not_verify", exit_code=2)
            report["notes"].append(f"ghostslice exporter failed during "
                                   f"fix: {exc}")
            report["expansion_rounds"] = rounds
            report["expanded_bundle"] = (
                str(expanded_path.resolve())
                if expanded_path is not None and expanded_path.is_dir()
                else None)
            return report
        expanded_path = current_out
        current_report = run_pre(project_root, current_out, task)
        sliced = manifest.get("total_sliced_tokens") \
            if isinstance(manifest, dict) else None
        if sliced is None:
            sliced = (current_report.get("tokens") or {}).get(
                "bundle_sliced", 0)
        rounds.append({"round": rnd, "changed": changed,
                       "sliced_tokens": sliced,
                       "hard_gaps": len(current_report["gaps"])})
        if current_report["exit_code"] == 0:
            break
    report["expansion_rounds"] = rounds
    # current_report can be a could_not_verify report (no "tokens"
    # key) when the freshly exported bundle is itself unreadable.
    report["tokens"]["expanded_sliced"] = (current_report.get("tokens")
                                           or {}).get(
        "bundle_sliced", report["tokens"]["bundle_sliced"])
    report["gaps"] = current_report["gaps"]
    report["soft_gaps"] = current_report["soft_gaps"]
    report["ungrounded_requirements"] = current_report.get(
        "ungrounded_requirements", report.get("ungrounded_requirements", []))
    # Cite only a bundle that was actually written. A round can be
    # recorded without any export (e.g. "no upgradeable hard gaps
    # left"); citing the output path then would point the reader at
    # evidence that does not exist on disk.
    report["expanded_bundle"] = (
        str(expanded_path.resolve())
        if expanded_path is not None and expanded_path.is_dir() else None)
    if rounds and expanded_path is None:
        report["notes"].append(
            "fix wrote no expanded bundle: no hard gap could be "
            "upgraded by re-export, so expanded_bundle is null rather "
            "than a path that does not exist")
    if current_report["exit_code"] == 2 and rounds:
        # The re-check of the expanded bundle could not verify:
        # propagate 2 rather than relabelling it "gaps remain".
        report["verdict"] = "could_not_verify"
        report["exit_code"] = 2
        report["notes"].append("expanded bundle could not be verified: "
                               + "; ".join(current_report.get("notes", [])))
    elif current_report["exit_code"] == 0 and rounds:
        report["verdict"] = "sufficient_after_expand"
        report["exit_code"] = 0
    elif rounds:
        report["verdict"] = "gaps_remain"
        report["exit_code"] = 1
    return report


# ---------------------------------------------------------------------------
# post: patch check (temp copy only)
# ---------------------------------------------------------------------------

_DEF_RE = re.compile(
    r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)|^\s*class\s+([A-Za-z_]\w*)|"
    r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)|"
    r"^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=|"
    r"^\s*([A-Za-z_]\w*)\s*=\s*")
_CALL_RE = re.compile(r"(?<![\w.])([A-Za-z_]\w*)\s*\(")
_IMPORT_NAME_RE = re.compile(
    r"^\s*(?:from\s+[\w.]+\s+import|import)\s+(.+)$")
_KEYWORDS = {"if", "elif", "while", "for", "return", "with", "assert",
             "not", "and", "or", "in", "is", "lambda", "yield",
             "await", "raise", "except", "print"}


def _verify_config(project_root: Path) -> dict:
    """GhostVerify's own per-project config, ``.ghostverify.json``.

    Currently recognised key: ``test_command`` (used by ``post``
    when ``--run-tests`` is given and no ``--test-command`` was
    passed). A missing or unparseable file means no config, not an
    error. Entry points and protected modes are not read here:
    those come from GhostSlice's ``.ghostslice.json`` profile."""
    cfg = project_root / ".ghostverify.json"
    if cfg.is_file():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}
    return {}


def run_post(project_root, bundle_path, patch_path, task: str = "",
             run_tests: bool = False, test_command: str | None = None,
             timeout: int = 120) -> dict:
    """Post-run patch check. Returns the ``run_pre`` report with a
    ``post`` section added and the verdict/exit code overwritten by
    the patch outcome (``ok`` = 0, ``flagged``/``failed`` = 1,
    ``could_not_verify`` = 2).

    Static by default. The patch is applied only to a temporary
    copy of the project; the working tree is never touched. Tests
    run only when ``run_tests`` is true, a test command is set
    (``test_command`` or ``.ghostverify.json``), and the patch
    applied cleanly to the temp copy."""
    # A non-positive timeout makes subprocess.run report an instant
    # "timeout" for commands that never ran; fall back to the
    # documented default instead of manufacturing that verdict.
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) \
            or timeout <= 0:
        timeout = 120
    project_root = Path(project_root)
    report = run_pre(project_root, bundle_path, task)
    report["kind"] = "post"
    if report["exit_code"] == 2:
        report["post"] = {"verdict": "could_not_verify",
                          "applied_temp_copy": False, "findings": [],
                          "tests": {"status": "not run"},
                          "diagnosis": "pre-check could not verify"}
        return report
    try:
        patch_text = Path(patch_path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        report["exit_code"] = 2
        report["post"] = {"verdict": "could_not_verify",
                          "applied_temp_copy": False, "findings": [],
                          "tests": {"status": "not run"},
                          "diagnosis": f"patch unreadable: {exc}"}
        return report
    bundle = load_bundle(bundle_path)
    proj = scan_project(project_root)
    patches = parse_patch(patch_text)
    findings: list[dict] = []

    created: set[str] = set()
    for fp in patches:
        for line in fp.added_lines:
            m = _DEF_RE.match(line)
            if m:
                created.update(g for g in m.groups() if g)
            im = _IMPORT_NAME_RE.match(line)
            if im:
                for part in im.group(1).replace("(", " ").replace(")", " ").split(","):
                    words = part.strip().split()
                    if words:
                        created.add(words[-1])
    project_by_name: dict[str, list] = {}
    for sym in proj.symbols.values():
        if sym.kind != "module":
            project_by_name.setdefault(sym.name, []).append(sym)

    for fp in patches:
        rel = fp.path
        status = bundle.status_of(rel) if rel in proj.files else "missing"
        file_in_bundle = rel in bundle.files and status != "excluded"
        if not file_in_bundle:
            findings.append({"type": "touches_file_not_in_bundle",
                             "file": rel,
                             "detail": f"patch touches a file that is "
                                       f"{status} in the bundle"})
        if is_test_path(rel):
            findings.append({"type": "edits_test_file", "file": rel,
                             "detail": "patch edits a test file; flagged "
                                       "for review, not auto-rejected"})
        import builtins as _builtins

        for line in fp.added_lines:
            for name in _CALL_RE.findall(line):
                if name in created or name in _KEYWORDS \
                        or hasattr(_builtins, name):
                    continue
                defs = [s for s in project_by_name.get(name, [])
                        if s.path != rel]
                if defs:
                    # The agent saw the name if ANY definition of it
                    # was in the bundle (body or signatures). Flag
                    # only when every definition is unseen; flagging
                    # per-excluded-definition used to condemn a call
                    # whose name was plainly visible in a kept file
                    # that also defines it.
                    if any(bundle.status_of(s.path)
                           not in ("missing", "excluded") for s in defs):
                        continue
                    sym = defs[0]
                    sstate = bundle.status_of(sym.path)
                    findings.append({
                        "type": "references_unseen_symbol",
                        "file": rel, "symbol": sym.qualified,
                        "detail": f"added line calls {name}(), defined "
                                  f"in {sym.path} which is {sstate} "
                                  f"in the bundle"})
                    continue
                if any(s.path == rel for s in project_by_name.get(name, [])):
                    continue  # defined in the patched file itself
                findings.append({
                    "type": "references_unseen_symbol",
                    "file": rel, "symbol": name,
                    "detail": f"added line calls {name}(), which is not "
                              f"defined in the bundle or the project "
                              f"and not created by the patch (unseen)"})
    # dedupe findings
    seen: set = set()
    unique = []
    for f in findings:
        key = json.dumps(f, sort_keys=True)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    findings = unique

    tests = {"status": "not run (static-only; pass --run-tests and "
                        "configure a test command to enable)"}
    applied = False
    tmp = Path(tempfile.mkdtemp(prefix="ghostverify-post-"))
    try:
        work = tmp / "project"
        shutil.copytree(project_root, work,
                        ignore=shutil.ignore_patterns(
                            ".git", "__pycache__", ".ghostslice-cache",
                            ".claude_context"))
        result = apply_patch(work, patches)
        applied = bool(result["applied_all"])
        if not applied:
            findings.append({"type": "patch_does_not_apply", "file": "",
                             "detail": json.dumps(result["files"])[:500]})
        cfg = _verify_config(project_root)
        cmd = test_command or cfg.get("test_command")
        if run_tests and applied:
            if not cmd:
                tests = {"status": "requested but no test command "
                                   "configured (CLI --test-command or "
                                   ".ghostverify.json test_command)"}
            elif not isinstance(cmd, str):
                # A hand-edited .ghostverify.json can carry any JSON
                # type here; subprocess would raise TypeError and
                # crash the whole post check on a config typo.
                tests = {"status": "requested but the configured test "
                                   "command is not a string "
                                   "(.ghostverify.json test_command)"}
            else:
                try:
                    proc = subprocess.run(cmd, shell=True, cwd=work,
                                          capture_output=True, text=True,
                                          timeout=timeout)
                    output = (proc.stdout + proc.stderr)[-4000:]
                    tests = {"status": "passed" if proc.returncode == 0
                             else "failed",
                             "command": cmd, "exit_code": proc.returncode,
                             "output_tail": output}
                except subprocess.TimeoutExpired:
                    tests = {"status": "timeout", "command": cmd,
                             "timeout_s": timeout}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    diagnosis = "cause not tied to the slice"
    if tests.get("status") == "failed":
        output = tests.get("output_tail", "")
        stub_names = []
        for sym in proj.symbols.values():
            if bundle.status_of(sym.path) == "stub" and sym.name in output:
                stub_names.append(sym.qualified)
        if stub_names:
            diagnosis = ("test failure mentions "
                         + ", ".join(sorted(set(stub_names)))
                         + ", which was signatures-only in the bundle")
    elif findings:
        diagnosis = "static findings above; no test failure to diagnose"
    else:
        diagnosis = "no findings; nothing to diagnose"

    if any(f["type"] == "patch_does_not_apply" for f in findings) \
            or tests.get("status") in ("failed", "timeout"):
        verdict = "failed"
    elif findings:
        verdict = "flagged"
    else:
        verdict = "ok"
    post = {"verdict": verdict, "applied_temp_copy": applied,
            "findings": findings, "tests": tests, "diagnosis": diagnosis,
            "note": "patch was applied only to a temporary copy; the "
                    "working tree was never touched"}
    if tests.get("status") in ("passed", "failed", "timeout"):
        # The one flow that executes anything: `post --run-tests`
        # ran the project's own configured test command against the
        # patched temporary copy. That evidence is "tests-executed"
        # under the shared contract (GhostBench uses the same word
        # for the same act); everything else in this report stays
        # static analysis, as the basis string says.
        report["verification_level"] = LEVEL_TESTS
        report["verification_level_basis"] = LEVEL_BASIS_TESTS
        report["notes"].append(
            "verification level is tests-executed because post ran "
            "the project's own test command in the temporary copy; "
            "the bundle and patch checks themselves are static")
    report["post"] = post
    report["verdict"] = verdict
    report["exit_code"] = 0 if verdict == "ok" else 1
    return report
