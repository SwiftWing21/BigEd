#!/usr/bin/env python3
"""
v0.48: AST-based dead code scanner for fleet codebase.
Identifies unused functions, classes, imports, and variables.

Usage:
    python dead_code_scan.py                    # Scan fleet/ directory
    python dead_code_scan.py --path ../BigEd    # Scan specific path
    python dead_code_scan.py --json             # Output as JSON
    python dead_code_scan.py --graveyard        # Copy dead code to _graveyard/
"""

import argparse
import ast
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Definition:
    """A defined name (function, class, import, variable) with its location."""
    name: str
    kind: str  # "function", "class", "import", "variable"
    file: str
    line: int
    # For imports: the module the name was imported from
    import_module: Optional[str] = None
    # Source lines (for graveyard extraction)
    source_lines: tuple = field(default_factory=tuple, repr=False)


@dataclass
class Finding:
    """A dead-code finding ready for reporting."""
    category: str  # UNUSED_FUNCTION, UNUSED_CLASS, UNUSED_IMPORT
    name: str
    file: str
    line: int
    detail: str = ""


# ---------------------------------------------------------------------------
# Exclusion rules — avoid false positives
# ---------------------------------------------------------------------------

# Module-level constants that are read dynamically by worker.py / smoke_test.py
DYNAMIC_CONSTANTS = frozenset({
    "SKILL_NAME",
    "DESCRIPTION",
    "REQUIRES_NETWORK",
})

# Functions that are entry points or called dynamically
DYNAMIC_ENTRY_POINTS = frozenset({
    "run",           # skills entry point — called by worker via importlib
    "main",          # script entry point
    "setup",         # setup.py / plugin entry
    "teardown",      # cleanup hook
    "configure",     # config hook
})

# Decorator names whose presence makes a function an entry point
ENTRY_POINT_DECORATORS = frozenset({
    "app.route",
    "app.get",
    "app.post",
    "app.put",
    "app.delete",
    "app.patch",
    "app.errorhandler",
    "app.before_request",
    "app.after_request",
    "app.teardown_request",
    "app.teardown_appcontext",
    "app.context_processor",
    "app.template_filter",
    "app.cli.command",
    "property",
    "staticmethod",
    "classmethod",
    "abstractmethod",
    # Discord.py / event-driven decorators
    "client.event",
    "bot.event",
    "client.listen",
    "bot.listen",
    "bot.command",
    "client.command",
})

# Method names inherited from stdlib base classes (called by framework, not user code)
FRAMEWORK_OVERRIDES = frozenset({
    # html.parser.HTMLParser
    "handle_starttag", "handle_endtag", "handle_data", "handle_startendtag",
    "handle_entityref", "handle_charref", "handle_comment", "handle_decl", "handle_pi",
    # threading.Thread
    "run",
    # unittest.TestCase
    "setUp", "tearDown", "setUpClass", "tearDownClass",
    # contextmanager protocol
    "__enter__", "__exit__",
    # iterator protocol
    "__iter__", "__next__",
})

# Built-in / dunder names that are never dead code
DUNDER_PATTERN = "__"


def _decorator_name(node: ast.expr) -> str:
    """Extract a readable name from a decorator AST node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        # e.g. app.route
        parts = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
    if isinstance(node, ast.Call):
        return _decorator_name(node.func)
    return ""


def _is_excluded_definition(defn: Definition, file_source_lines: list[str]) -> bool:
    """Return True if this definition should be excluded from dead-code checks."""
    name = defn.name

    # Private names (single leading underscore) — convention for internal use
    if name.startswith("_") and not name.startswith("__"):
        return True

    # Dunder methods / attributes
    if name.startswith(DUNDER_PATTERN) and name.endswith(DUNDER_PATTERN):
        return True

    # Test functions
    if name.startswith("test_"):
        return True

    # Dynamic entry points
    if name in DYNAMIC_ENTRY_POINTS:
        return True

    # Dynamic constants read by worker/smoke_test
    if name in DYNAMIC_CONSTANTS:
        return True

    return False


def _has_entry_point_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """Check whether a function/method has a decorator that makes it an entry point."""
    for dec in node.decorator_list:
        dname = _decorator_name(dec)
        if dname in ENTRY_POINT_DECORATORS:
            return True
        # Partial match for route-like decorators (e.g. blueprint.route)
        if any(dname.endswith(f".{ep}") for ep in
               ("route", "get", "post", "put", "delete", "event", "listen", "command")):
            return True
    return False


def _is_signal_handler(name: str, tree: ast.Module) -> bool:
    """Check if a function is registered via signal.signal() in the same file."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr == "signal":
                if isinstance(func.value, ast.Name) and func.value.id == "signal":
                    # signal.signal(SIG, handler) — second arg is the handler
                    if len(node.args) >= 2:
                        handler_arg = node.args[1]
                        if isinstance(handler_arg, ast.Name) and handler_arg.id == name:
                            return True
    return False


# ---------------------------------------------------------------------------
# AST scanning
# ---------------------------------------------------------------------------

def collect_py_files(root: Path) -> list[Path]:
    """Collect all .py files under root, excluding .venv, __pycache__, _graveyard."""
    excluded_dirs = {".venv", "__pycache__", "_graveyard", "node_modules", ".git", ".tox"}
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded directories in-place
        dirnames[:] = [d for d in dirnames if d not in excluded_dirs]
        for fname in filenames:
            if fname.endswith(".py"):
                files.append(Path(dirpath) / fname)
    return sorted(files)


def parse_file(filepath: Path) -> tuple[Optional[ast.Module], list[str]]:
    """Parse a Python file into an AST, returning (tree, source_lines) or (None, [])."""
    try:
        source = filepath.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(filepath))
        return tree, source.splitlines()
    except (SyntaxError, UnicodeDecodeError):
        return None, []


def collect_definitions(filepath: Path, tree: ast.Module, source_lines: list[str]) -> list[Definition]:
    """Collect function, class, and import definitions from a parsed AST."""
    defs = []
    rel = str(filepath)

    for node in ast.iter_child_nodes(tree):
        # Top-level function defs
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _has_entry_point_decorator(node):
                continue
            if _is_signal_handler(node.name, tree):
                continue
            end_line = getattr(node, "end_lineno", node.lineno)
            defs.append(Definition(
                name=node.name,
                kind="function",
                file=rel,
                line=node.lineno,
                source_lines=tuple(source_lines[node.lineno - 1 : end_line]),
            ))

        # Top-level class defs
        elif isinstance(node, ast.ClassDef):
            end_line = getattr(node, "end_lineno", node.lineno)
            defs.append(Definition(
                name=node.name,
                kind="class",
                file=rel,
                line=node.lineno,
                source_lines=tuple(source_lines[node.lineno - 1 : end_line]),
            ))

            # Skip methods inside private classes — the class exclusion covers them
            if node.name.startswith("_"):
                continue

            # Also collect methods inside the class
            for item in ast.iter_child_nodes(node):
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if _has_entry_point_decorator(item):
                        continue
                    if _is_signal_handler(item.name, tree):
                        continue
                    # Skip framework method overrides (HTMLParser, Thread, etc.)
                    if item.name in FRAMEWORK_OVERRIDES:
                        continue
                    m_end = getattr(item, "end_lineno", item.lineno)
                    defs.append(Definition(
                        name=item.name,
                        kind="function",
                        file=rel,
                        line=item.lineno,
                        source_lines=tuple(source_lines[item.lineno - 1 : m_end]),
                    ))

    return defs


def collect_imports(filepath: Path, tree: ast.Module) -> list[Definition]:
    """Collect import statements from a parsed AST."""
    imports = []
    rel = str(filepath)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                local_name = alias.asname if alias.asname else alias.name
                # For dotted imports like "import os.path", the local name is "os"
                if "." in local_name:
                    local_name = local_name.split(".")[0]
                imports.append(Definition(
                    name=local_name,
                    kind="import",
                    file=rel,
                    line=node.lineno,
                    import_module=alias.name,
                ))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                if alias.name == "*":
                    continue  # star imports can't be checked
                local_name = alias.asname if alias.asname else alias.name
                imports.append(Definition(
                    name=local_name,
                    kind="import",
                    file=rel,
                    line=node.lineno,
                    import_module=f"{module}.{alias.name}" if module else alias.name,
                ))

    return imports


def collect_name_usages(tree: ast.Module) -> set[str]:
    """Collect all Name identifier usages in an AST (excludes definition sites)."""
    usages = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            usages.add(node.id)
        elif isinstance(node, ast.Attribute):
            usages.add(node.attr)
        # String annotations (type hints as strings)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Could be a forward reference — add it as a possible usage
            val = node.value.strip()
            if val.isidentifier():
                usages.add(val)
    return usages


# ---------------------------------------------------------------------------
# Dead code analysis
# ---------------------------------------------------------------------------

def find_unused_imports(
    file_trees: dict[Path, tuple[ast.Module, list[str]]],
) -> list[Finding]:
    """Find imports that are never referenced in the same file."""
    findings = []

    for filepath, (tree, source_lines) in file_trees.items():
        if tree is None:
            continue

        imports = collect_imports(filepath, tree)
        usages = collect_name_usages(tree)

        for imp in imports:
            if _is_excluded_definition(imp, source_lines):
                continue

            # __future__ imports are always implicit / compiler directives
            if imp.import_module and imp.import_module.startswith("__future__"):
                continue

            # Check if the imported name is used anywhere in the file as a Name node
            # We need to discount the import statement itself, so we walk non-import nodes
            used = False
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                if isinstance(node, ast.Name) and node.id == imp.name:
                    used = True
                    break
                if isinstance(node, ast.Attribute) and node.attr == imp.name:
                    used = True
                    break
                # Check string constants for forward references
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if imp.name in node.value:
                        used = True
                        break

            if not used:
                # Check for re-export pattern: the name might be accessed via this
                # module from other files (e.g. db.post_message where db.py imports
                # post_message from comms). Check if any other file uses this name
                # as an attribute of this module.
                module_stem = filepath.stem
                reexported = False
                for other_path, (other_tree, _) in file_trees.items():
                    if other_path == filepath or other_tree is None:
                        continue
                    for other_node in ast.walk(other_tree):
                        if (isinstance(other_node, ast.Attribute)
                                and other_node.attr == imp.name
                                and isinstance(other_node.value, ast.Name)
                                and other_node.value.id == module_stem):
                            reexported = True
                            break
                    if reexported:
                        break

                if not reexported:
                    findings.append(Finding(
                        category="UNUSED_IMPORT",
                        name=imp.name,
                        file=str(filepath),
                        line=imp.line,
                        detail=f"imported from {imp.import_module}" if imp.import_module else "",
                    ))

    return findings


def find_unused_definitions(
    root: Path,
    file_trees: dict[Path, tuple[ast.Module, list[str]]],
) -> list[Finding]:
    """Find functions and classes defined but never referenced across the codebase."""
    findings = []

    # Phase 1: Collect all definitions
    all_defs: list[Definition] = []
    for filepath, (tree, source_lines) in file_trees.items():
        if tree is None:
            continue
        all_defs.extend(collect_definitions(filepath, tree, source_lines))

    # Phase 2: Collect all name usages across the entire codebase
    global_usages: set[str] = set()
    per_file_usages: dict[Path, set[str]] = {}
    for filepath, (tree, _) in file_trees.items():
        if tree is None:
            continue
        usages = collect_name_usages(tree)
        per_file_usages[filepath] = usages
        global_usages.update(usages)

    # Phase 3: Check each definition against cross-file usages
    for defn in all_defs:
        if _is_excluded_definition(defn, []):
            continue

        name = defn.name
        defn_path = Path(defn.file)

        # Check if the name is used in ANY other file
        used_elsewhere = False
        for filepath, usages in per_file_usages.items():
            if filepath == defn_path:
                continue  # skip the defining file for cross-file check
            if name in usages:
                used_elsewhere = True
                break

        # Also check if it's used in the same file (self-referencing, recursion, etc.)
        # but NOT at the definition site
        used_in_own_file = False
        own_tree, _ = file_trees.get(defn_path, (None, []))
        if own_tree is not None:
            for node in ast.walk(own_tree):
                if isinstance(node, ast.Name) and node.id == name:
                    # Make sure this isn't the definition itself
                    if node.lineno != defn.line:
                        used_in_own_file = True
                        break
                if isinstance(node, ast.Attribute) and node.attr == name:
                    used_in_own_file = True
                    break
                # Check call sites: obj.name()
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name) and func.id == name and func.lineno != defn.line:
                        used_in_own_file = True
                        break

        if not used_elsewhere and not used_in_own_file:
            category = "UNUSED_FUNCTION" if defn.kind == "function" else "UNUSED_CLASS"
            findings.append(Finding(
                category=category,
                name=name,
                file=defn.file,
                line=defn.line,
            ))

    return findings


# ---------------------------------------------------------------------------
# Graveyard helper
# ---------------------------------------------------------------------------

def write_graveyard(root: Path, findings: list[Finding], file_trees: dict) -> Path:
    """
    Copy dead code to _graveyard/ with a manifest.
    Does NOT delete anything from the original files.
    Returns the graveyard directory path.
    """
    graveyard = root / "_graveyard"
    graveyard.mkdir(exist_ok=True)

    manifest_entries = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Group findings by file
    by_file: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        if f.category != "UNUSED_IMPORT":  # only archive functions/classes
            by_file[f.file].append(f)

    for filepath_str, file_findings in by_file.items():
        filepath = Path(filepath_str)
        try:
            rel = filepath.relative_to(root)
        except ValueError:
            rel = Path(filepath.name)

        # Create graveyard subdirectory mirroring source structure
        dest_dir = graveyard / rel.parent
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / f"{rel.stem}_dead_{timestamp}.py"

        # Extract dead code blocks from the source
        tree, source_lines = file_trees.get(filepath, (None, []))
        if tree is None or not source_lines:
            continue

        # Collect the definitions we need to archive
        archived_defs = []
        all_defs = collect_definitions(filepath, tree, source_lines)
        finding_keys = {(f.name, f.line) for f in file_findings}

        for defn in all_defs:
            if (defn.name, defn.line) in finding_keys and defn.source_lines:
                archived_defs.append(defn)

        if not archived_defs:
            continue

        # Write the graveyard file
        header = (
            f'"""\n'
            f"Dead code archived from {rel}\n"
            f"Scan date: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"Original file preserved — this is a copy only.\n"
            f'"""\n\n'
        )
        body_parts = []
        for defn in archived_defs:
            body_parts.append(f"# Originally at {rel}:{defn.line}")
            body_parts.append("\n".join(defn.source_lines))
            body_parts.append("")  # blank line separator

        dest_file.write_text(header + "\n".join(body_parts), encoding="utf-8")

        for defn in archived_defs:
            manifest_entries.append({
                "name": defn.name,
                "kind": defn.kind,
                "original_file": str(rel),
                "original_line": defn.line,
                "archived_to": str(dest_file.relative_to(root)),
            })

    # Write manifest
    manifest_path = graveyard / f"manifest_{timestamp}.json"
    manifest_path.write_text(
        json.dumps({
            "scan_date": datetime.now().isoformat(),
            "root": str(root),
            "entries": manifest_entries,
        }, indent=2),
        encoding="utf-8",
    )

    return graveyard


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def format_report(findings: list[Finding], root: Path) -> str:
    """Format findings into a human-readable report grouped by category."""
    if not findings:
        return "No dead code detected."

    # Group by category
    by_cat: dict[str, list[Finding]] = defaultdict(list)
    for f in findings:
        by_cat[f.category].append(f)

    lines = []
    lines.append("=" * 70)
    lines.append(f"  Dead Code Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"  Scanned: {root}")
    lines.append("=" * 70)
    lines.append("")

    category_order = ["UNUSED_FUNCTION", "UNUSED_CLASS", "UNUSED_IMPORT"]
    category_labels = {
        "UNUSED_FUNCTION": "Unused Functions",
        "UNUSED_CLASS": "Unused Classes",
        "UNUSED_IMPORT": "Unused Imports",
    }

    total = 0
    for cat in category_order:
        items = by_cat.get(cat, [])
        if not items:
            continue
        total += len(items)

        lines.append(f"--- {category_labels[cat]} ({len(items)}) ---")
        lines.append("")

        # Sort by file, then line
        items.sort(key=lambda f: (f.file, f.line))
        for item in items:
            try:
                rel = str(Path(item.file).relative_to(root))
            except ValueError:
                rel = item.file
            detail = f"  ({item.detail})" if item.detail else ""
            lines.append(f"  {rel}:{item.line}  {item.name}{detail}")

        lines.append("")

    lines.append(f"Total findings: {total}")
    lines.append("")
    lines.append("Exclusions applied: _private, __dunder__, test_*, run(), main(),")
    lines.append("  SKILL_NAME, DESCRIPTION, REQUIRES_NETWORK, @app.route, signal handlers")

    return "\n".join(lines)


def format_json(findings: list[Finding]) -> str:
    """Format findings as JSON."""
    return json.dumps(
        [asdict(f) for f in findings],
        indent=2,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def scan(root: Path) -> list[Finding]:
    """Run the full dead-code scan on a directory. Returns a list of findings."""
    py_files = collect_py_files(root)
    if not py_files:
        return []

    # Parse all files
    file_trees: dict[Path, tuple[Optional[ast.Module], list[str]]] = {}
    for fp in py_files:
        tree, lines = parse_file(fp)
        if tree is not None:
            file_trees[fp] = (tree, lines)

    # Run analyses
    import_findings = find_unused_imports(file_trees)
    def_findings = find_unused_definitions(root, file_trees)

    return import_findings + def_findings


def main():
    parser = argparse.ArgumentParser(
        description="AST-based dead code scanner for fleet codebase",
    )
    parser.add_argument(
        "--path",
        default=None,
        help="Directory to scan (default: fleet/ directory containing this script)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output findings as JSON",
    )
    parser.add_argument(
        "--graveyard",
        action="store_true",
        help="Copy dead code to _graveyard/ directory (does not delete originals)",
    )
    args = parser.parse_args()

    # Determine scan root
    if args.path:
        root = Path(args.path).resolve()
    else:
        root = Path(__file__).parent.resolve()

    if not root.is_dir():
        print(f"Error: {root} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Run scan
    findings = scan(root)

    # Graveyard
    if args.graveyard and findings:
        graveyard_dir = write_graveyard(root, findings, {
            fp: parse_file(fp) for fp in collect_py_files(root)
        })
        archive_count = sum(1 for f in findings if f.category != "UNUSED_IMPORT")
        print(f"Graveyard: {archive_count} definitions archived to {graveyard_dir}/")
        print()

    # Output
    if args.json_output:
        print(format_json(findings))
    else:
        print(format_report(findings, root))

    # Exit code: 0 if clean, 1 if findings
    sys.exit(1 if findings else 0)


if __name__ == "__main__":
    main()
