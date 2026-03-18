"""
Security review skill — scans fleet skill files for security vulnerabilities
and best practice violations, optionally auto-queuing skill_evolve tasks to fix them.

Checks:
  - Path traversal (payload file paths without validation)
  - Command injection (shell=True, unsanitized subprocess args)
  - SQL injection (f-strings in execute())
  - Hardcoded secrets (API keys, tokens in source)
  - Missing timeouts (HTTP calls, subprocess without timeout)
  - Unsafe deserialization (eval, exec, pickle.loads)
  - Input validation gaps (payload fields used without checks)
  - Network exposure (binding to 0.0.0.0)
  - Error information leakage (stack traces in responses)

Payload:
  target        str   specific skill or file to review (default: scan all skills)
  auto_fix      bool  queue skill_evolve tasks for findings (default false)
  severity_min  str   minimum severity to report: "LOW" | "MEDIUM" | "HIGH" | "CRITICAL" (default "LOW")

Output: knowledge/security/reviews/security_review_<date>.md
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
REVIEWS_DIR = KNOWLEDGE_DIR / "security" / "reviews"

SEVERITY_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def _scan_file(path: Path) -> list[dict]:
    """Run all security checks against a single Python file."""
    findings = []
    name = path.name

    try:
        content = path.read_text(errors="ignore")
        lines = content.splitlines()
    except Exception:
        return [{"file": name, "severity": "HIGH", "category": "ACCESS",
                 "line": 0, "detail": f"Cannot read file: {path}"}]

    # Parse AST for structural checks
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        return [{"file": name, "severity": "CRITICAL", "category": "SYNTAX",
                 "line": e.lineno or 0, "detail": f"Syntax error: {e.msg}"}]

    # --- Check: shell=True in subprocess ---
    for i, line in enumerate(lines, 1):
        if "shell=True" in line:
            findings.append({"file": name, "severity": "CRITICAL", "category": "INJECTION",
                             "line": i, "detail": "subprocess with shell=True — command injection risk"})

    # --- Check: eval/exec ---
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func_name = ""
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            if func_name in ("eval", "exec"):
                findings.append({"file": name, "severity": "CRITICAL", "category": "INJECTION",
                                 "line": node.lineno, "detail": f"{func_name}() — arbitrary code execution risk"})
            if func_name == "loads" or (isinstance(node.func, ast.Attribute) and
                                        node.func.attr == "loads" and
                                        isinstance(node.func.value, ast.Name) and
                                        node.func.value.id == "pickle"):
                findings.append({"file": name, "severity": "HIGH", "category": "DESERIALIZATION",
                                 "line": node.lineno, "detail": "pickle.loads — unsafe deserialization"})

    # --- Check: f-string SQL injection ---
    for i, line in enumerate(lines, 1):
        if re.search(r'\.execute\s*\(\s*f["\']', line):
            findings.append({"file": name, "severity": "HIGH", "category": "SQL_INJECTION",
                             "line": i, "detail": "f-string in SQL execute() — use parameterized queries"})

    # --- Check: hardcoded secrets ---
    for i, line in enumerate(lines, 1):
        # Skip comments and docstrings
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        if re.search(r'(?:api_key|token|password|secret|credential)\s*=\s*["\'][A-Za-z0-9]{8,}', line, re.I):
            # Exclude os.environ.get patterns and config references
            if "environ" not in line and "config" not in line and "payload" not in line:
                findings.append({"file": name, "severity": "HIGH", "category": "SECRETS",
                                 "line": i, "detail": "Possible hardcoded secret/credential"})

    # --- Check: missing timeouts ---
    for i, line in enumerate(lines, 1):
        if re.search(r'httpx\.(post|get|put|delete)\(', line):
            # Look ahead 5 lines for timeout
            block = "\n".join(lines[i-1:i+5])
            if "timeout" not in block:
                findings.append({"file": name, "severity": "MEDIUM", "category": "TIMEOUT",
                                 "line": i, "detail": "HTTP request without timeout — can hang indefinitely"})
        if "subprocess.run(" in line or "subprocess.Popen(" in line:
            block = "\n".join(lines[i-1:i+5])
            if "timeout" not in block and "Popen" not in line:  # Popen is long-lived, ok without
                findings.append({"file": name, "severity": "MEDIUM", "category": "TIMEOUT",
                                 "line": i, "detail": "subprocess.run without timeout"})

    # --- Check: path traversal ---
    payload_file_pattern = re.compile(r'payload\.get\s*\(\s*["\'](?:file|path|dir|project_dir|draft_path|target)["\']')
    has_payload_paths = bool(payload_file_pattern.search(content))
    has_traversal_guard = any(x in content for x in ["is_relative_to", "safe_path", "resolve()", "sanitize_filename"])
    if has_payload_paths and not has_traversal_guard:
        findings.append({"file": name, "severity": "MEDIUM", "category": "PATH_TRAVERSAL",
                         "line": 0, "detail": "Accepts file paths from payload without traversal validation — use _security.safe_path()"})

    # --- Check: 0.0.0.0 binding ---
    for i, line in enumerate(lines, 1):
        if "0.0.0.0" in line and "bind" in line.lower() or "host" in line.lower():
            findings.append({"file": name, "severity": "MEDIUM", "category": "NETWORK",
                             "line": i, "detail": "Binds to 0.0.0.0 — accessible from network, use 127.0.0.1 for localhost"})

    # --- Check: error info leakage ---
    for i, line in enumerate(lines, 1):
        if "traceback.format_exc" in line or "traceback.print_exc" in line:
            # Check if it's being returned to users vs logged
            context = "\n".join(lines[max(0,i-3):i+3])
            if "return" in context:
                findings.append({"file": name, "severity": "LOW", "category": "INFO_LEAK",
                                 "line": i, "detail": "Stack trace may be exposed in return value"})

    # --- Check: input validation ---
    # Count payload.get() calls vs validation checks
    payload_gets = len(re.findall(r'payload\.get\s*\(', content))
    validations = len(re.findall(r'if not \w+:|if \w+ is None|if not isinstance', content))
    if payload_gets > 3 and validations == 0:
        findings.append({"file": name, "severity": "LOW", "category": "VALIDATION",
                         "line": 0, "detail": f"Has {payload_gets} payload fields but no input validation"})

    return findings


def run(payload, config):
    import sys
    sys.path.insert(0, str(FLEET_DIR))

    target = payload.get("target", "")
    auto_fix = payload.get("auto_fix", False)
    min_severity = payload.get("severity_min", "LOW")
    min_level = SEVERITY_ORDER.get(min_severity, 0)

    # Determine files to scan
    if target:
        target_path = SKILLS_DIR / f"{target.replace('.py', '')}.py"
        if not target_path.exists():
            # Try as absolute/relative
            target_path = Path(target)
        if target_path.exists():
            files = [target_path]
        else:
            return {"error": f"Target not found: {target}"}
    else:
        # Scan all skills + core fleet files
        files = sorted(SKILLS_DIR.glob("*.py"))
        for core in ["supervisor.py", "worker.py", "db.py", "discord_bot.py", "dashboard.py", "rag.py", "lead_client.py"]:
            p = FLEET_DIR / core
            if p.exists():
                files.append(p)

    # Run scans
    all_findings = []
    for f in files:
        if f.name.startswith("__"):
            continue
        findings = _scan_file(f)
        all_findings.extend(findings)

    # Filter by severity
    filtered = [f for f in all_findings if SEVERITY_ORDER.get(f["severity"], 0) >= min_level]
    filtered.sort(key=lambda x: SEVERITY_ORDER.get(x["severity"], 0), reverse=True)

    # Auto-queue skill_evolve for HIGH+ findings
    fixes_queued = 0
    if auto_fix:
        import db
        # Group by file
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
                    "focus": f"Security fixes: {focus}",
                    "perspective": "code critic / reviewer",
                    "agent_name": "coder_2",
                }),
                priority=8,
                assigned_to="coder_2",
            )
            fixes_queued += 1

    # Save report
    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    report = REVIEWS_DIR / f"security_review_{date_str}.md"

    lines = [
        f"# Security Review — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"**Files scanned:** {len(files)}",
        f"**Findings:** {len(filtered)} (min severity: {min_severity})",
        f"**Auto-fix tasks queued:** {fixes_queued}",
        "",
        "## Summary",
        f"| Severity | Count |",
        f"|----------|-------|",
    ]
    by_sev = {}
    for f in filtered:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1
    for sev in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
        if sev in by_sev:
            lines.append(f"| {sev} | {by_sev[sev]} |")
    lines.append("")

    lines.append("## Findings")
    for f in filtered:
        line_ref = f"line {f['line']}" if f["line"] else "file-level"
        lines.append(f"- **[{f['severity']}]** `{f['file']}` ({line_ref}) — {f['category']}: {f['detail']}")
    lines.append("")

    report.write_text("\n".join(lines))

    return {
        "files_scanned": len(files),
        "findings": filtered[:30],  # cap for payload size
        "total_findings": len(filtered),
        "by_severity": by_sev,
        "auto_fixes_queued": fixes_queued,
        "saved_to": str(report),
    }
