"""
Skill dependency map — scans all skills in fleet/skills/, extracts imports
and DB table references using AST analysis, builds a dependency graph, and
identifies circular dependencies and missing modules.

Output: knowledge/analysis/skill_deps_YYYY-MM-DD.md
"""
import ast
import logging
from collections import defaultdict
from datetime import date
from pathlib import Path

SKILL_NAME = "skill_dependency_map"
DESCRIPTION = "Analyze skill dependencies — imports, DB tables, circular deps, missing modules."
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False
SUITE = ""

FLEET_DIR = Path(__file__).parent.parent
SKILLS_DIR = FLEET_DIR / "skills"
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
ANALYSIS_DIR = KNOWLEDGE_DIR / "analysis"

log = logging.getLogger(__name__)

# Standard library modules to ignore when checking for missing deps
_STDLIB = {
    "abc", "ast", "asyncio", "base64", "bisect", "calendar", "cgi",
    "collections", "concurrent", "configparser", "contextlib", "copy",
    "csv", "ctypes", "dataclasses", "datetime", "decimal", "difflib",
    "email", "enum", "fnmatch", "fractions", "ftplib", "functools",
    "getpass", "glob", "gzip", "hashlib", "heapq", "hmac", "html",
    "http", "importlib", "inspect", "io", "ipaddress", "itertools",
    "json", "keyword", "linecache", "locale", "logging", "lzma",
    "math", "mimetypes", "multiprocessing", "numbers", "operator",
    "os", "pathlib", "pprint", "queue",
    "random", "re", "secrets", "select", "shlex", "shutil", "signal",
    "site", "smtplib", "socket", "sqlite3", "ssl", "stat",
    "statistics", "string", "struct", "subprocess", "sys",
    "tempfile", "textwrap", "threading", "time", "timeit",
    "tkinter", "tomllib", "traceback", "types", "typing",
    "unicodedata", "unittest", "urllib", "uuid", "venv",
    "warnings", "weakref", "webbrowser", "xml", "zipfile", "zlib",
}

# Fleet-internal modules (not skills, but expected to exist)
_FLEET_INTERNAL = {
    "db", "config", "providers", "rag", "data_access", "gpu",
    "mcp_manager", "system_info", "dependency_check", "supervisor",
    "hw_supervisor", "reinforcement", "self_healing", "ml_router",
    "federation_router", "tenant_admin", "compliance", "marketplace",
    "geo_fleet", "sso", "billing", "control_plane", "experiment",
    "view_registry", "views_blueprint", "token_bridge",
    "backup_manager", "cpu_temp", "filesystem_guard", "idle_evolution",
}

# Known third-party packages that are expected
_KNOWN_THIRD_PARTY = {
    "flask", "httpx", "numpy", "pandas", "scipy", "sklearn",
    "scikit_learn", "tomli", "psutil", "requests", "pydantic",
    "cryptography", "jinja2", "yaml", "toml", "dotenv",
    "watchdog", "pillow", "PIL", "discord", "openai", "anthropic",
    "google", "ollama", "bs4", "beautifulsoup4", "lxml",
    "matplotlib", "seaborn", "tqdm",
}


def _extract_imports(tree):
    """Extract all import names from an AST tree."""
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.add(node.module.split(".")[0])
    return imports


def _extract_db_tables(source):
    """Extract SQL table references from source code using pattern matching."""
    import re
    tables = set()
    # Match common SQL patterns: FROM table, INTO table, UPDATE table, JOIN table
    for pattern in [
        r"(?:FROM|INTO|UPDATE|JOIN)\s+(\w+)",
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
    ]:
        for match in re.finditer(pattern, source, re.IGNORECASE):
            table = match.group(1).lower()
            # Filter out SQL keywords and common false positives
            if table not in {"select", "where", "set", "values", "and", "or",
                             "not", "null", "table", "index", "exists", "if"}:
                tables.add(table)
    return tables


def _detect_cycles(graph):
    """Detect circular dependencies using DFS. Returns list of cycles."""
    cycles = []
    visited = set()
    path = []
    path_set = set()

    def dfs(node):
        if node in path_set:
            cycle_start = path.index(node)
            cycles.append(path[cycle_start:] + [node])
            return
        if node in visited:
            return
        visited.add(node)
        path.append(node)
        path_set.add(node)
        for neighbor in graph.get(node, []):
            dfs(neighbor)
        path.pop()
        path_set.discard(node)

    for node in graph:
        dfs(node)

    return cycles


def _scan_skills():
    """Scan all skill files and extract dependency information."""
    skill_data = {}

    for skill_path in sorted(SKILLS_DIR.glob("*.py")):
        if skill_path.name.startswith("__"):
            continue

        skill_name = skill_path.stem
        try:
            source = skill_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(skill_path))

            imports = _extract_imports(tree)
            db_tables = _extract_db_tables(source)

            # Separate imports into categories
            skill_imports = set()
            fleet_imports = set()
            third_party = set()
            stdlib = set()
            unknown = set()

            for imp in imports:
                if imp == "skills" or (SKILLS_DIR / f"{imp}.py").exists():
                    skill_imports.add(imp)
                elif imp in _FLEET_INTERNAL:
                    fleet_imports.add(imp)
                elif imp in _STDLIB:
                    stdlib.add(imp)
                elif imp in _KNOWN_THIRD_PARTY:
                    third_party.add(imp)
                else:
                    unknown.add(imp)

            skill_data[skill_name] = {
                "skill_imports": sorted(skill_imports),
                "fleet_imports": sorted(fleet_imports),
                "third_party": sorted(third_party),
                "stdlib": sorted(stdlib),
                "unknown": sorted(unknown),
                "db_tables": sorted(db_tables),
            }
        except SyntaxError:
            log.warning("Syntax error parsing %s", skill_path.name)
            skill_data[skill_name] = {"error": "SyntaxError"}
        except Exception:
            log.warning("Failed to analyze %s", skill_path.name, exc_info=True)
            skill_data[skill_name] = {"error": "AnalysisError"}

    return skill_data


def _build_report(skill_data):
    """Render the dependency map as Markdown."""
    today = date.today().isoformat()
    lines = [
        f"# Skill Dependency Map — {today}",
        "",
        f"**Skills analyzed:** {len(skill_data)}",
        "",
    ]

    # Build dependency graph for cycle detection
    dep_graph = {}
    for name, data in skill_data.items():
        if "error" not in data:
            dep_graph[name] = data.get("skill_imports", [])

    cycles = _detect_cycles(dep_graph)

    # Collect all unknown imports
    all_unknown = defaultdict(list)
    for name, data in skill_data.items():
        for u in data.get("unknown", []):
            all_unknown[u].append(name)

    # Summary section
    error_skills = [n for n, d in skill_data.items() if "error" in d]
    db_users = [n for n, d in skill_data.items()
                if "db_tables" in d and d["db_tables"]]

    if cycles:
        lines.append("## Circular Dependencies")
        lines.append("")
        for cycle in cycles:
            lines.append(f"- {' -> '.join(cycle)}")
        lines.append("")
    else:
        lines.append("## Circular Dependencies")
        lines.append("")
        lines.append("None detected.")
        lines.append("")

    if all_unknown:
        lines.append("## Potentially Missing Modules")
        lines.append("")
        for mod, users in sorted(all_unknown.items()):
            lines.append(f"- `{mod}` (used by: {', '.join(users)})")
        lines.append("")

    if error_skills:
        lines.append("## Parse Errors")
        lines.append("")
        for name in error_skills:
            lines.append(f"- `{name}`: {skill_data[name]['error']}")
        lines.append("")

    # DB table usage
    if db_users:
        lines.append("## Database Table Usage")
        lines.append("")
        lines.append("| Skill | Tables |")
        lines.append("|-------|--------|")
        for name in sorted(db_users):
            tables = ", ".join(f"`{t}`" for t in skill_data[name]["db_tables"])
            lines.append(f"| `{name}` | {tables} |")
        lines.append("")

    # Full dependency listing
    lines.append("## Full Dependency Listing")
    lines.append("")

    for name in sorted(skill_data.keys()):
        data = skill_data[name]
        if "error" in data:
            continue

        deps = []
        if data["skill_imports"]:
            deps.append(f"skills: {', '.join(data['skill_imports'])}")
        if data["fleet_imports"]:
            deps.append(f"fleet: {', '.join(data['fleet_imports'])}")
        if data["third_party"]:
            deps.append(f"3rd-party: {', '.join(data['third_party'])}")

        dep_str = " | ".join(deps) if deps else "stdlib only"
        lines.append(f"- **{name}** — {dep_str}")

    lines.append("")
    return "\n".join(lines)


def run(payload, config):
    """Analyze skill dependencies and build a dependency map."""
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)

    try:
        skill_data = _scan_skills()
    except Exception:
        log.warning("Failed to scan skills", exc_info=True)
        return {"status": "error", "message": "Failed to scan skills directory"}

    report = _build_report(skill_data)

    today = date.today().isoformat()
    report_path = ANALYSIS_DIR / f"skill_deps_{today}.md"
    try:
        report_path.write_text(report, encoding="utf-8")
    except Exception:
        log.warning("Failed to write dependency report", exc_info=True)
        return {"status": "error", "message": "Failed to write report"}

    # Compute summary stats
    total = len(skill_data)
    errors = sum(1 for d in skill_data.values() if "error" in d)
    with_db = sum(1 for d in skill_data.values()
                  if "db_tables" in d and d["db_tables"])

    dep_graph = {}
    for name, data in skill_data.items():
        if "error" not in data:
            dep_graph[name] = data.get("skill_imports", [])
    cycles = _detect_cycles(dep_graph)

    all_unknown = set()
    for data in skill_data.values():
        all_unknown.update(data.get("unknown", []))

    return {
        "status": "ok",
        "saved_to": str(report_path),
        "skills_analyzed": total,
        "parse_errors": errors,
        "skills_using_db": with_db,
        "circular_deps": len(cycles),
        "unknown_modules": len(all_unknown),
    }
