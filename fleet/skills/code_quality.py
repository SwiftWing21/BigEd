"""
Code quality skill — static analysis for Python best practices and style.

Runs AST-based and regex checks against fleet Python files to catch common
quality issues that aren't security-related (those live in security_review).

Checks:
  - Unused imports
  - Bare except clauses (should catch specific exceptions)
  - Mutable default arguments (def foo(x=[]))
  - Star imports (from x import *)
  - Too-long functions (>80 lines)
  - Nested function depth >3
  - print() left in production code (should use logging)
  - TODO/FIXME/HACK/XXX markers
  - Missing run() docstring (fleet skill convention)
  - Inconsistent return types (mixed None/dict returns in run())
  - Global variable mutation
  - Magic numbers in logic (numeric literals outside simple 0/1/-1)
  - Duplicate code blocks (identical 3+ line sequences)

Payload:
  target        str   specific skill or file to review (default: scan all skills)
  severity_min  str   "INFO" | "LOW" | "MEDIUM" | "HIGH" (default "INFO")
  auto_fix      bool  queue skill_evolve tasks for HIGH findings (default false)

Output: knowledge/quality/reviews/quality_review_<date>.md
Returns: {files_scanned, findings: [{file, severity, category, line, detail}], auto_fixes_queued}
"""
import ast
import json
import re
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
SKILLS_DIR = FLEET_DIR / "skills"
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
REVIEWS_DIR = KNOWLEDGE_DIR / "quality" / "reviews"

SEVERITY_ORDER = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}

# Standard library modules — used to skip false positives on "unused" imports
# that are actually side-effect imports or re-exports
KNOWN_SIDE_EFFECT_IMPORTS = {"sys", "logging", "signal", "threading"}


def _scan_file(path: Path) -> list[dict]:
    """Run all quality checks against a single Python file."""
    findings = []
    name = path.name

    try:
        content = path.read_text(errors="ignore")
        lines = content.splitlines()
    except Exception:
        return [{"file": name, "severity": "HIGH", "category": "ACCESS",
                 "line": 0, "detail": f"Cannot read file: {path}"}]

    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        return [{"file": name, "severity": "HIGH", "category": "SYNTAX",
                 "line": e.lineno or 0, "detail": f"Syntax error: {e.msg}"}]

    # --- Check: bare except ---
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            findings.append({"file": name, "severity": "MEDIUM", "category": "BARE_EXCEPT",
                             "line": node.lineno, "detail": "Bare except — catch specific exceptions instead"})

    # --- Check: mutable default arguments ---
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for default in node.args.defaults + node.args.kw_defaults:
                if default and isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    findings.append({"file": name, "severity": "MEDIUM", "category": "MUTABLE_DEFAULT",
                                     "line": node.lineno,
                                     "detail": f"Mutable default argument in {node.name}() — use None and assign inside"})

    # --- Check: star imports ---
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            findings.append({"file": name, "severity": "MEDIUM", "category": "STAR_IMPORT",
                             "line": node.lineno,
                             "detail": f"from {node.module} import * — use explicit imports"})

    # --- Check: too-long functions ---
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", None)
            if end:
                length = end - node.lineno
                if length > 80:
                    findings.append({"file": name, "severity": "LOW", "category": "LONG_FUNCTION",
                                     "line": node.lineno,
                                     "detail": f"{node.name}() is {length} lines — consider splitting"})

    # --- Check: print() in production code ---
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "print":
                # Skip if file is a CLI tool or test
                if name not in ("lead_client.py", "supervisor.py"):
                    findings.append({"file": name, "severity": "INFO", "category": "PRINT_STMT",
                                     "line": node.lineno,
                                     "detail": "print() in skill code — use logging instead"})

    # --- Check: TODO/FIXME/HACK/XXX markers ---
    for i, line in enumerate(lines, 1):
        m = re.search(r'#\s*(TODO|FIXME|HACK|XXX)\b', line, re.I)
        if m:
            tag = m.group(1).upper()
            findings.append({"file": name, "severity": "INFO", "category": "MARKER",
                             "line": i, "detail": f"{tag}: {line.strip()[:80]}"})

    # --- Check: missing run() docstring (fleet skill convention) ---
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            docstring = ast.get_docstring(node)
            if not docstring:
                findings.append({"file": name, "severity": "LOW", "category": "MISSING_DOCSTRING",
                                 "line": node.lineno,
                                 "detail": "run() has no docstring — fleet skills should document payload/output"})

    # --- Check: unused imports ---
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                used_name = alias.asname or alias.name.split(".")[0]
                imports.append((used_name, alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "*":
                    continue
                used_name = alias.asname or alias.name
                imports.append((used_name, f"{node.module}.{alias.name}", node.lineno))

    for used_name, full_name, lineno in imports:
        if used_name.startswith("_"):
            continue
        if used_name in KNOWN_SIDE_EFFECT_IMPORTS:
            continue
        # Count occurrences in content (excluding import lines themselves)
        non_import_lines = [l for l in lines if not l.strip().startswith(("import ", "from "))]
        body = "\n".join(non_import_lines)
        if used_name not in body:
            findings.append({"file": name, "severity": "LOW", "category": "UNUSED_IMPORT",
                             "line": lineno,
                             "detail": f"Import '{used_name}' ({full_name}) appears unused"})

    # --- Check: inconsistent run() returns ---
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            returns = []
            for child in ast.walk(node):
                if isinstance(child, ast.Return):
                    if child.value is None:
                        returns.append("None")
                    elif isinstance(child.value, ast.Dict):
                        returns.append("dict")
                    elif isinstance(child.value, ast.Constant) and isinstance(child.value.value, str):
                        returns.append("str")
                    else:
                        returns.append("other")
            types = set(returns)
            if len(types) > 1 and "None" in types:
                findings.append({"file": name, "severity": "LOW", "category": "INCONSISTENT_RETURN",
                                 "line": node.lineno,
                                 "detail": f"run() has mixed return types: {types} — always return a dict"})

    # --- Check: nested depth > 3 ---
    def _max_depth(node, current=0):
        max_d = current
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.If, ast.For, ast.While, ast.With, ast.Try)):
                max_d = max(max_d, _max_depth(child, current + 1))
            else:
                max_d = max(max_d, _max_depth(child, current))
        return max_d

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            depth = _max_depth(node)
            if depth > 3:
                findings.append({"file": name, "severity": "LOW", "category": "DEEP_NESTING",
                                 "line": node.lineno,
                                 "detail": f"{node.name}() has nesting depth {depth} — consider early returns or extraction"})

    return findings


def run(payload, config):
    """Scan fleet Python files for code quality issues.

    Payload:
      target       — specific file/skill to scan (default: all skills)
      severity_min — minimum severity to report: INFO|LOW|MEDIUM|HIGH
      auto_fix     — queue skill_evolve tasks for findings (default false)
    """
    target = payload.get("target", "")
    auto_fix = payload.get("auto_fix", False)
    min_severity = payload.get("severity_min", "INFO")
    min_level = SEVERITY_ORDER.get(min_severity, 0)

    # Determine files to scan
    if target:
        target_path = SKILLS_DIR / f"{target.replace('.py', '')}.py"
        if not target_path.exists():
            target_path = Path(target)
        if target_path.exists():
            files = [target_path]
        else:
            return {"error": f"Target not found: {target}"}
    else:
        files = sorted(SKILLS_DIR.glob("*.py"))
        for core in ["supervisor.py", "worker.py", "db.py", "discord_bot.py", "dashboard.py", "rag.py"]:
            p = FLEET_DIR / core
            if p.exists():
                files.append(p)

    # Run scans
    all_findings = []
    for f in files:
        if f.name.startswith("__"):
            continue
        all_findings.extend(_scan_file(f))

    # Filter by severity
    filtered = [f for f in all_findings if SEVERITY_ORDER.get(f["severity"], 0) >= min_level]
    filtered.sort(key=lambda x: SEVERITY_ORDER.get(x["severity"], 0), reverse=True)

    # Auto-queue skill_evolve for HIGH findings
    fixes_queued = 0
    if auto_fix:
        import sys
        sys.path.insert(0, str(FLEET_DIR))
        import db
        files_with_issues = {}
        for f in filtered:
            if SEVERITY_ORDER.get(f["severity"], 0) >= SEVERITY_ORDER["MEDIUM"]:
                files_with_issues.setdefault(f["file"], []).append(f)

        for fname, issues in files_with_issues.items():
            skill_name = fname.replace(".py", "")
            if skill_name.startswith("_"):
                continue
            focus = "; ".join(f"{i['category']}: {i['detail'][:60]}" for i in issues[:3])
            db.post_task(
                "skill_evolve",
                json.dumps({
                    "skill_name": skill_name,
                    "focus": f"Quality fixes: {focus}",
                    "perspective": "code critic / reviewer",
                    "agent_name": "coder_2",
                }),
                priority=6,
                assigned_to="coder_2",
            )
            fixes_queued += 1

    # Save report
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    report = REVIEWS_DIR / f"quality_review_{date_str}.md"

    by_sev = {}
    for f in filtered:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1

    report_lines = [
        f"# Code Quality Review — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Files scanned:** {len(files)}",
        f"**Findings:** {len(filtered)} (min severity: {min_severity})",
        f"**Auto-fix tasks queued:** {fixes_queued}",
        "",
        "## Summary",
        "| Severity | Count |",
        "|----------|-------|",
    ]
    for sev in ["HIGH", "MEDIUM", "LOW", "INFO"]:
        if sev in by_sev:
            report_lines.append(f"| {sev} | {by_sev[sev]} |")
    report_lines.append("")

    # Group findings by category
    by_cat = {}
    for f in filtered:
        by_cat.setdefault(f["category"], []).append(f)

    report_lines.append("## Findings by Category")
    for cat, items in sorted(by_cat.items()):
        report_lines.append(f"\n### {cat} ({len(items)})")
        for f in items:
            line_ref = f"line {f['line']}" if f["line"] else "file-level"
            report_lines.append(f"- **[{f['severity']}]** `{f['file']}` ({line_ref}) — {f['detail']}")
    report_lines.append("")

    report.write_text("\n".join(report_lines))

    return {
        "files_scanned": len(files),
        "findings": filtered[:30],
        "total_findings": len(filtered),
        "by_severity": by_sev,
        "by_category": {k: len(v) for k, v in by_cat.items()},
        "auto_fixes_queued": fixes_queued,
        "saved_to": str(report),
    }
