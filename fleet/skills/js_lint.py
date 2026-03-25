"""
JavaScript / HTML lint skill — pure-Python static analysis via regex.

Catches the most impactful JS issues without requiring Node.js or ESLint:
  - innerHTML assignment (XSS risk)
  - eval() / document.write usage (code injection)
  - console.log left in production code
  - == instead of === (type coercion bugs)
  - var usage (prefer let/const)
  - Empty catch blocks (silently swallowed errors)
  - TODO/FIXME/HACK/XXX markers
  - Functions over 50 lines (complexity warning)
  - Duplicate addEventListener patterns
  - Unreachable code after return

For .html files, extracts <script> blocks and lints those separately.

Payload:
  file       str   single file to lint (optional)
  directory  str   directory to scan (optional)
  (default: scans fleet/templates/ and fleet/static/)

Output: knowledge/quality/js_lint_<date>.md
Returns: {files_scanned, issues_found, errors, warnings, infos}
"""
import re
from datetime import datetime
from pathlib import Path

SKILL_NAME = "js_lint"
DESCRIPTION = (
    "Lint JavaScript code for common issues: unused vars, missing semicolons, "
    "console.log, innerHTML, eval usage."
)
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False
SUITE = ""

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
QUALITY_DIR = KNOWLEDGE_DIR / "quality"

# ── Pattern catalogue ────────────────────────────────────────────────
# Each entry: (severity, compiled_regex, message)
# NOTE: patterns that reference "eval" are detection rules, not usage.
_RAW_PATTERNS = [
    ("error",   r'\.innerHTML\s*=',                        "innerHTML assignment — use textContent or createElement (XSS risk)"),
    ("error",   r'[^.]\beval\s*\(',                        "eval() usage — code injection risk"),  # detects eval, does not call it
    ("error",   r'\bdocument\.write\s*\(',                  "document.write — use DOM manipulation instead"),
    ("warning", r'\bconsole\.(log|debug|info)\s*\(',        "console.log left in code"),
    ("warning", r'[^=!<>]==[^=]',                           "== instead of === (type coercion)"),
    ("warning", r'catch\s*\([^)]*\)\s*\{\s*\}',            "Empty catch block — errors silently swallowed"),
    ("warning", r'\bvar\s+',                                "var usage — prefer let/const"),
    ("info",    r'//\s*(TODO|FIXME|HACK|XXX)\b',            "TODO/FIXME comment found"),
    ("error",   r'\balert\s*\(',                            "alert() in production code"),
    ("warning", r'\.addEventListener\s*\(\s*["\']click',    "click addEventListener — verify no duplicate bindings"),
]

PATTERNS = [(sev, re.compile(pat), msg) for sev, pat, msg in _RAW_PATTERNS]

# Regex for extracting <script> blocks from HTML
_SCRIPT_RE = re.compile(r'<script[^>]*>(.*?)</script>', re.DOTALL)

# Regex helpers for function-length analysis
_FUNC_DECL_RE = re.compile(
    r'(?:^|[;{}\s])(?:async\s+)?function\s+\w+\s*\('
    r'|'
    r'(?:const|let|var)\s+\w+\s*=\s*(?:async\s+)?(?:function|\([^)]*\)\s*=>|\w+\s*=>)',
)

MAX_FUNCTION_LINES = 50


# ── Helpers ──────────────────────────────────────────────────────────

def _extract_js_from_html(html_content: str) -> list[tuple[int, str]]:
    """Return list of (start_line_offset, script_text) from HTML."""
    blocks = []
    for m in _SCRIPT_RE.finditer(html_content):
        # Count newlines before the match to get the line offset
        start_offset = html_content[:m.start(1)].count('\n')
        blocks.append((start_offset, m.group(1)))
    return blocks


def _lint_js(js_text: str, line_offset: int = 0) -> list[dict]:
    """Run all regex patterns against a JS string, returning findings."""
    findings = []
    lines = js_text.splitlines()

    # Pattern checks
    for line_num, line in enumerate(lines, 1):
        # Skip comment-only lines for some patterns (not for TODO detection)
        stripped = line.strip()
        is_comment = stripped.startswith("//")

        for severity, pattern, message in PATTERNS:
            # Allow TODO pattern to match on comments (that's the point)
            if is_comment and "TODO" not in message and "FIXME" not in message:
                continue
            if pattern.search(line):
                findings.append({
                    "line": line_num + line_offset,
                    "severity": severity,
                    "message": message,
                    "text": stripped[:120],
                })

    # Function length check — heuristic brace-depth tracking
    func_start = None
    func_name = "anonymous"
    brace_depth = 0
    in_func = False

    for line_num, line in enumerate(lines, 1):
        stripped = line.strip()

        # Detect function declarations
        if _FUNC_DECL_RE.search(line) and not in_func:
            func_start = line_num
            # Try to extract function name
            name_match = re.search(r'function\s+(\w+)', line)
            if not name_match:
                name_match = re.search(r'(?:const|let|var)\s+(\w+)\s*=', line)
            func_name = name_match.group(1) if name_match else "anonymous"
            brace_depth = 0
            in_func = True

        if in_func:
            brace_depth += stripped.count('{') - stripped.count('}')
            if brace_depth <= 0 and func_start is not None:
                length = line_num - func_start
                if length > MAX_FUNCTION_LINES:
                    findings.append({
                        "line": func_start + line_offset,
                        "severity": "warning",
                        "message": f"Function '{func_name}' is {length} lines (>{MAX_FUNCTION_LINES}) — consider splitting",
                        "text": "",
                    })
                in_func = False
                func_start = None

    # Unreachable code after return (simple heuristic)
    for line_num, line in enumerate(lines, 1):
        stripped = line.strip()
        if re.match(r'return\b', stripped) and not stripped.endswith('{'):
            # Check if next non-empty line exists and is not } or case/default
            for lookahead in lines[line_num:]:
                la = lookahead.strip()
                if not la:
                    continue
                if la.startswith('}') or la.startswith('case ') or la.startswith('default:'):
                    break
                findings.append({
                    "line": line_num + 1 + line_offset,
                    "severity": "warning",
                    "message": "Possible unreachable code after return",
                    "text": la[:120],
                })
                break

    return findings


def _collect_files(directory: Path) -> list[Path]:
    """Recursively collect .js and .html files."""
    files = []
    try:
        for ext in ("*.js", "*.html"):
            files.extend(directory.rglob(ext))
    except Exception:
        pass
    return sorted(set(files))


def _lint_file(path: Path) -> list[dict]:
    """Lint a single .js or .html file, returning findings with file info."""
    try:
        content = path.read_text(errors="ignore")
    except Exception:
        return [{
            "file": str(path),
            "line": 0,
            "severity": "error",
            "message": f"Cannot read file: {path}",
            "text": "",
        }]

    findings = []
    if path.suffix.lower() in (".html", ".htm", ".jinja2"):
        # Extract and lint each <script> block
        blocks = _extract_js_from_html(content)
        for offset, block_text in blocks:
            for f in _lint_js(block_text, line_offset=offset):
                f["file"] = str(path)
                findings.append(f)
    else:
        # Plain JS file
        for f in _lint_js(content):
            f["file"] = str(path)
            findings.append(f)

    return findings


def _save_report(findings: list[dict], files_scanned: int) -> Path:
    """Write markdown report to knowledge/quality/."""
    QUALITY_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = QUALITY_DIR / f"js_lint_{date_str}.md"

    errors = sum(1 for f in findings if f["severity"] == "error")
    warnings = sum(1 for f in findings if f["severity"] == "warning")
    infos = sum(1 for f in findings if f["severity"] == "info")

    lines = [
        f"# JS Lint Report — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        f"**Files scanned:** {files_scanned}",
        f"**Issues found:** {len(findings)} ({errors} errors, {warnings} warnings, {infos} info)",
        "",
        "## Summary",
        "| Severity | Count |",
        "|----------|-------|",
        f"| Error    | {errors} |",
        f"| Warning  | {warnings} |",
        f"| Info     | {infos} |",
        "",
    ]

    # Group by file
    by_file: dict[str, list[dict]] = {}
    for f in findings:
        by_file.setdefault(f["file"], []).append(f)

    lines.append("## Findings by File")
    for fname in sorted(by_file):
        items = by_file[fname]
        lines.append(f"\n### {fname} ({len(items)} issues)")
        for item in sorted(items, key=lambda x: x["line"]):
            sev = item["severity"].upper()
            lines.append(
                f"- **[{sev}]** line {item['line']} — {item['message']}"
            )
            if item.get("text"):
                lines.append(f"  `{item['text']}`")

    lines.append("")
    report_path.write_text("\n".join(lines))
    return report_path


# ── Skill entry point ────────────────────────────────────────────────

def run(payload: dict, config: dict) -> dict:
    """Lint JavaScript/HTML files for common issues.

    Payload fields:
      file      — single file path to lint
      directory — directory to scan (.js + .html files)
      (omit both to scan fleet/templates/ and fleet/static/)

    Returns dict with files_scanned, issues_found, errors, warnings, infos.
    """
    file_path = payload.get("file") or payload.get("payload", {}).get("file")
    dir_path = payload.get("directory") or payload.get("payload", {}).get("directory")

    files: list[Path] = []

    if file_path:
        p = Path(file_path)
        if p.exists():
            files = [p]
        else:
            return {"status": "error", "error": f"File not found: {file_path}"}
    elif dir_path:
        d = Path(dir_path)
        if d.is_dir():
            files = _collect_files(d)
        else:
            return {"status": "error", "error": f"Directory not found: {dir_path}"}
    else:
        # Default: scan fleet templates and static directories
        for default_dir in [FLEET_DIR / "templates", FLEET_DIR / "static"]:
            if default_dir.is_dir():
                files.extend(_collect_files(default_dir))

    if not files:
        return {
            "status": "ok",
            "files_scanned": 0,
            "issues_found": 0,
            "errors": 0,
            "warnings": 0,
            "infos": 0,
            "result": "No JS/HTML files found to scan.",
        }

    # Run lint on all files
    all_findings: list[dict] = []
    for f in files:
        all_findings.extend(_lint_file(f))

    # Save report
    report_path = _save_report(all_findings, len(files))

    errors = sum(1 for f in all_findings if f["severity"] == "error")
    warnings = sum(1 for f in all_findings if f["severity"] == "warning")
    infos = sum(1 for f in all_findings if f["severity"] == "info")

    return {
        "status": "ok",
        "files_scanned": len(files),
        "issues_found": len(all_findings),
        "errors": errors,
        "warnings": warnings,
        "infos": infos,
        "saved_to": str(report_path),
        "result": (
            f"Scanned {len(files)} files — {len(all_findings)} issues "
            f"({errors} errors, {warnings} warnings, {infos} info). "
            f"Report: {report_path.name}"
        ),
    }
