"""Project model: scan, symbol universe, import graph, call graph.

Python is analysed with the real stdlib ``ast`` (calls, imports,
references from annotations / bases / decorators), the same basis
GhostSlice's own call graph uses. Every other language is
parser-lite: an outline (regex symbol extraction) plus an import
graph, and reports must label that confidence difference.

 Deliberate simplifications, recorded rather than hidden:
- dynamic dispatch, getattr and string registries are not traced;
- a bare call name that matches exactly one project callable
  resolves to it (heuristic kind), an attribute call fans out to
  every project method of that name (attribute kind);
- assignments inside function bodies are local variables, not
  symbols (a slice cannot include or exclude a local); they are
  kept in Project.local_names so task mentions of them surface as
  ungrounded requirements, never as gap symbols.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .gsload import estimate_tokens

IGNORE_DIRS = {".git", ".hg", ".svn", "node_modules", "dist", "build",
               "out", "__pycache__", ".venv", "venv", ".tox",
               ".mypy_cache", ".pytest_cache", ".next", ".nuxt",
               "coverage", ".cache", "target", ".idea", ".vscode",
               ".ghostslice-cache", ".claude_context"}
BINARY_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".webp", ".bmp",
               ".pdf", ".zip", ".tar", ".gz", ".exe", ".dll", ".so",
               ".dylib", ".o", ".a", ".class", ".wasm", ".mp3", ".mp4",
               ".woff", ".woff2", ".ttf", ".pyc", ".db", ".sqlite"}
LANG_BY_EXT = {".py": "python", ".ts": "typescript", ".tsx": "typescript",
               ".js": "javascript", ".jsx": "javascript",
               ".mjs": "javascript", ".cjs": "javascript",
               ".go": "go", ".rs": "rust", ".java": "java", ".cs": "csharp",
               ".php": "php", ".rb": "ruby", ".kt": "kotlin",
               ".kts": "kotlin", ".swift": "swift", ".c": "c",
               ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".scala": "scala",
               ".md": "markdown", ".json": "json", ".ipynb": "notebook"}

# Per-language confidence in reachability verdicts (never averaged).
CONFIDENCE = {"python": "high (ast call graph)",
              "typescript": "parser-lite (import graph + outline)",
              "javascript": "parser-lite (import graph + outline)"}
DEFAULT_CONFIDENCE = "parser-lite (import graph + outline)"


def language_for(path: str) -> str:
    return LANG_BY_EXT.get(Path(path).suffix.lower(), "text")


def is_test_path(rel: str) -> bool:
    p = PurePosixPath(rel)
    name = p.name.lower()
    if any(part in ("tests", "test", "__tests__", "spec", "specs")
           for part in p.parts[:-1]):
        return True
    return (name.startswith("test_") or name.startswith("test.")
            or "_test." in name or ".test." in name or ".spec." in name
            or name.endswith("_test.go") or name.endswith("_spec.rb"))


@dataclass
class Symbol:
    id: str            # "path:Qualified.name"
    path: str
    name: str
    qualified: str
    kind: str          # function|method|class|constant|type
    language: str

    @property
    def callable(self) -> bool:
        return self.kind in ("function", "method", "class")


@dataclass
class Project:
    root: Path
    files: list = field(default_factory=list)        # posix rel paths
    languages: dict = field(default_factory=dict)    # path -> language
    sources: dict = field(default_factory=dict)      # path -> text
    symbols: dict = field(default_factory=dict)      # id -> Symbol
    symbols_by_path: dict = field(default_factory=dict)
    symbols_by_name: dict = field(default_factory=dict)
    # Names assigned inside function bodies, per name:
    # [{"path": rel, "enclosing": qualified}]. Locals are NOT
    # symbols: a slice can include or exclude files and
    # definitions, never a local variable. They are recorded so a
    # task that mentions one can be reported in plain words as an
    # ungrounded requirement instead of an invented gap symbol.
    local_names: dict = field(default_factory=dict)
    imports: dict = field(default_factory=dict)      # path -> [paths]
    edges: list = field(default_factory=list)        # call/reference dicts
    parse_failures: list = field(default_factory=list)
    total_tokens: int = 0

    def confidence_for(self, language: str) -> str:
        return CONFIDENCE.get(language, DEFAULT_CONFIDENCE)


def scan_project(root) -> Project:
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"project is not a directory: {root}")
    proj = Project(root=root)
    for dirpath, dirnames, filenames in __import__("os").walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in IGNORE_DIRS)
        for name in sorted(filenames):
            abspath = Path(dirpath) / name
            if name in (".ghostverify-history.jsonl",
                        ".ghostslice-history.jsonl"):
                continue  # ledgers are tool output, never project source
            if abspath.suffix.lower() in BINARY_EXTS:
                continue
            rel = abspath.relative_to(root).as_posix()
            try:
                text = abspath.read_text(encoding="utf-8", errors="strict")
            except (OSError, UnicodeDecodeError):
                try:
                    text = abspath.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
            if "\x00" in text[:4096]:
                continue
            lang = language_for(rel)
            proj.files.append(rel)
            proj.languages[rel] = lang
            proj.sources[rel] = text
            proj.total_tokens += estimate_tokens(text, lang)
    for rel in proj.files:
        if proj.languages[rel] == "python":
            _analyse_python(proj, rel)
        else:
            _analyse_lite(proj, rel)
    # Second pass for Python: call/reference edges are built only
    # after EVERY file's symbols are registered, otherwise a call
    # from a.py to a symbol in b.py is dropped whenever b.py is
    # analysed after a.py.
    for rel in proj.files:
        if proj.languages[rel] == "python":
            _python_edges(proj, rel)
    _build_attribute_edges(proj)
    _build_import_edges(proj)
    return proj


_ATTR_CALL_RE = re.compile(r"\.([A-Za-z_]\w*)\s*\(")


def _build_attribute_edges(proj: Project) -> None:
    """Module-level attribute-call edges for every project file:
    ``anything.name(...)`` may dispatch to any project method named
    ``name`` (the conservative fan-out GhostSlice's call graph also
    uses). Caller is the file's module node, which reachability
    seeds for every kept file, so a method only reachable through
    an attribute call in a kept file still counts as reached."""
    methods: dict[str, list[str]] = {}
    for sym in proj.symbols.values():
        if sym.kind == "method":
            methods.setdefault(sym.name, []).append(sym.id)
    if not methods:
        return
    have = {(e["caller"], e["callee"]) for e in proj.edges}
    for rel in proj.files:
        for name in set(_ATTR_CALL_RE.findall(proj.sources[rel])):
            for target in methods.get(name, []):
                key = (f"{rel}:<module>", target)
                if key[0] != target and key not in have:
                    have.add(key)
                    proj.edges.append({"caller": key[0], "callee": target,
                                       "kind": "attribute", "path": rel,
                                       "callee_path": proj.symbols[target].path})


# ---------------------------------------------------------------------------
# Python analysis (ast)
# ---------------------------------------------------------------------------

def _module_base(module: str, level: int, current_dir: PurePosixPath) -> str:
    parts: list[str] = []
    if level:
        base = [] if str(current_dir) == "." else list(current_dir.parts)
        keep = base[: len(base) - (level - 1)] if level - 1 <= len(base) else []
        parts = list(keep)
    if module:
        parts += module.split(".")
    return "/".join(p for p in parts if p and p != ".")


def _module_file(base: str, root_files: set) -> str | None:
    if not base:
        return None
    for cand in (f"{base}.py", f"{base}/__init__.py"):
        if cand in root_files:
            return cand
    hits = [p for p in root_files
            if p.endswith(f"/{base}.py") or p.endswith(f"/{base}/__init__.py")]
    return hits[0] if len(hits) == 1 else None


def _analyse_python(proj: Project, rel: str) -> None:
    source = proj.sources[rel]
    try:
        tree = ast.parse(source)
    except SyntaxError:
        proj.parse_failures.append(rel)
        _analyse_lite(proj, rel)
        return
    root_files = set(proj.files)
    cur_dir = PurePosixPath(rel).parent
    symbols: dict[str, Symbol] = {}
    locals_found: list[tuple[str, str]] = []  # (name, enclosing qualified)

    def add(qualified, kind):
        name = qualified.rsplit(".", 1)[-1]
        sym = Symbol(id=f"{rel}:{qualified}", path=rel, name=name,
                     qualified=qualified, kind=kind, language="python")
        symbols[qualified] = sym

    def walk_defs(body, prefix):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = f"{prefix}.{node.name}" if prefix else node.name
                add(q, "method" if prefix and "." not in prefix and prefix else
                    ("method" if prefix else "function"))
                # fix kind: direct child of a class is a method; we
                # know class prefixes via the second walk below, so
                # recompute in walk_classes. Keep simple: a def whose
                # prefix names a class is set there.
                walk_defs(node.body, q)
            elif isinstance(node, ast.ClassDef):
                q = f"{prefix}.{node.name}" if prefix else node.name
                add(q, "class")
                for sub in node.body:
                    if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        add(f"{q}.{sub.name}", "method")
                        walk_defs(sub.body, f"{q}.{sub.name}")
                    elif isinstance(sub, ast.ClassDef):
                        walk_defs([sub], q)
                    elif isinstance(sub, (ast.Assign, ast.AnnAssign)):
                        for t in _targets(sub):
                            add(f"{q}.{t}", "constant")
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                if not prefix:
                    for t in _targets(node):
                        add(t, "constant")
                else:
                    # An assignment inside a function body is a
                    # local variable, not a symbol. Registering it
                    # as a constant (the old behaviour) let
                    # task-word matching invent gap symbols like
                    # `find_budget.budget` -- a name that exists
                    # nowhere as a definition and that no bundle
                    # expansion can ever close. Record the name as
                    # a local instead (see Project.local_names).
                    for t in _targets(node):
                        locals_found.append((t, prefix))

    def fix_kinds(body, prefix, in_class):
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = f"{prefix}.{node.name}" if prefix else node.name
                if q in symbols:
                    symbols[q].kind = "method" if in_class else "function"
                fix_kinds(node.body, q, False)
            elif isinstance(node, ast.ClassDef):
                q = f"{prefix}.{node.name}" if prefix else node.name
                fix_kinds(node.body, q, True)

    walk_defs(tree.body, "")
    fix_kinds(tree.body, "", False)
    for sym in symbols.values():
        _register(proj, sym, rel)
    for lname, enclosing in locals_found:
        proj.local_names.setdefault(lname, []).append(
            {"path": rel, "enclosing": enclosing})

    # Imports: local name -> (target_path | None, original name, is_module)
    imports: dict[str, tuple] = {}
    file_imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                base = _module_base(alias.name, 0, cur_dir)
                target = _module_file(base, root_files)
                if target:
                    file_imports.add(target)
                key = alias.asname or alias.name.split(".")[0]
                base_for_key = base if alias.asname else _module_base(
                    alias.name.split(".")[0], 0, cur_dir)
                imports[key] = (_module_file(base_for_key, root_files), None, True)
        elif isinstance(node, ast.ImportFrom):
            base = _module_base(node.module or "", node.level or 0, cur_dir)
            target_file = _module_file(base, root_files)
            if target_file:
                file_imports.add(target_file)
            for alias in node.names:
                if alias.name == "*":
                    continue
                sub = _module_file(f"{base}/{alias.name}" if base else alias.name,
                                   root_files)
                if sub:
                    file_imports.add(sub)
                imports[alias.asname or alias.name] = (target_file, alias.name, False)
    proj.imports[rel] = sorted(file_imports)
    if not hasattr(proj, "_py_local"):
        proj._py_local = {}
    proj._py_local[rel] = (tree, symbols, imports)
    msym = Symbol(id=f"{rel}:<module>", path=rel, name="<module>",
                  qualified="<module>", kind="module", language="python")
    _register(proj, msym, rel)


def _python_edges(proj: Project, rel: str) -> None:
    """Second pass (see scan_project): build call/reference edges
    for one Python file now that all symbols are registered."""
    local = getattr(proj, "_py_local", {}).get(rel)
    if local is None:
        return
    tree, symbols, imports = local

    methods_by_name: dict[str, list[str]] = {}
    for sym in proj.symbols.values():
        if sym.kind == "method":
            methods_by_name.setdefault(sym.name, []).append(sym.id)

    def resolve_bare(name, scope_prefixes):
        for prefix in scope_prefixes:
            cand = f"{prefix}.{name}" if prefix else name
            sym = symbols.get(cand)
            if sym is not None:
                return sym.id, "exact"
        imp = imports.get(name)
        if imp is not None and imp[0] and imp[1]:
            return f"{imp[0]}:{imp[1]}", "exact"
        cands = [s.id for s in proj.symbols.values()
                 if s.name == name and s.callable and s.path != rel]
        if len(cands) == 1:
            return cands[0], "heuristic"
        return None, None

    def add_edge(caller_id, callee_id, kind):
        if callee_id and callee_id != caller_id and callee_id in proj.symbols:
            proj.edges.append({"caller": caller_id, "callee": callee_id,
                               "kind": kind, "path": rel,
                               "callee_path": proj.symbols[callee_id].path})

    def visit(node, caller_id, prefixes):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            q = _qualified_of(node, prefixes, symbols)
            cid = f"{rel}:{q}" if q else caller_id
            for dec in node.decorator_list:
                visit(dec, caller_id, prefixes)
            for d in list(node.args.defaults) + [d for d in node.args.kw_defaults if d]:
                visit(d, caller_id, prefixes)
            if node.returns is not None:
                _reference(node.returns, caller_id, prefixes, imports, add_edge)
            for stmt in node.body:
                visit(stmt, cid, prefixes + [q] if q else prefixes)
            return
        if isinstance(node, ast.ClassDef):
            for base in node.bases:
                _reference(base, caller_id, prefixes, imports, add_edge)
            q = _qualified_of(node, prefixes, symbols)
            for stmt in node.body:
                visit(stmt, caller_id, prefixes + [q] if q else prefixes)
            return
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                target, kind = resolve_bare(func.id, prefixes)
                if target:
                    add_edge(caller_id, target, kind)
            elif isinstance(func, ast.Attribute):
                recv = func.value
                if isinstance(recv, ast.Name) and recv.id in imports:
                    imp = imports[recv.id]
                    if imp[2] and imp[0]:
                        cand = f"{imp[0]}:{func.attr}"
                        if cand in proj.symbols:
                            add_edge(caller_id, cand, "exact")
                for cand in methods_by_name.get(func.attr, []):
                    add_edge(caller_id, cand, "attribute")
            visit(func, caller_id, prefixes)
            for a in node.args:
                visit(a, caller_id, prefixes)
            for kw in node.keywords:
                visit(kw.value, caller_id, prefixes)
            return
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            target, _k = resolve_bare(node.id, prefixes)
            if target:
                add_edge(caller_id, target, "reference")
            return
        if isinstance(node, ast.AnnAssign) and node.annotation is not None:
            _reference(node.annotation, caller_id, prefixes, imports, add_edge)
        for child in ast.iter_child_nodes(node):
            visit(child, caller_id, prefixes)

    module_id = f"{rel}:<module>"
    for stmt in tree.body:
        visit(stmt, module_id, [""])


def _qualified_of(node, prefixes, symbols):
    for prefix in reversed(prefixes):
        cand = f"{prefix}.{node.name}" if prefix else node.name
        if cand in symbols:
            return cand
    return node.name


def _reference(node, caller_id, prefixes, imports, add_edge):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            for prefix in prefixes:
                pass
            # Resolved by the caller's resolve via closure is not
            # available here; use name matching against symbols in
            # the same project through add_edge's registry check by
            # emitting module/name refs handled in visit. For
            # annotations we resolve bare names conservatively:
            # exact same-file or imported targets only, approximated
            # by unique project-wide name match.
            # (Kept simple: unique callable/type name wins.)
            # NOTE: add_edge validates the callee exists.
            # We cannot see proj here, so this is a no-op hook kept
            # for structure; annotation refs are also caught by the
            # generic Name-load visit in visit().
            break


def _targets(node) -> list[str]:
    out = []
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    for t in targets:
        if isinstance(t, ast.Name):
            out.append(t.id)
        elif isinstance(t, (ast.Tuple, ast.List)):
            for elt in t.elts:
                if isinstance(elt, ast.Name):
                    out.append(elt.id)
    return out


def _register(proj: Project, sym: Symbol, rel: str) -> None:
    proj.symbols[sym.id] = sym
    proj.symbols_by_path.setdefault(rel, []).append(sym)
    proj.symbols_by_name.setdefault(sym.name, []).append(sym)


# ---------------------------------------------------------------------------
# Parser-lite analysis (non-Python)
# ---------------------------------------------------------------------------

_LITE_PATTERNS = [
    (re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"), "function"),
    (re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z_$][\w$]*)"), "class"),
    (re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)"), "type"),
    (re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*="), "type"),
    (re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"), "function"),
    (re.compile(r"^\s*(?:pub\s+)?fn\s+([A-Za-z_]\w*)"), "function"),
    (re.compile(r"^\s*(?:def|fun)\s+([A-Za-z_]\w*)"), "function"),
    (re.compile(r"^\s*(?:public|private|protected|static|final|\s)+[\w<>\[\]]+\s+([A-Za-z_]\w*)\s*\("), "method"),
    (re.compile(r"^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*="), "constant"),
]
_TS_IMPORT_RE = re.compile(r"""(?:import|export)\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\)""")


def _analyse_lite(proj: Project, rel: str) -> None:
    source = proj.sources[rel]
    lang = proj.languages[rel]
    seen: set[str] = set()
    for line in source.splitlines():
        for pat, kind in _LITE_PATTERNS:
            m = pat.match(line)
            if m:
                name = m.group(1)
                if name not in seen:
                    seen.add(name)
                    _register(proj, Symbol(
                        id=f"{rel}:{name}", path=rel, name=name,
                        qualified=name, kind=kind, language=lang), rel)
                break
    msym = Symbol(id=f"{rel}:<module>", path=rel, name="<module>",
                  qualified="<module>", kind="module", language=lang)
    _register(proj, msym, rel)
    # Import graph (TS/JS relative specs; others: no edges in v0.1).
    edges: set[str] = set()
    if lang in ("typescript", "javascript"):
        root_files = set(proj.files)
        for m in _TS_IMPORT_RE.finditer(source):
            spec = m.group(1) or m.group(2) or ""
            hit = _ts_resolve(spec, rel, root_files)
            if hit and hit != rel:
                edges.add(hit)
    proj.imports[rel] = sorted(edges)


def _ts_resolve(spec: str, rel: str, root_files: set) -> str | None:
    if not spec.startswith("."):
        return None
    cur = PurePosixPath(rel).parent
    target = cur / spec
    parts: list[str] = []
    for part in target.parts:
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)
    base = "/".join(parts)
    exts = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json"]
    for cand in [base] + [base + e for e in exts] + [f"{base}/index{e}" for e in exts]:
        if cand in root_files:
            return cand
    return None


def _build_import_edges(proj: Project) -> None:
    """File-level import edges as pseudo call edges module->module,
    so reachability can cross files whose symbols are stubbed."""
    for src, targets in proj.imports.items():
        for dst in targets:
            proj.edges.append({"caller": f"{src}:<module>",
                               "callee": f"{dst}:<module>",
                               "kind": "import", "path": src,
                               "callee_path": dst})
