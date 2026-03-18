"""
Security audit skill — scans targets for issues, generates an advisory,
and saves it to knowledge/security/pending/ for human review.
No changes are made until the advisory is explicitly approved via security_apply.
"""
import hashlib
import json
import os
import re
import stat
from datetime import datetime
from pathlib import Path

import httpx

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
SECURITY_DIR = KNOWLEDGE_DIR / "security"
PENDING_DIR = SECURITY_DIR / "pending"
APPLIED_DIR = SECURITY_DIR / "applied"

# Patterns that suggest accidental credential exposure
SECRET_PATTERNS = [
    (re.compile(r'sk-ant-[A-Za-z0-9_-]{20,}'), "Anthropic API key"),
    (re.compile(r'hf_[A-Za-z0-9]{20,}'), "HuggingFace token"),
    (re.compile(r'(?i)(api[_-]?key|secret|password|token)\s*=\s*["\']?[A-Za-z0-9_\-]{16,}'), "Potential credential"),
    (re.compile(r'BRAVE_API_KEY=[A-Za-z0-9_\-]{16,}'), "Brave API key"),
    (re.compile(r'TAVILY_API_KEY=[A-Za-z0-9_\-]{16,}'), "Tavily API key"),
]

# Paths to check for overly permissive file modes
PERMISSION_CHECKS = [
    ("~/.secrets", 0o600),
    ("~/.ssh/id_rsa", 0o600),
    ("~/.ssh/id_ed25519", 0o600),
]


def _ollama(prompt, config):
    resp = httpx.post(
        f"{config['models']['ollama_host']}/api/generate",
        json={"model": config["models"]["local"], "prompt": prompt, "stream": False},
        timeout=300,
    )
    resp.raise_for_status()
    return resp.json()["response"].strip()


def _check_permissions():
    findings = []
    for path_str, required_mode in PERMISSION_CHECKS:
        path = Path(path_str).expanduser()
        if not path.exists():
            continue
        current = stat.S_IMODE(path.stat().st_mode)
        if current & ~required_mode:
            findings.append({
                "severity": "HIGH",
                "type": "file_permissions",
                "path": str(path),
                "detail": f"Mode {oct(current)} is too permissive — should be {oct(required_mode)}",
                "fix": f"chmod {oct(required_mode)[2:]} {path}",
            })
    return findings


def _scan_secrets(scan_dirs):
    findings = []
    for base in scan_dirs:
        base = Path(base).expanduser()
        if not base.exists():
            continue
        for fpath in base.rglob("*"):
            if not fpath.is_file():
                continue
            # Skip binary/large files and excluded names
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
            for pattern, label in SECRET_PATTERNS:
                for match in pattern.finditer(content):
                    line_num = content[:match.start()].count("\n") + 1
                    # Mask the matched value in the report
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


def _check_gitignore(scan_dirs):
    findings = []
    sensitive_names = {".secrets", "*.env", "*.pem", "*.key", "fleet.db", "*.jsonl"}
    for base in scan_dirs:
        base = Path(base).expanduser()
        gitignore = base / ".gitignore"
        if not gitignore.exists():
            continue
        content = gitignore.read_text()
        for name in sensitive_names:
            # Very basic check — just flag if not mentioned
            stem = name.replace("*.", "").replace("*", "")
            if stem not in content:
                findings.append({
                    "severity": "MEDIUM",
                    "type": "gitignore_gap",
                    "path": str(gitignore),
                    "detail": f"'{name}' not in .gitignore — may be accidentally committed",
                    "fix": f"Add '{name}' to {gitignore}",
                })
    return findings


def _build_advisory(findings, scope_desc, config):
    if not findings:
        return None

    high = [f for f in findings if f["severity"] == "HIGH"]
    medium = [f for f in findings if f["severity"] == "MEDIUM"]
    low = [f for f in findings if f["severity"] == "LOW"]

    summary_text = "\n".join(
        f"[{f['severity']}] {f['type']} — {f['detail']} (fix: {f.get('fix', 'manual review')})"
        for f in findings
    )

    prompt = f"""You are a security advisor reviewing a local development environment.
Scope: {scope_desc}

Findings:
{summary_text}

Write a concise security advisory (max 6 bullet points). Lead with HIGH severity items.
For each finding: state the risk, the specific file/path, and the exact remediation step.
Do NOT include boilerplate. Be direct and actionable."""

    analysis = _ollama(prompt, config)

    advisory_id = hashlib.sha1(
        (scope_desc + datetime.now().isoformat()).encode()
    ).hexdigest()[:8]

    return {
        "id": advisory_id,
        "created_at": datetime.now().isoformat(),
        "scope": scope_desc,
        "status": "PENDING_APPROVAL",
        "counts": {"HIGH": len(high), "MEDIUM": len(medium), "LOW": len(low)},
        "findings": findings,
        "analysis": analysis,
    }


def run(payload, config):
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db

    scope = payload.get("scope", "fleet")
    scan_dirs = payload.get("scan_dirs", [str(FLEET_DIR)])
    include_permission_check = payload.get("check_permissions", True)
    include_secret_scan = payload.get("check_secrets", True)
    include_gitignore = payload.get("check_gitignore", True)

    findings = []

    if include_permission_check:
        findings.extend(_check_permissions())

    if include_secret_scan:
        findings.extend(_scan_secrets(scan_dirs))

    if include_gitignore:
        findings.extend(_check_gitignore(scan_dirs))

    if not findings:
        return {"status": "clean", "scope": scope, "message": "No security issues found."}

    advisory = _build_advisory(findings, scope, config)
    if not advisory:
        return {"status": "clean", "scope": scope}

    # Save advisory to pending/
    PENDING_DIR.mkdir(parents=True, exist_ok=True)
    advisory_file = PENDING_DIR / f"advisory_{advisory['id']}.json"
    advisory_file.write_text(json.dumps(advisory, indent=2))

    # Also write a human-readable markdown version
    md_lines = [
        f"# Security Advisory {advisory['id']}",
        f"**Created:** {advisory['created_at']}",
        f"**Scope:** {scope}",
        f"**Status:** PENDING_APPROVAL",
        f"**Findings:** {advisory['counts']['HIGH']} HIGH, {advisory['counts']['MEDIUM']} MEDIUM, {advisory['counts']['LOW']} LOW",
        "",
        "## Analysis",
        advisory["analysis"],
        "",
        "## Raw Findings",
    ]
    for f in findings:
        md_lines.append(f"- **[{f['severity']}]** `{f.get('path', '')}` — {f['detail']}")
        if f.get("fix"):
            md_lines.append(f"  - Fix: `{f['fix']}`")
    md_lines += [
        "",
        "## To Apply",
        f"```",
        f"uv run python lead_client.py task 'security_apply {advisory['id']}' --wait",
        f"```",
        "Or apply manually using the fixes listed above.",
    ]
    md_file = PENDING_DIR / f"advisory_{advisory['id']}.md"
    md_file.write_text("\n".join(md_lines))

    # Post to messages table so lead can see it
    db.post_message(
        from_agent="security",
        to_agent="lead",
        body_json=json.dumps({
            "type": "security_advisory",
            "advisory_id": advisory["id"],
            "counts": advisory["counts"],
            "summary": advisory["analysis"][:500],
            "pending_file": str(md_file),
        })
    )

    return {
        "advisory_id": advisory["id"],
        "counts": advisory["counts"],
        "pending_file": str(md_file),
        "status": "advisory_posted",
        "note": "Review advisory then dispatch security_apply to proceed.",
    }
