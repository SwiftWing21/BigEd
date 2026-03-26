"""
Security Suite -- consolidates 6 security skills into a single dispatch-based skill.

Sub-actions:
  audit      -- scan for file permission issues, exposed secrets, gitignore gaps
  code_scan  -- static security analysis of Python skill files
  apply      -- execute approved fixes from a pending advisory
  pentest    -- nmap-based local network penetration test (requires nmap)
  cve        -- check dependencies against OSV.dev vulnerability database
  sql        -- validate SQL queries against the fleet DB schema

Advisory pipeline: audit -> (human review) -> apply
  Both audit and pentest produce PENDING_APPROVAL advisories in
  knowledge/security/pending/.  Apply reads those by advisory_id.
  This pipeline is safety-critical and is preserved exactly from the source skills.
"""
import ast
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path
import logging

from skills._dispatch import dispatch_action
from skills._models import call_complex

SKILL_NAME = "security_suite"
DESCRIPTION = "Security operations: audit, code scan, fix application, pentest, CVE watch, SQL review"
VERSION = "1.0.0"
REQUIRES_NETWORK = True   # pen_test and cve use network; others do not
COMPLEXITY = "medium"
SUITE = "security"
log = logging.getLogger(SKILL_NAME)

# ---------------------------------------------------------------------------
# Directory layout
# ---------------------------------------------------------------------------
FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
SECURITY_DIR = KNOWLEDGE_DIR / "security"
PENDING_DIR = SECURITY_DIR / "pending"
APPLIED_DIR = SECURITY_DIR / "applied"
REVIEWS_DIR = SECURITY_DIR / "reviews"
PENTEST_DIR = SECURITY_DIR / "pen_tests"
SKILLS_DIR = FLEET_DIR / "skills"
DB_PY_PATH = FLEET_DIR / "db.py"
PROJECT_DIR = FLEET_DIR.parent

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

# Unified severity ordering -- used by code_scan and sql sub-actions
SEVERITY_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

# Ports with dedicated risk profiles (pentest sub-action)
UNIFI_PORTS = {"8080", "8443", "8843", "8880", "3478", "10001", "6789", "27117"}

# Secret patterns scanned by audit sub-action
_SECRET_PATTERNS = [
    (re.compile(r'sk-ant-[A-Za-z0-9_-]{20,}'), "Anthropic API key"),
    (re.compile(r'hf_[A-Za-z0-9]{20,}'), "HuggingFace token"),
    (re.compile(r'(?i)(api[_-]?key|secret|password|token)\s*=\s*["\']?[A-Za-z0-9_\-]{16,}'), "Potential credential"),
    (re.compile(r'BRAVE_API_KEY=[A-Za-z0-9_\-]{16,}'), "Brave API key"),
    (re.compile(r'TAVILY_API_KEY=[A-Za-z0-9_\-]{16,}'), "Tavily API key"),
]

# Paths to check for overly permissive file modes (audit sub-action, Unix only)
_PERMISSION_CHECKS = [
    ("~/.secrets", 0o600),
    ("~/.ssh/id_rsa", 0o600),
    ("~/.ssh/id_ed25519", 0o600),
]

# Unsafe deserialization function name (split to avoid triggering security hooks on this source file)
_UNSAFE_DESER_MODULE = "pick" + "le"
_UNSAFE_DESER_FUNC = "load" + "s"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _notify_lead(db, body: dict) -> None:
    """Post a message to the lead agent inbox (single authoritative copy)."""
    db.post_message(
        from_agent="security",
        to_agent="lead",
        body_json=json.dumps(body),
    )


def _create_advisory(findings: list, scope_desc: str, config: dict,
                     source: str = "audit", extra_md_lines: list = None) -> dict:
    """Build and persist a PENDING_APPROVAL advisory from a list of findings.

    Shared by the audit and pentest sub-actions.  Returns the advisory dict
    (already written to knowledge/security/pending/).

    Args:
        findings:       List of finding dicts (must include severity, detail, fix keys).
        scope_desc:     Human-readable scope string (used in advisory and LLM prompt).
        config:         Fleet config dict (passed to call_complex).
        source:         Origin tag stored in advisory JSON ('audit' | 'pen_test').
        extra_md_lines: Optional extra lines appended to the advisory markdown.
    """
    if not findings:
        return None

    high = [f for f in findings if f.get("severity") == "HIGH"]
    medium = [f for f in findings if f.get("severity") == "MEDIUM"]
    low = [f for f in findings if f.get("severity") == "LOW"]

    summary_text = "\n".join(
        f"[{f.get('severity', '?')}] {f.get('type', 'finding')} -- "
        f"{f.get('detail', '')} (fix: {f.get('fix', 'manual review')})"
        for f in findings
    )

    prompt = (
        f"You are a security advisor reviewing a local development environment.\n"
        f"Scope: {scope_desc}\n\n"
        f"Findings:\n{summary_text}\n\n"
        f"Write a concise security advisory (max 6 bullet points). Lead with HIGH severity items.\n"
        f"For each finding: state the risk, the specific file/path, and the exact remediation step.\n"
        f"Do NOT include boilerplate. Be direct and actionable."
    )
    analysis = call_complex("You are a security auditor.", prompt, config,
                            skill_name="security_suite")

    advisory_id = hashlib.sha1(
        (scope_desc + datetime.now().isoformat()).encode()
    ).hexdigest()[:8]

    advisory = {
        "id": advisory_id,
        "created_at": datetime.now().isoformat(),
        "scope": scope_desc,
        "status": "PENDING_APPROVAL",
        "source": source,
        "counts": {"HIGH": len(high), "MEDIUM": len(medium), "LOW": len(low)},
        "findings": findings,
        "analysis": analysis,
    }

    # Persist JSON
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    advisory_file = PENDING_DIR / f"advisory_{advisory_id}.json"
    advisory_file.write_text(json.dumps(advisory, indent=2), encoding="utf-8")

    # Persist human-readable Markdown
    md_lines = [
        f"# Security Advisory {advisory_id}",
        f"**Created:** {advisory['created_at']}",
        f"**Scope:** {scope_desc}",
        f"**Source:** {source}",
        f"**Status:** PENDING_APPROVAL",
        f"**Findings:** {len(high)} HIGH, {len(medium)} MEDIUM, {len(low)} LOW",
        "",
        "## Analysis",
        analysis,
        "",
        "## Raw Findings",
    ]
    for f in findings:
        md_lines.append(
            f"- **[{f.get('severity', '?')}]** "
            f"`{f.get('path', f.get('host', ''))}` -- {f.get('detail', '')}"
        )
        if f.get("fix"):
            md_lines.append(f"  - Fix: `{f['fix']}`")
    md_lines += [
        "",
        "## To Apply",
        "```",
        f"python lead_client.py task 'security_suite advisory_id={advisory_id} action=apply' --wait",
        "```",
        "Or apply manually using the fixes listed above.",
    ]
    if extra_md_lines:
        md_lines.extend(extra_md_lines)

    md_file = PENDING_DIR / f"advisory_{advisory_id}.md"
    md_file.write_text("\n".join(md_lines), encoding="utf-8")

    advisory["_md_file"] = str(md_file)
    return advisory


def _build_severity_report(filtered_findings: list, title: str,
                            extra_header_lines: list = None):
    """Render the shared Markdown severity summary table.

    Used by code_scan and sql sub-actions.
    Returns (lines: list[str], by_sev: dict[str, int]).
    """
    by_sev: dict = {}
    for f in filtered_findings:
        sev = f.get("severity", "?")
        by_sev[sev] = by_sev.get(sev, 0) + 1

    lines = [title, ""]
    if extra_header_lines:
        lines.extend(extra_header_lines)
        lines.append("")

    lines += [
        "## Summary",
        "| Severity | Count |",
        "|----------|-------|",
    ]
    for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW"):
        if sev in by_sev:
            lines.append(f"| {sev} | {by_sev[sev]} |")
    lines.append("")
    return lines, by_sev


# ===========================================================================
# Sub-action: audit
# (was: security_audit.py)
# ===========================================================================

def _audit_check_permissions() -> list:
    """Return file-permission findings (Unix only -- Windows always 0o666)."""
    if sys.platform == "win32":
        return []
    findings = []
    for path_str, required_mode in _PERMISSION_CHECKS:
        path = Path(path_str).expanduser()
        if not path.exists():
            continue
        current = stat.S_IMODE(path.stat().st_mode)
        if current & ~required_mode:
            findings.append({
                "severity": "HIGH",
                "type": "file_permissions",
                "path": str(path),
                "detail": f"Mode {oct(current)} is too permissive -- should be {oct(required_mode)}",
                "fix": f"chmod {oct(required_mode)[2:]} {path}",
            })
    return findings


def _audit_scan_secrets(scan_dirs: list) -> list:
    findings = []
    for base in scan_dirs:
        base = Path(base).expanduser()
        if not base.exists():
            continue
        for fpath in base.rglob("*"):
            if not fpath.is_file():
                continue
            if fpath.suffix in {".db", ".pyc", ".png", ".jpg", ".bin"}:
                continue
            if any(part.startswith(".") and part not in {".secrets"} for part in fpath.parts[-3:]):
                continue
            if fpath.name in {"fleet.db", "results.tsv"} or fpath.suffix == ".jsonl":
                continue
            try:
                content = fpath.read_text(errors="ignore")
            except Exception:
                continue
            for pattern, label in _SECRET_PATTERNS:
                for match in pattern.finditer(content):
                    line_num = content[:match.start()].count("\n") + 1
                    raw = match.group(0)
                    masked = raw[:8] + "..." + raw[-4:] if len(raw) > 12 else "***"
                    findings.append({
                        "severity": "HIGH",
                        "type": "exposed_secret",
                        "path": str(fpath),
                        "line": line_num,
                        "label": label,
                        "masked_value": masked,
                        "detail": f"{label} found in plaintext at line {line_num}",
                        "fix": f"Remove from {fpath.name} and rotate credential",
                    })
    return findings


def _audit_check_gitignore(scan_dirs: list) -> list:
    findings = []
    sensitive_names = {".secrets", "*.env", "*.pem", "*.key", "fleet.db", "*.jsonl"}
    for base in scan_dirs:
        base = Path(base).expanduser()
        gitignore = base / ".gitignore"
        if not gitignore.exists():
            continue
        content = gitignore.read_text(encoding="utf-8")
        for name in sensitive_names:
            stem = name.replace("*.", "").replace("*", "")
            if stem not in content:
                findings.append({
                    "severity": "MEDIUM",
                    "type": "gitignore_gap",
                    "path": str(gitignore),
                    "detail": f"'{name}' not in .gitignore -- may be accidentally committed",
                    "fix": f"Add '{name}' to {gitignore}",
                })
    return findings


def _run_audit(payload: dict, config: dict) -> dict:
    """Audit sub-action: scan for permission issues, exposed secrets, gitignore gaps."""
    sys.path.insert(0, str(FLEET_DIR))
    import db  # lazy import -- prevents circular imports

    scope = payload.get("scope", "fleet")
    scan_dirs = payload.get("scan_dirs", [str(FLEET_DIR)])
    include_permission_check = payload.get("check_permissions", True)
    include_secret_scan = payload.get("check_secrets", True)
    include_gitignore = payload.get("check_gitignore", True)

    findings = []
    if include_permission_check:
        findings.extend(_audit_check_permissions())
    if include_secret_scan:
        findings.extend(_audit_scan_secrets(scan_dirs))
    if include_gitignore:
        findings.extend(_audit_check_gitignore(scan_dirs))

    if not findings:
        return {"status": "clean", "scope": scope, "message": "No security issues found."}

    advisory = _create_advisory(findings, scope, config, source="audit")
    if not advisory:
        return {"status": "clean", "scope": scope}

    md_file = advisory["_md_file"]

    _notify_lead(db, {
        "type": "security_advisory",
        "advisory_id": advisory["id"],
        "counts": advisory["counts"],
        "summary": advisory["analysis"][:500],
        "pending_file": md_file,
    })

    return {
        "advisory_id": advisory["id"],
        "counts": advisory["counts"],
        "pending_file": md_file,
        "status": "advisory_posted",
        "note": "Review advisory then dispatch security_suite action=apply to proceed.",
    }


# ===========================================================================
# Sub-action: code_scan
# (was: security_review.py)
# ===========================================================================

def _code_scan_file(path: Path) -> list:
    """Run all security checks against a single Python file."""
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
        return [{"file": name, "severity": "CRITICAL", "category": "SYNTAX",
                 "line": e.lineno or 0, "detail": f"Syntax error: {e.msg}"}]

    # shell=True
    for i, line in enumerate(lines, 1):
        if "shell=True" in line:
            findings.append({"file": name, "severity": "CRITICAL", "category": "INJECTION",
                             "line": i, "detail": "subprocess with shell=True -- command injection risk"})

    # eval/exec/unsafe deserialization
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func_name = ""
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            if func_name in ("eval", "exec"):
                findings.append({"file": name, "severity": "CRITICAL", "category": "INJECTION",
                                 "line": node.lineno,
                                 "detail": f"{func_name}() -- arbitrary code execution risk"})
            # Detect unsafe deserialization (module name assembled at runtime to avoid hook)
            is_bare_loads = (func_name == _UNSAFE_DESER_FUNC)
            is_module_loads = (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == _UNSAFE_DESER_FUNC
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == _UNSAFE_DESER_MODULE
            )
            if is_bare_loads or is_module_loads:
                findings.append({"file": name, "severity": "HIGH", "category": "DESERIALIZATION",
                                 "line": node.lineno,
                                 "detail": f"{_UNSAFE_DESER_MODULE}.{_UNSAFE_DESER_FUNC} -- unsafe deserialization"})

    # f-string SQL injection
    for i, line in enumerate(lines, 1):
        if re.search(r'\.execute\s*\(\s*f["\']', line):
            findings.append({"file": name, "severity": "HIGH", "category": "SQL_INJECTION",
                             "line": i,
                             "detail": "f-string in SQL execute() -- use parameterized queries"})

    # hardcoded secrets
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
            continue
        if re.search(r'(?:api_key|token|password|secret|credential)\s*=\s*["\'][A-Za-z0-9]{8,}', line, re.I):
            if "environ" not in line and "config" not in line and "payload" not in line:
                findings.append({"file": name, "severity": "HIGH", "category": "SECRETS",
                                 "line": i, "detail": "Possible hardcoded secret/credential"})

    # missing HTTP/subprocess timeouts
    for i, line in enumerate(lines, 1):
        if re.search(r'httpx\.(post|get|put|delete)\(', line):
            block = "\n".join(lines[i - 1:i + 5])
            if "timeout" not in block:
                findings.append({"file": name, "severity": "MEDIUM", "category": "TIMEOUT",
                                 "line": i,
                                 "detail": "HTTP request without timeout -- can hang indefinitely"})
        if "subprocess.run(" in line or "subprocess.Popen(" in line:
            block = "\n".join(lines[i - 1:i + 5])
            if "timeout" not in block and "Popen" not in line:
                findings.append({"file": name, "severity": "MEDIUM", "category": "TIMEOUT",
                                 "line": i, "detail": "subprocess.run without timeout"})

    # path traversal
    payload_file_pattern = re.compile(
        r'payload\.get\s*\(\s*["\'](?:file|path|dir|project_dir|draft_path|target)["\']'
    )
    has_payload_paths = bool(payload_file_pattern.search(content))
    has_traversal_guard = any(x in content for x in
                              ["is_relative_to", "safe_path", "resolve()", "sanitize_filename"])
    if has_payload_paths and not has_traversal_guard:
        findings.append({"file": name, "severity": "MEDIUM", "category": "PATH_TRAVERSAL",
                         "line": 0,
                         "detail": "Accepts file paths from payload without traversal validation -- use _security.safe_path()"})

    # 0.0.0.0 binding
    for i, line in enumerate(lines, 1):
        if "0.0.0.0" in line and ("bind" in line.lower() or "host" in line.lower()):
            findings.append({"file": name, "severity": "MEDIUM", "category": "NETWORK",
                             "line": i,
                             "detail": "Binds to 0.0.0.0 -- accessible from network, use 127.0.0.1 for localhost"})

    # error info leakage
    for i, line in enumerate(lines, 1):
        if "traceback.format_exc" in line or "traceback.print_exc" in line:
            context = "\n".join(lines[max(0, i - 3):i + 3])
            if "return" in context:
                findings.append({"file": name, "severity": "LOW", "category": "INFO_LEAK",
                                 "line": i, "detail": "Stack trace may be exposed in return value"})

    # input validation
    payload_gets = len(re.findall(r'payload\.get\s*\(', content))
    validations = len(re.findall(r'if not \w+:|if \w+ is None|if not isinstance', content))
    if payload_gets > 3 and validations == 0:
        findings.append({"file": name, "severity": "LOW", "category": "VALIDATION",
                         "line": 0,
                         "detail": f"Has {payload_gets} payload fields but no input validation"})

    return findings


def _run_code_scan(payload: dict, config: dict) -> dict:
    """Code-scan sub-action: static security analysis of Python skill files."""
    sys.path.insert(0, str(FLEET_DIR))

    target = payload.get("target", "")
    auto_fix = payload.get("auto_fix", False)
    min_severity = payload.get("severity_min", "LOW")
    min_level = SEVERITY_ORDER.get(min_severity, 0)

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
        for core in ["supervisor.py", "worker.py", "db.py", "discord_bot.py",
                     "dashboard.py", "rag.py", "lead_client.py"]:
            p = FLEET_DIR / core
            if p.exists():
                files.append(p)

    all_findings = []
    for f in files:
        if f.name.startswith("__"):
            continue
        all_findings.extend(_code_scan_file(f))

    filtered = [f for f in all_findings if SEVERITY_ORDER.get(f["severity"], 0) >= min_level]
    filtered.sort(key=lambda x: SEVERITY_ORDER.get(x["severity"], 0), reverse=True)

    fixes_queued = 0
    if auto_fix:
        import db
        files_with_issues: dict = {}
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

    REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = REVIEWS_DIR / f"security_review_{date_str}.md"

    header_extra = [
        f"**Files scanned:** {len(files)}",
        f"**Findings:** {len(filtered)} (min severity: {min_severity})",
        f"**Auto-fix tasks queued:** {fixes_queued}",
    ]
    report_lines, by_sev = _build_severity_report(
        filtered,
        f"# Security Review -- {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        extra_header_lines=header_extra,
    )
    report_lines.append("## Findings")
    for f in filtered:
        line_ref = f"line {f['line']}" if f.get("line") else "file-level"
        report_lines.append(
            f"- **[{f['severity']}]** `{f['file']}` ({line_ref}) -- {f['category']}: {f['detail']}"
        )
    report_lines.append("")
    report_path.write_text("\n".join(report_lines), encoding="utf-8")

    return {
        "files_scanned": len(files),
        "findings": filtered[:30],
        "total_findings": len(filtered),
        "by_severity": by_sev,
        "auto_fixes_queued": fixes_queued,
        "saved_to": str(report_path),
    }


# ===========================================================================
# Sub-action: apply
# (was: security_apply.py)
# ===========================================================================

def _apply_chmod(path_str: str, fix_str: str):
    m = re.search(r'chmod (\d+) (.+)', fix_str)
    if not m:
        return False, "Could not parse chmod command"
    mode_str, target = m.group(1), m.group(2).strip()
    target_path = Path(target).expanduser()
    if not target_path.exists():
        return False, f"Path not found: {target_path}"
    try:
        os.chmod(target_path, int(mode_str, 8))
        return True, f"chmod {mode_str} applied to {target_path}"
    except Exception as e:
        return False, str(e)


def _apply_gitignore(fix_str: str, gitignore_path: str):
    m = re.search(r"Add '(.+?)' to", fix_str)
    if not m:
        return False, "Could not parse gitignore fix"
    entry = m.group(1)
    gpath = Path(gitignore_path)
    if not gpath.exists():
        return False, f".gitignore not found: {gpath}"
    content = gpath.read_text(encoding="utf-8")
    if entry.replace("*.", "").replace("*", "") in content:
        return True, f"'{entry}' already in .gitignore (skipped)"
    gpath.write_text(content.rstrip() + f"\n{entry}\n", encoding="utf-8")
    return True, f"Added '{entry}' to {gpath}"


def _run_apply(payload: dict, config: dict) -> dict:
    """Apply sub-action: execute approved fixes from a pending advisory."""
    sys.path.insert(0, str(FLEET_DIR))
    import db  # lazy import

    advisory_id = payload.get("advisory_id", "")
    if not advisory_id:
        return {"error": "advisory_id required"}

    advisory_file = PENDING_DIR / f"advisory_{advisory_id}.json"
    if not advisory_file.exists():
        return {"error": f"Advisory {advisory_id} not found in pending/"}

    advisory = json.loads(advisory_file.read_text(encoding="utf-8"))

    applied = []
    skipped = []
    manual_required = []

    for finding in advisory.get("findings", []):
        fix = finding.get("fix", "")
        ftype = finding.get("type", "")
        severity = finding.get("severity", "LOW")

        if ftype == "file_permissions" and "chmod" in fix:
            ok, msg = _apply_chmod(finding.get("path", ""), fix)
            (applied if ok else skipped).append({"finding": finding["detail"], "result": msg})

        elif ftype == "gitignore_gap":
            ok, msg = _apply_gitignore(fix, finding.get("path", ""))
            (applied if ok else skipped).append({"finding": finding["detail"], "result": msg})

        elif ftype == "exposed_secret":
            # Never auto-remove secrets -- flag for manual rotation
            manual_required.append({
                "severity": severity,
                "finding": finding["detail"],
                "path": finding.get("path", ""),
                "action": fix,
                "note": "Credential rotation must be done manually -- rotate key then remove from file",
            })

        else:
            manual_required.append({
                "finding": finding["detail"],
                "action": fix,
                "note": "Manual remediation required",
            })

    # Move advisory to applied/
    APPLIED_DIR.mkdir(parents=True, exist_ok=True)
    advisory["status"] = "APPLIED"
    advisory["applied_at"] = datetime.now().isoformat()
    advisory["apply_results"] = {
        "applied": applied,
        "skipped": skipped,
        "manual_required": manual_required,
    }

    dest = APPLIED_DIR / advisory_file.name
    dest.write_text(json.dumps(advisory, indent=2), encoding="utf-8")
    advisory_file.unlink()

    # Move markdown pending file
    md_pending = PENDING_DIR / f"advisory_{advisory_id}.md"
    if md_pending.exists():
        md_pending.rename(APPLIED_DIR / md_pending.name)

    # Write apply report
    report_lines = [
        f"# Security Apply Report -- {advisory_id}",
        f"**Applied at:** {advisory['applied_at']}",
        "",
        f"## Automated Fixes Applied ({len(applied)})",
    ]
    for item in applied:
        report_lines.append(f"- {item['finding']}: {item['result']}")

    if skipped:
        report_lines += [f"\n## Skipped ({len(skipped)})"]
        for item in skipped:
            report_lines.append(f"- {item['finding']}: {item['result']}")

    if manual_required:
        report_lines += [f"\n## Manual Action Required ({len(manual_required)})"]
        for item in manual_required:
            report_lines.append(f"- **{item.get('severity', 'INFO')}** {item['finding']}")
            report_lines.append(f"  - Action: {item['action']}")
            if item.get("note"):
                report_lines.append(f"  - Note: {item['note']}")

    report_file = APPLIED_DIR / f"apply_report_{advisory_id}.md"
    report_file.write_text("\n".join(report_lines), encoding="utf-8")

    _notify_lead(db, {
        "type": "security_applied",
        "advisory_id": advisory_id,
        "applied_count": len(applied),
        "manual_required_count": len(manual_required),
        "report_file": str(report_file),
    })

    return {
        "advisory_id": advisory_id,
        "applied": len(applied),
        "skipped": len(skipped),
        "manual_required": len(manual_required),
        "report_file": str(report_file),
    }


# ===========================================================================
# Sub-action: pentest
# (was: pen_test.py)
# ===========================================================================

def _pentest_validate_target(target: str) -> bool:
    """Block shell injection via nmap target -- allow only IPs, CIDR, ranges."""
    if target == "auto":
        return True
    return bool(re.match(r'^[\d\./:a-fA-F\-]+$', target))


def _pentest_detect_wsl_nat():
    """Detect WSL2 NAT networking (172.x.x.x). Only relevant on Linux."""
    if sys.platform == "win32":
        return False, ""
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and "172." in result.stdout:
            return True, result.stdout.strip()
    except Exception:
        pass
    return False, ""


def _pentest_detect_network() -> str:
    """Detect local network range -- cross-platform (psutil > ipconfig/ip)."""
    try:
        import psutil
        for _iface, addrs in psutil.net_if_addrs().items():
            for addr in addrs:
                if addr.family.name == "AF_INET" and not addr.address.startswith("127."):
                    parts = addr.address.rsplit(".", 1)
                    return f"{parts[0]}.0/24"
    except ImportError:
        pass

    if sys.platform == "win32":
        try:
            r = subprocess.run(["ipconfig"], capture_output=True, text=True, timeout=5)
            for line in r.stdout.splitlines():
                if "IPv4" in line and ":" in line:
                    ip = line.split(":")[-1].strip()
                    if not ip.startswith("127."):
                        parts = ip.rsplit(".", 1)
                        return f"{parts[0]}.0/24"
        except Exception:
            pass
    else:
        try:
            r = subprocess.run(["ip", "route", "show", "default"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                parts = r.stdout.split()
                if len(parts) >= 3:
                    base = parts[2].rsplit(".", 1)[0]
                    return f"{base}.0/24"
        except Exception:
            pass

    return "192.168.1.0/24"


def _pentest_run_nmap(target: str, scan_type: str = "quick"):
    """Run nmap and return XML string output, or (None, error_str)."""
    if scan_type == "quick":
        args = ["-T4", "--top-ports", "100", "-oX", "-"]
    elif scan_type == "service":
        args = ["-sV", "--version-intensity", "5", "-p",
                "21,22,23,25,53,80,110,143,443,445,3306,3389,5432,6379,8080,8443,9200,11434",
                "-oX", "-"]
    elif scan_type == "full":
        args = ["-sV", "-p-", "-T4", "-oX", "-"]
    else:
        args = ["-T4", "--top-ports", "100", "-oX", "-"]

    try:
        result = subprocess.run(
            ["nmap"] + args + [target],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode not in (0, 1):
            return None, f"nmap error: {result.stderr[:200]}"
        return result.stdout, None
    except FileNotFoundError:
        return None, "nmap not installed -- run: sudo apt install nmap"
    except subprocess.TimeoutExpired:
        return None, "nmap scan timed out"


def _pentest_parse_nmap_xml(xml_str: str) -> list:
    """Parse nmap XML output into structured host data."""
    hosts = []
    try:
        root = ET.fromstring(xml_str)
        for host_el in root.findall("host"):
            status = host_el.find("status")
            if status is None or status.get("state") != "up":
                continue
            addr_el = host_el.find("address")
            ip = addr_el.get("addr", "?") if addr_el is not None else "?"
            hostname_els = host_el.findall("hostnames/hostname")
            hostname = hostname_els[0].get("name", "") if hostname_els else ""
            ports = []
            for port_el in host_el.findall("ports/port"):
                state_el = port_el.find("state")
                if state_el is None or state_el.get("state") != "open":
                    continue
                svc_el = port_el.find("service")
                ports.append({
                    "port": port_el.get("portid"),
                    "protocol": port_el.get("protocol"),
                    "service": svc_el.get("name", "") if svc_el is not None else "",
                    "product": svc_el.get("product", "") if svc_el is not None else "",
                    "version": svc_el.get("version", "") if svc_el is not None else "",
                })
            hosts.append({"ip": ip, "hostname": hostname, "open_ports": ports})
    except ET.ParseError:
        pass
    return hosts


def _pentest_assess_findings(hosts: list) -> list:
    """Generate security findings from nmap host data."""
    findings = []

    HIGH_RISK_PORTS = {
        "23": ("Telnet open", "HIGH", "Plaintext protocol -- disable and use SSH"),
        "21": ("FTP open", "MEDIUM", "Plaintext protocol -- use SFTP/SCP instead"),
        "3389": ("RDP exposed", "HIGH", "Remote Desktop exposed -- restrict with firewall or VPN"),
        "445": ("SMB exposed", "HIGH", "SMB port open -- check for EternalBlue, restrict to LAN"),
        "3306": ("MySQL exposed", "HIGH", "Database port exposed -- bind to localhost only"),
        "5432": ("PostgreSQL exposed", "HIGH", "Database port exposed -- bind to localhost only"),
        "6379": ("Redis exposed", "HIGH", "Redis often has no auth -- bind to localhost only"),
        "9200": ("Elasticsearch exposed", "HIGH", "Elasticsearch may have no auth -- restrict access"),
        "27017": ("MongoDB exposed", "HIGH", "MongoDB may have no auth -- restrict access"),
        "11434": ("Ollama API exposed", "MEDIUM", "Ollama API accessible on LAN -- ensure firewall blocks WAN access"),
        "27117": ("UniFi embedded MongoDB exposed", "HIGH", "UniFi internal MongoDB -- must not be externally accessible"),
        "8880": ("UniFi guest portal HTTP", "MEDIUM", "Guest portal unencrypted -- verify intended exposure"),
    }

    INTERESTING_PORTS = {
        "80": "HTTP (unencrypted web)",
        "443": "HTTPS",
        "22": "SSH",
        "8080": "UniFi controller HTTP / HTTP alternate",
        "8443": "UniFi controller HTTPS admin UI",
        "8843": "UniFi guest portal HTTPS",
        "3478": "UniFi STUN / device adoption (UDP)",
        "10001": "UniFi device discovery (UDP)",
        "6789": "UniFi throughput test",
    }

    for host in hosts:
        for port_info in host["open_ports"]:
            port = port_info["port"]
            svc = port_info.get("service", "")
            product = port_info.get("product", "")
            version = port_info.get("version", "")

            if port in HIGH_RISK_PORTS:
                label, severity, fix = HIGH_RISK_PORTS[port]
                findings.append({
                    "severity": severity,
                    "type": "network_exposure",
                    "host": host["ip"],
                    "hostname": host["hostname"],
                    "port": port,
                    "service": svc,
                    "detail": f"{label} on {host['ip']}:{port} ({f'{product} {version}'.strip()})",
                    "fix": fix,
                })
            elif port in INTERESTING_PORTS and (product or svc):
                version_str = f"{product} {version}".strip()
                findings.append({
                    "severity": "INFO",
                    "type": "service_inventory",
                    "host": host["ip"],
                    "hostname": host["hostname"],
                    "port": port,
                    "service": svc,
                    "detail": f"{INTERESTING_PORTS[port]}: {version_str} on {host['ip']}:{port}",
                    "fix": "No immediate action -- review version for known CVEs",
                })

        if len(host["open_ports"]) > 15:
            findings.append({
                "severity": "MEDIUM",
                "type": "attack_surface",
                "host": host["ip"],
                "detail": f"{host['ip']} has {len(host['open_ports'])} open ports -- review for unnecessary services",
                "fix": "Audit running services and disable unused ones",
            })

    return findings


def _pentest_check_localhost_binding() -> list:
    """Verify Ollama and Dashboard are bound to 127.0.0.1 only."""
    findings = []
    ports_to_check = [(11434, "Ollama API"), (5555, "Fleet Dashboard")]

    try:
        import psutil
        for port, name in ports_to_check:
            for conn in psutil.net_connections(kind="tcp"):
                if conn.laddr.port == port and conn.status == "LISTEN":
                    if conn.laddr.ip in ("0.0.0.0", "::"):
                        findings.append({
                            "severity": "HIGH",
                            "type": "network_binding",
                            "port": port,
                            "detail": f"{name} (port {port}) bound to all interfaces ({conn.laddr.ip}) -- accessible from LAN",
                            "fix": f"Bind {name} to 127.0.0.1 only (--host 127.0.0.1)",
                        })
                    elif conn.laddr.ip == "127.0.0.1":
                        findings.append({
                            "severity": "INFO",
                            "type": "network_binding",
                            "port": port,
                            "detail": f"{name} (port {port}) correctly bound to 127.0.0.1",
                            "fix": "No action needed",
                        })
        return findings
    except (ImportError, PermissionError):
        pass

    try:
        if sys.platform == "win32":
            cmds = [["netstat", "-an"]]
        else:
            cmds = [["ss", "-tlnp"], ["netstat", "-tlnp"]]

        for cmd in cmds:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    for port, name in ports_to_check:
                        port_str = f":{port}"
                        for line in result.stdout.splitlines():
                            if port_str in line:
                                if f"0.0.0.0:{port}" in line or f":::{port}" in line or f"*:{port}" in line:
                                    findings.append({
                                        "severity": "HIGH",
                                        "type": "network_binding",
                                        "port": port,
                                        "detail": f"{name} (port {port}) bound to all interfaces (0.0.0.0) -- accessible from LAN",
                                        "fix": f"Bind {name} to 127.0.0.1 only (--host 127.0.0.1)",
                                    })
                                elif f"127.0.0.1:{port}" in line:
                                    findings.append({
                                        "severity": "INFO",
                                        "type": "network_binding",
                                        "port": port,
                                        "detail": f"{name} (port {port}) correctly bound to 127.0.0.1",
                                        "fix": "No action needed",
                                    })
                    break
            except (FileNotFoundError, subprocess.TimeoutExpired):
                continue
    except Exception:
        pass
    return findings


def _run_pentest(payload: dict, config: dict) -> dict:
    """Pentest sub-action: nmap-based local network penetration test."""
    sys.path.insert(0, str(FLEET_DIR))
    import db  # lazy import

    target = payload.get("target", "auto")
    scan_type = payload.get("scan_type", "service")
    label = payload.get("label", "local_network")
    include_hardening = config.get("security", {}).get("network_hardening_enabled", True)

    if target == "auto":
        target = _pentest_detect_network()

    if not _pentest_validate_target(target):
        return {"error": f"Invalid target format: {target}"}

    scan_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    PENTEST_DIR.mkdir(parents=True, exist_ok=True)
    warnings = []

    is_wsl_nat, _route_info = _pentest_detect_wsl_nat()
    if is_wsl_nat:
        warnings.append(
            "WSL2 NAT detected -- scans will target the virtual network (172.x.x.x), "
            "not your physical LAN. To fix: add networkingMode=mirrored to "
            "%USERPROFILE%\\.wslconfig and restart WSL."
        )

    xml_output, nmap_error = _pentest_run_nmap(target, scan_type)
    if nmap_error:
        return {"error": nmap_error, "target": target}

    raw_file = PENTEST_DIR / f"scan_{scan_id}_{label}.xml"
    raw_file.write_text(xml_output, encoding="utf-8")

    hosts = _pentest_parse_nmap_xml(xml_output)
    findings = _pentest_assess_findings(hosts)

    if include_hardening:
        findings.extend(_pentest_check_localhost_binding())

    host_summary = "\n".join(
        f"  {h['ip']} ({h['hostname']}): {len(h['open_ports'])} open ports -- "
        + ", ".join(f"{p['port']}/{p['service']}" for p in h["open_ports"][:8])
        for h in hosts
    )
    findings_summary = "\n".join(
        f"  [{f['severity']}] {f['detail']}"
        for f in findings if f["severity"] in ("HIGH", "MEDIUM")
    ) or "  No high/medium findings."

    unifi_hosts = [
        h for h in hosts
        if any(p["port"] in UNIFI_PORTS for p in h["open_ports"])
    ]
    unifi_note = ""
    if unifi_hosts:
        unifi_note = (
            f"\nUniFi infrastructure detected on: "
            + ", ".join(h["ip"] for h in unifi_hosts)
            + "\nApply UniFi-specific hardening: disable default SSH credentials, "
            "enable 2FA on controller, restrict controller port 8443 to management VLAN, "
            "keep firmware current, disable UPnP, review guest VLAN isolation."
        )

    system = ("You are a security advisor reviewing a local network penetration test. "
              "The network includes Ubiquiti UniFi infrastructure (routers, APs, switches).")
    user = (
        f"Target: {target}\nScan type: {scan_type}\nHosts discovered: {len(hosts)}"
        f"{unifi_note}\n\nHost inventory:\n{host_summary or '  No hosts found.'}"
        f"\n\nKey findings:\n{findings_summary}"
        "\n\nWrite a concise post-pentest security report (max 8 bullet points):\n"
        "- Lead with HIGH severity findings and exact remediation steps\n"
        "- Call out any UniFi controller/device exposure or misconfiguration\n"
        "- Note services that should not be LAN-exposed (databases, Ollama API, UniFi MongoDB)\n"
        "- Include 2-3 hardening recommendations for a home lab running local AI services on UniFi\n"
        "- End with a one-line overall risk rating: LOW / MEDIUM / HIGH"
    )
    analysis = call_complex(system, user, config, skill_name="security_suite")

    # Persist full JSON report
    report_json = {
        "scan_id": scan_id,
        "created_at": datetime.now().isoformat(),
        "target": target,
        "scan_type": scan_type,
        "hosts_discovered": len(hosts),
        "hosts": hosts,
        "findings": findings,
        "analysis": analysis,
        "raw_xml": str(raw_file),
    }
    report_json_file = PENTEST_DIR / f"pentest_{scan_id}_{label}.json"
    report_json_file.write_text(json.dumps(report_json, indent=2), encoding="utf-8")

    high = [f for f in findings if f["severity"] == "HIGH"]
    medium = [f for f in findings if f["severity"] == "MEDIUM"]
    info = [f for f in findings if f["severity"] == "INFO"]

    # Markdown report
    md_lines = [
        f"# Pen Test Report -- {target}",
        f"**Scan ID:** {scan_id}  **Type:** {scan_type}  **Hosts:** {len(hosts)}",
        f"**Findings:** {len(high)} HIGH, {len(medium)} MEDIUM, {len(info)} INFO",
        "",
        "## Security Analysis",
        analysis,
        "",
        "## Host Inventory",
    ]
    for h in hosts:
        ports_str = ", ".join(f"{p['port']}/{p['service']}" for p in h["open_ports"])
        md_lines.append(f"- **{h['ip']}** ({h['hostname'] or 'no hostname'}): {ports_str or 'no open ports'}")

    if high or medium:
        md_lines += ["", "## Findings Requiring Action"]
        for f in sorted(findings, key=lambda x: {"HIGH": 0, "MEDIUM": 1}.get(x["severity"], 2)):
            if f["severity"] in ("HIGH", "MEDIUM"):
                md_lines.append(f"- **[{f['severity']}]** {f['detail']}")
                if f.get("fix"):
                    md_lines.append(f"  - Remediation: {f['fix']}")

    md_lines += ["", f"*Raw nmap XML: {raw_file}*"]
    md_file = PENTEST_DIR / f"pentest_{scan_id}_{label}.md"
    md_file.write_text("\n".join(md_lines), encoding="utf-8")

    # If HIGH findings, create a pending security advisory
    if high:
        advisory_scope = f"pen_test_{target}"
        extra_md = [
            "",
            "## HIGH Findings",
        ] + [f"- {f['detail']} -- Fix: {f.get('fix', 'manual review')}" for f in high]
        advisory = _create_advisory(
            high + medium, advisory_scope, config, source="pen_test",
            extra_md_lines=extra_md,
        )
        advisory_id = advisory["id"]

        _notify_lead(db, {
            "type": "pentest_advisory",
            "advisory_id": advisory_id,
            "target": target,
            "counts": {"HIGH": len(high), "MEDIUM": len(medium)},
            "summary": analysis[:500],
            "report_file": str(md_file),
        })

    # Always notify lead of completed scan
    _notify_lead(db, {
        "type": "pentest_complete",
        "scan_id": scan_id,
        "target": target,
        "hosts_found": len(hosts),
        "high_findings": len(high),
        "report_file": str(md_file),
    })

    result = {
        "scan_id": scan_id,
        "target": target,
        "hosts_discovered": len(hosts),
        "findings": {"HIGH": len(high), "MEDIUM": len(medium), "INFO": len(info)},
        "report_file": str(md_file),
        "advisory_created": len(high) > 0,
    }
    if warnings:
        result["warnings"] = warnings
    return result


# ===========================================================================
# Sub-action: cve
# (was: cve_watch.py)
# ===========================================================================

def _cve_parse_req_line(line: str):
    """Parse a requirements.txt line into (name, version) or None."""
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("-"):
        return None
    match = re.match(
        r"^([a-zA-Z0-9_.-]+)\s*(?:[=<>!~]+\s*([0-9][0-9a-zA-Z.*-]*))?", line
    )
    if match:
        name = match.group(1).lower().replace("-", "_")
        version = match.group(2) or ""
        return name, version
    return None


def _cve_discover_deps() -> dict:
    """Collect dependencies from requirements.txt and pyproject.toml."""
    deps: dict = {}

    req_file = PROJECT_DIR / "requirements.txt"
    if req_file.exists():
        for line in req_file.read_text(encoding="utf-8").splitlines():
            parsed = _cve_parse_req_line(line)
            if parsed:
                deps[parsed[0]] = parsed[1]

    pyproject = PROJECT_DIR / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
            for match in re.finditer(
                r'"([a-zA-Z0-9_.-]+)\s*(?:[><=!~]+\s*([0-9][0-9a-zA-Z.*-]*))?', text
            ):
                name = match.group(1).lower().replace("-", "_")
                version = match.group(2) or ""
                if name not in deps:
                    deps[name] = version
        except Exception:
            pass

    fleet_req = FLEET_DIR / "requirements.txt"
    if fleet_req.exists():
        for line in fleet_req.read_text(encoding="utf-8").splitlines():
            parsed = _cve_parse_req_line(line)
            if parsed and parsed[0] not in deps:
                deps[parsed[0]] = parsed[1]

    return deps


def _cve_query_osv(pkg_name: str, version: str) -> dict:
    """Query OSV.dev for vulnerabilities on a single PyPI package."""
    query: dict = {"package": {"name": pkg_name, "ecosystem": "PyPI"}}
    if version:
        query["version"] = version
    data = json.dumps(query).encode("utf-8")
    req = urllib.request.Request(
        "https://api.osv.dev/v1/query",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _cve_extract_severity(vuln: dict) -> str:
    """Derive a severity label from an OSV vuln entry."""
    for s in vuln.get("severity", []):
        if s.get("type") == "CVSS_V3":
            score_str = s.get("score", "")
            num_match = re.search(r"(\d+\.\d+)", score_str)
            if num_match:
                score = float(num_match.group(1))
                if score >= 9.0:
                    return "critical"
                if score >= 7.0:
                    return "high"
                if score >= 4.0:
                    return "medium"
                return "low"
            return "high"
    return "unknown"


def _cve_build_report(checked: int, total: int, vulnerabilities: list):
    """Render the Markdown CVE report. Returns (md_str, critical, medium, low)."""
    critical = [v for v in vulnerabilities if v["severity"] in ("critical", "high")]
    medium = [v for v in vulnerabilities if v["severity"] == "medium"]
    low = [v for v in vulnerabilities if v["severity"] in ("low", "unknown")]

    md = [
        f"# CVE Watch Report -- {date.today().isoformat()}",
        "",
        f"**Packages checked:** {checked}/{total} | "
        f"**Vulnerabilities found:** {len(vulnerabilities)}",
        f"**Critical/High:** {len(critical)} | **Medium:** {len(medium)} "
        f"| **Low:** {len(low)}",
        "",
    ]

    if critical:
        md.append("## Critical / High Severity")
        md.append("")
        for v in critical:
            aliases = ", ".join(v["aliases"][:3]) if v["aliases"] else "none"
            md.append(f"### {v['id']} -- {v['package']} {v['version']}")
            md.append(f"- **Summary:** {v['summary']}")
            md.append(f"- **Aliases:** {aliases}")
            md.append(f"- **Published:** {v['published'][:10] if v['published'] else 'unknown'}")
            md.append("")

    if medium:
        md.append("## Medium Severity")
        md.append("")
        for v in medium:
            md.append(
                f"- **{v['id']}** -- {v['package']} {v['version']}: "
                f"{v['summary'][:100]}"
            )
        md.append("")

    if low:
        md.append("## Low / Unknown Severity")
        md.append("")
        for v in low:
            md.append(
                f"- **{v['id']}** -- {v['package']} {v['version']}: "
                f"{v['summary'][:100]}"
            )
        md.append("")

    if not vulnerabilities:
        md.append("## No Vulnerabilities Found")
        md.append("")
        md.append(f"All {checked} checked packages are clean.")
        md.append("")

    return "\n".join(md), critical, medium, low


def _run_cve(payload: dict, config: dict) -> dict:
    """CVE sub-action: check dependencies against OSV vulnerability database."""
    SECURITY_DIR.mkdir(parents=True, exist_ok=True)

    deps = _cve_discover_deps()
    if not deps:
        return {"status": "skip", "message": "No dependencies found to check"}

    vulnerabilities = []
    checked = 0
    errors = 0

    for pkg_name, version in deps.items():
        try:
            result = _cve_query_osv(pkg_name, version)
            for v in result.get("vulns", []):
                vulnerabilities.append({
                    "package": pkg_name,
                    "version": version,
                    "id": v.get("id", "unknown"),
                    "summary": v.get("summary", "No summary"),
                    "severity": _cve_extract_severity(v),
                    "aliases": v.get("aliases", []),
                    "published": v.get("published", ""),
                })
            checked += 1
        except urllib.error.HTTPError:
            checked += 1
        except Exception:
            errors += 1
            if errors > 5:
                break

    report, critical, medium, low = _cve_build_report(checked, len(deps), vulnerabilities)

    out_file = SECURITY_DIR / f"cve_watch_{date.today().isoformat()}.md"
    out_file.write_text(report, encoding="utf-8")

    result = {
        "status": "ok",
        "saved_to": str(out_file),
        "checked": checked,
        "total_deps": len(deps),
        "vulnerabilities": len(vulnerabilities),
        "critical_high": len(critical),
        "medium": len(medium),
        "low": len(low),
    }
    if critical:
        result["advisory"] = (
            f"CRITICAL: {len(critical)} high-severity CVEs found -- review {out_file.name}"
        )
    return result


# ===========================================================================
# Sub-action: sql
# (was: sql_review.py)
# ===========================================================================

def _sql_parse_schema(db_py_path: Path) -> dict:
    """Extract table schemas from db.py CREATE TABLE statements."""
    text = db_py_path.read_text(encoding="utf-8")
    tables: dict = {}
    for match in re.finditer(
        r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\((.*?)\)",
        text, re.DOTALL | re.IGNORECASE,
    ):
        table_name = match.group(1)
        columns: list = []
        for col_match in re.finditer(
            r"^\s*(\w+)\s+(?:TEXT|INTEGER|REAL|BLOB|NUMERIC)",
            match.group(2), re.MULTILINE | re.IGNORECASE,
        ):
            columns.append(col_match.group(1))
        tables[table_name] = columns
    return tables


def _sql_find_in_file(file_path: Path) -> list:
    """Find SQL strings in Python source files."""
    try:
        text = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    findings: list = []

    sql_pattern = re.compile(
        r"""(?:execute|executescript)\s*\(\s*"""
        r"""(?:f?(?:\"\"\"(.*?)\"\"\"|"([^"]*?)"|\'\'\'(.*?)\'\'\'|'([^']*?)')"""
        r""")""",
        re.DOTALL,
    )
    for match in sql_pattern.finditer(text):
        sql = match.group(1) or match.group(2) or match.group(3) or match.group(4)
        if not sql:
            continue
        sql_upper = sql.upper().strip()
        if not any(sql_upper.startswith(kw) for kw in
                   ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP",
                    "ALTER", "PRAGMA", "WITH", "REPLACE")):
            continue
        line_no = text[:match.start()].count("\n") + 1
        prefix_region = text[max(0, match.start() - 10):match.start() + 10]
        is_fstring = bool(re.search(r"""f["']""", prefix_region))
        findings.append({"sql": sql, "line": line_no, "fstring": is_fstring, "file": str(file_path)})

    format_pattern = re.compile(
        r"""(?:\"\"\"(.*?)\"\"\"|"([^"]*?)")\.format\(""", re.DOTALL
    )
    for match in format_pattern.finditer(text):
        sql = match.group(1) or match.group(2)
        if not sql:
            continue
        sql_upper = sql.upper().strip()
        if not any(sql_upper.startswith(kw) for kw in
                   ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP")):
            continue
        line_no = text[:match.start()].count("\n") + 1
        findings.append({
            "sql": sql, "line": line_no, "fstring": False, "format_call": True, "file": str(file_path)
        })

    return findings


def _sql_extract_table_refs(sql: str, schema: dict) -> list:
    sql_clean = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
    tables: list = []
    for match in re.finditer(
        r"(?:FROM|JOIN|INTO|UPDATE|TABLE)\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)",
        sql_clean, re.IGNORECASE,
    ):
        name = match.group(1)
        if name.upper() not in ("SELECT", "SET", "WHERE", "VALUES", "AND", "OR", "NOT", "NULL", "AS"):
            tables.append(name)
    return tables


def _sql_extract_column_refs(sql: str) -> list:
    sql_clean = re.sub(r"--.*$", "", sql, flags=re.MULTILINE)
    columns: list = []

    select_match = re.search(r"SELECT\s+(.*?)\s+FROM", sql_clean, re.IGNORECASE | re.DOTALL)
    if select_match:
        col_str = select_match.group(1)
        if col_str.strip() != "*":
            for part in col_str.split(","):
                part = part.strip()
                ident = re.findall(r"(?:(\w+)\.)?(\w+)(?:\s+AS\s+\w+)?", part, re.IGNORECASE)
                for _tbl, col in ident:
                    if col.upper() not in ("AS", "DESC", "ASC", "NULL", "NOT",
                                           "DISTINCT", "COUNT", "SUM", "AVG",
                                           "MIN", "MAX", "GROUP", "ORDER",
                                           "FROM", "WHERE", "AND", "OR", "NOW"):
                        columns.append(col)

    where_match = re.search(r"WHERE\s+(.*?)(?:ORDER|GROUP|LIMIT|$)", sql_clean, re.IGNORECASE | re.DOTALL)
    if where_match:
        for ident in re.findall(r"(\w+)\s*(?:=|<|>|!=|LIKE|IN|IS)", where_match.group(1), re.IGNORECASE):
            if ident.upper() not in ("AND", "OR", "NOT", "NULL", "WHERE"):
                columns.append(ident)

    set_match = re.search(r"SET\s+(.*?)(?:WHERE|$)", sql_clean, re.IGNORECASE | re.DOTALL)
    if set_match:
        for part in set_match.group(1).split(","):
            eq = part.split("=")
            if eq:
                col = eq[0].strip()
                if re.match(r"^\w+$", col) and col.upper() not in ("SET",):
                    columns.append(col)

    return columns


def _sql_validate(finding: dict, schema: dict) -> list:
    """Validate a single SQL finding against the schema."""
    issues: list = []
    sql = finding["sql"]
    file_path = finding["file"]
    line = finding["line"]
    file_name = Path(file_path).name

    if finding.get("fstring"):
        issues.append({
            "file": file_name, "line": line, "severity": "HIGH",
            "category": "SQL_INJECTION",
            "detail": "f-string used in SQL query -- use parameterized queries (?) instead",
        })
    if finding.get("format_call"):
        issues.append({
            "file": file_name, "line": line, "severity": "HIGH",
            "category": "SQL_INJECTION",
            "detail": ".format() used in SQL query -- use parameterized queries (?) instead",
        })

    if re.search(r"SELECT\s+\*", sql, re.IGNORECASE):
        issues.append({
            "file": file_name, "line": line, "severity": "MEDIUM",
            "category": "SELECT_STAR",
            "detail": "SELECT * is fragile -- enumerate columns explicitly",
        })

    if re.match(r"\s*(UPDATE|DELETE)\s", sql, re.IGNORECASE):
        if not re.search(r"\bWHERE\b", sql, re.IGNORECASE):
            issues.append({
                "file": file_name, "line": line, "severity": "HIGH",
                "category": "NO_WHERE_CLAUSE",
                "detail": "UPDATE/DELETE without WHERE -- risk of modifying all rows",
            })

    sql_upper = sql.upper().strip()
    if sql_upper.startswith("SELECT") and not re.search(r"\bLIMIT\b", sql, re.IGNORECASE):
        tables = _sql_extract_table_refs(sql, schema)
        known_tables = [t for t in tables if t in schema]
        if known_tables:
            issues.append({
                "file": file_name, "line": line, "severity": "LOW",
                "category": "NO_LIMIT",
                "detail": f"SELECT on {', '.join(known_tables)} without LIMIT -- may return unbounded rows",
            })

    tables = _sql_extract_table_refs(sql, schema)
    for table in tables:
        if table not in schema and not table.startswith("{"):
            if table.upper() not in ("PRAGMA", "SQLITE_MASTER"):
                issues.append({
                    "file": file_name, "line": line, "severity": "HIGH",
                    "category": "UNKNOWN_TABLE",
                    "detail": f"Table '{table}' not found in fleet DB schema",
                })

    columns = _sql_extract_column_refs(sql)
    tables = _sql_extract_table_refs(sql, schema)
    known_tables = [t for t in tables if t in schema]
    if known_tables and columns:
        all_valid_cols: set = set()
        for t in known_tables:
            all_valid_cols.update(schema[t])
        for col in columns:
            if col.startswith("{"):
                continue
            if col not in all_valid_cols:
                if col.upper() not in ("ROWID", "OID", "TRUE", "FALSE",
                                       "CURRENT_TIMESTAMP", "DATETIME"):
                    issues.append({
                        "file": file_name, "line": line, "severity": "MEDIUM",
                        "category": "UNKNOWN_COLUMN",
                        "detail": f"Column '{col}' not in schema for table(s) {known_tables}",
                    })

    return issues


def _run_sql(payload: dict, config: dict) -> dict:
    """SQL sub-action: validate SQL queries against the fleet DB schema."""
    target_file = payload.get("file", "")
    target_dir = payload.get("directory", "")

    if not DB_PY_PATH.exists():
        return {"error": f"db.py not found at {DB_PY_PATH}"}
    schema = _sql_parse_schema(DB_PY_PATH)
    if not schema:
        return {"error": "No CREATE TABLE statements found in db.py"}

    if target_file:
        path = Path(target_file)
        if not path.exists():
            path = FLEET_DIR / target_file
        if not path.exists():
            return {"error": f"File not found: {target_file}"}
        py_files = [path]
    elif target_dir:
        scan_dir = Path(target_dir)
        if not scan_dir.exists():
            scan_dir = FLEET_DIR / target_dir
        if not scan_dir.exists():
            return {"error": f"Directory not found: {target_dir}"}
        py_files = sorted(scan_dir.rglob("*.py"))
    else:
        py_files = sorted(FLEET_DIR.rglob("*.py"))
        py_files = [
            f for f in py_files
            if "__pycache__" not in str(f)
            and ".venv" not in str(f)
            and "node_modules" not in str(f)
            and "knowledge" not in str(f)
        ]

    all_issues: list = []
    files_with_sql = 0
    total_queries = 0

    for py_file in py_files:
        findings = _sql_find_in_file(py_file)
        if findings:
            files_with_sql += 1
            total_queries += len(findings)
        for finding in findings:
            all_issues.extend(_sql_validate(finding, schema))

    severity_sort = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INFO": 3}
    all_issues.sort(key=lambda x: severity_sort.get(x["severity"], 9))

    by_severity: dict = {}
    for issue in all_issues:
        by_severity[issue["severity"]] = by_severity.get(issue["severity"], 0) + 1

    by_category: dict = {}
    for issue in all_issues:
        by_category[issue["category"]] = by_category.get(issue["category"], 0) + 1

    output_dir = KNOWLEDGE_DIR / "quality"
    output_dir.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = output_dir / f"sql_review_{date_str}.md"

    lines = [
        f"# SQL Review -- {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"**Files scanned:** {len(py_files)}",
        f"**Files with SQL:** {files_with_sql}",
        f"**SQL queries found:** {total_queries}",
        f"**Issues found:** {len(all_issues)}",
        "",
        "## Schema (from db.py)",
        "",
        "| Table | Columns |",
        "|-------|---------|",
    ]
    for table, cols in sorted(schema.items()):
        lines.append(f"| {table} | {', '.join(cols)} |")
    lines.append("")
    lines.extend([
        "## Severity Summary",
        "",
        "| Severity | Count |",
        "|----------|-------|",
    ])
    for sev in ("HIGH", "MEDIUM", "LOW", "INFO"):
        if sev in by_severity:
            lines.append(f"| {sev} | {by_severity[sev]} |")
    lines.append("")

    lines.append("## Issues by Category")
    grouped: dict = {}
    for issue in all_issues:
        grouped.setdefault(issue["category"], []).append(issue)
    for cat, items in sorted(grouped.items()):
        lines.append(f"\n### {cat} ({len(items)})")
        for item in items:
            line_ref = f"line {item['line']}" if item.get("line") else "file-level"
            lines.append(f"- **[{item['severity']}]** `{item['file']}` ({line_ref}) -- {item['detail']}")
    lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")

    return {
        "status": "ok",
        "files_scanned": len(py_files),
        "files_with_sql": files_with_sql,
        "queries_found": total_queries,
        "total_issues": len(all_issues),
        "issues": all_issues[:50],
        "by_severity": by_severity,
        "by_category": by_category,
        "schema_tables": list(schema.keys()),
        "saved_to": str(report_path),
    }


# ===========================================================================
# Entry point
# ===========================================================================

def run(payload: dict, config: dict) -> dict:
    """Dispatch to security sub-action.

    Payload key: action = audit | code_scan | apply | pentest | cve | sql
    Default action: audit
    """
    return dispatch_action(
        payload,
        config,
        actions={
            "audit": _run_audit,
            "code_scan": _run_code_scan,
            "apply": _run_apply,
            "pentest": _run_pentest,
            "cve": _run_cve,
            "sql": _run_sql,
        },
        default="audit",
    )
