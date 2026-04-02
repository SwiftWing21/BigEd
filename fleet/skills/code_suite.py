"""
Code suite — consolidates 6 code review/quality skills into one dispatcher.

Actions (payload['action']):
  review   — read a fleet Python file and produce a structured LLM-based review
  quality  — static AST/regex quality checks across fleet Python files
  refactor — suggest refactoring improvements (DRY, SOLID, readability, …)
  discuss  — multi-agent code discussion posted to messages table
  evaluate — adversarial PASS/FAIL evaluation of any skill output
  pair     — line-level pair-programming session: issues, suggestions, questions

Common payload keys:
  action       str  one of the 6 actions above (default "review")
  agent_name   str  "coder_1" | "coder_2" | "coder_3" (default "coder_2")

See each _action function's docstring for action-specific payload keys.
"""
import ast
import json
import logging
import re
from datetime import date, datetime
from pathlib import Path

from skills._models import call_complex

SKILL_NAME = "code_suite"
DESCRIPTION = "Code analysis: review, quality, refactor, discuss, evaluate, pair program"
VERSION = "1.0.0"
REQUIRES_NETWORK = False
COMPLEXITY = "medium"
SUITE = "code"

log = logging.getLogger(SKILL_NAME)

# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

FLEET_DIR     = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"

# Maps perspective label → focus area used in review/discuss prompts
PERSPECTIVE_FOCUS = {
    "software architect":     "module structure, interface design, coupling, extensibility",
    "code critic / reviewer": "bugs, error handling, edge cases, security, code clarity",
    "performance optimizer":  "query efficiency, I/O patterns, timeouts, caching opportunities",
}

# Maps agent name → default perspective
PERSPECTIVE_MAP = {
    "coder_1": "software architect",
    "coder_2": "code critic / reviewer",
    "coder_3": "performance optimizer",
}

# Rotated when no explicit file is requested for review
DEFAULT_REVIEW_QUEUE = [
    "db.py",
    "worker.py",
    "supervisor.py",
    "lead_client.py",
    "skills/web_search.py",
    "skills/lead_research.py",
    "skills/synthesize.py",
    "skills/web_crawl.py",
    "skills/marketing.py",
    "skills/generate_image.py",
    "skills/generate_video.py",
    "skills/security_audit.py",
]

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _pick_review_file(requested: str, reviews_dir: Path = None) -> Path | None:
    """Return the Path to a fleet file to review.

    If *requested* is given, resolves it (relative to FLEET_DIR or absolute)
    and blocks path-traversal.  If omitted, returns the least-recently-reviewed
    entry from DEFAULT_REVIEW_QUEUE.
    """
    base = FLEET_DIR
    if requested:
        p = Path(requested).resolve()
        if not p.is_absolute():
            p = (base / requested).resolve()
        if not str(p).startswith(str(base.resolve())):
            return None  # block path traversal
        if p.exists():
            return p
        rel = (base / requested).resolve()
        if not str(rel).startswith(str(base.resolve())):
            return None
        return rel if rel.exists() else None

    # Auto-pick least-recently-reviewed
    if reviews_dir is None:
        reviews_dir = KNOWLEDGE_DIR / "code_reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    reviewed = {
        re.sub(r"_review_\d{8}.*\.md$", "", f.stem).replace("_", "/")
        for f in reviews_dir.glob("*_review_*.md")
    }
    for candidate in DEFAULT_REVIEW_QUEUE:
        key = candidate.replace("/", "_").replace(".py", "")
        if not any(key in r.replace("/", "_") for r in reviewed):
            p = FLEET_DIR / candidate
            if p.exists():
                return p
    # All reviewed — start over
    for candidate in DEFAULT_REVIEW_QUEUE:
        p = FLEET_DIR / candidate
        if p.exists():
            return p
    return None


def _read_source(path: Path, max_lines: int = 400) -> str:
    """Read a source file, truncating to *max_lines* with a note if needed."""
    content = path.read_text(errors="ignore")
    lines = content.splitlines()
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]) + f"\n\n... ({len(lines) - max_lines} more lines truncated)"
    return content


def _load_discussion(topic: str, limit: int = 0) -> str:
    """Load prior contributions for *topic* from the messages table.

    Args:
        topic: The discussion topic string to filter by.
        limit: If > 0, return only the last *limit* contributions.
    """
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db
    with db.get_conn() as conn:
        rows = conn.execute("""
            SELECT from_agent, body_json FROM messages
            WHERE json_extract(body_json, '$.topic') = ?
              AND channel IN ('agent', 'fleet')
            ORDER BY created_at ASC
        """, (topic,)).fetchall()
    contributions = []
    for row in rows:
        try:
            body = json.loads(row["body_json"])
            contributions.append(f"[{row['from_agent']}]: {body.get('contribution', '')}")
        except Exception:
            pass
    if limit > 0:
        contributions = contributions[-limit:]
    return "\n\n".join(contributions)


def _extract_json(text: str, required_key: str) -> dict | None:
    """Find the first valid JSON object in *text* that contains *required_key*."""
    text = text.strip()
    try:
        data = json.loads(text)
        if required_key in data:
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    # Walk through the text looking for balanced braces
    depth, start = 0, None
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    data = json.loads(text[start : i + 1])
                    if required_key in data:
                        return data
                except (json.JSONDecodeError, TypeError):
                    pass
                start = None
    return None


# ---------------------------------------------------------------------------
# Action: review
# ---------------------------------------------------------------------------

_REVIEWS_DIR = KNOWLEDGE_DIR / "code_reviews"


def _review(payload: dict, config: dict) -> dict:
    """LLM-based code review of a fleet Python file.

    Payload:
      file         str  path relative to fleet root (or absolute); auto-picks if omitted
      perspective  str  "software architect" | "code critic / reviewer" | "performance optimizer"
      agent_name   str  reviewer label used in the saved filename
      focus        str  optional narrow focus, e.g. "error handling only"
    """
    requested   = payload.get("file", "")
    perspective = payload.get("perspective", "code critic / reviewer")
    agent_name  = payload.get("agent_name", "coder_2")
    focus_hint  = payload.get("focus", "")

    target = _pick_review_file(requested, _REVIEWS_DIR)
    if target is None:
        return {"error": "No reviewable file found", "requested": requested}

    try:
        source_excerpt = _read_source(target, max_lines=400)
    except Exception as e:
        return {"error": str(e), "file": str(target)}

    focus_area = focus_hint or PERSPECTIVE_FOCUS.get(perspective, "general code quality")
    rel_path   = str(target.relative_to(FLEET_DIR)) if target.is_relative_to(FLEET_DIR) else str(target)

    prompt = f"""You are a {perspective} conducting a code review of a Python file in a local AI agent fleet system.

FILE: {rel_path}
REVIEW FOCUS: {focus_area}

SOURCE CODE:
```python
{source_excerpt}
```

Produce a structured code review with these sections:
## Summary
One paragraph — what this file does and your overall impression.

## Findings
List each finding as:
- [SEVERITY] Line ~N: description of issue and suggested fix
Severity levels: CRITICAL | HIGH | MEDIUM | LOW | NOTE

Focus your findings on {focus_area}.
Be specific — reference actual variable names, function names, and line numbers.
Limit to 8 most important findings.

## Top Recommendation
The single most impactful change you would make first."""

    review_text = call_complex("You are a code reviewer.", prompt, config)

    _REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = rel_path.replace("/", "_").replace(".py", "")
    date_str  = datetime.now().strftime("%Y%m%d")
    out_file  = _REVIEWS_DIR / f"{safe_name}_review_{date_str}_{agent_name}.md"
    header = (
        f"# Code Review: `{rel_path}`\n"
        f"**Reviewer:** {agent_name} ({perspective})\n"
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
        f"**Focus:** {focus_area}\n\n---\n\n"
    )
    out_file.write_text(header + review_text, encoding="utf-8")

    return {
        "file_reviewed":    rel_path,
        "perspective":      perspective,
        "saved_to":         str(out_file),
        "findings_preview": review_text[:300],
    }


# ---------------------------------------------------------------------------
# Action: quality
# ---------------------------------------------------------------------------

_QUALITY_REVIEWS_DIR = KNOWLEDGE_DIR / "quality" / "reviews"
_SKILLS_DIR          = FLEET_DIR / "skills"

SEVERITY_ORDER = {"INFO": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
_KNOWN_SIDE_EFFECT_IMPORTS = {"sys", "logging", "signal", "threading"}


def _scan_file(path: Path) -> list[dict]:
    """Run AST + regex quality checks against one Python file."""
    findings = []
    name = path.name

    try:
        content = path.read_text(errors="ignore")
        lines   = content.splitlines()
    except Exception:
        return [{"file": name, "severity": "HIGH", "category": "ACCESS",
                 "line": 0, "detail": f"Cannot read file: {path}"}]

    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        return [{"file": name, "severity": "HIGH", "category": "SYNTAX",
                 "line": e.lineno or 0, "detail": f"Syntax error: {e.msg}"}]

    # bare except
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            findings.append({"file": name, "severity": "MEDIUM", "category": "BARE_EXCEPT",
                             "line": node.lineno, "detail": "Bare except — catch specific exceptions instead"})

    # mutable default arguments
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for default in node.args.defaults + node.args.kw_defaults:
                if default and isinstance(default, (ast.List, ast.Dict, ast.Set)):
                    findings.append({"file": name, "severity": "MEDIUM", "category": "MUTABLE_DEFAULT",
                                     "line": node.lineno,
                                     "detail": f"Mutable default argument in {node.name}() — use None and assign inside"})

    # star imports
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names):
            findings.append({"file": name, "severity": "MEDIUM", "category": "STAR_IMPORT",
                             "line": node.lineno,
                             "detail": f"from {node.module} import * — use explicit imports"})

    # too-long functions
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", None)
            if end and (end - node.lineno) > 80:
                findings.append({"file": name, "severity": "LOW", "category": "LONG_FUNCTION",
                                 "line": node.lineno,
                                 "detail": f"{node.name}() is {end - node.lineno} lines — consider splitting"})

    # print() in skill code
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "print":
                if name not in ("lead_client.py", "supervisor.py"):
                    findings.append({"file": name, "severity": "INFO", "category": "PRINT_STMT",
                                     "line": node.lineno,
                                     "detail": "print() in skill code — use logging instead"})

    # TODO/FIXME/HACK/XXX markers
    for i, line in enumerate(lines, 1):
        m = re.search(r"#\s*(TODO|FIXME|HACK|XXX)\b", line, re.I)
        if m:
            findings.append({"file": name, "severity": "INFO", "category": "MARKER",
                             "line": i, "detail": f"{m.group(1).upper()}: {line.strip()[:80]}"})

    # missing run() docstring
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            if not ast.get_docstring(node):
                findings.append({"file": name, "severity": "LOW", "category": "MISSING_DOCSTRING",
                                 "line": node.lineno,
                                 "detail": "run() has no docstring — fleet skills should document payload/output"})

    # unused imports
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

    non_import_lines = "\n".join(l for l in lines if not l.strip().startswith(("import ", "from ")))
    for used_name, full_name, lineno in imports:
        if used_name.startswith("_") or used_name in _KNOWN_SIDE_EFFECT_IMPORTS:
            continue
        if used_name not in non_import_lines:
            findings.append({"file": name, "severity": "LOW", "category": "UNUSED_IMPORT",
                             "line": lineno,
                             "detail": f"Import '{used_name}' ({full_name}) appears unused"})

    # inconsistent run() returns
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

    # nesting depth > 3
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


def _quality(payload: dict, config: dict) -> dict:
    """Static AST/regex quality scan across fleet Python files.

    Payload:
      target        str   specific skill or file to scan (default: all skills)
      severity_min  str   "INFO" | "LOW" | "MEDIUM" | "HIGH" (default "INFO")
      auto_fix      bool  queue skill_evolve tasks for HIGH/MEDIUM findings (default False)
    """
    target       = payload.get("target", "")
    auto_fix     = payload.get("auto_fix", False)
    min_severity = payload.get("severity_min", "INFO")
    min_level    = SEVERITY_ORDER.get(min_severity, 0)

    if target:
        target_path = _SKILLS_DIR / f"{target.replace('.py', '')}.py"
        if not target_path.exists():
            target_path = Path(target)
        if not target_path.exists():
            return {"error": f"Target not found: {target}"}
        files = [target_path]
    else:
        files = sorted(_SKILLS_DIR.glob("*.py"))
        for core in ["supervisor.py", "worker.py", "db.py", "discord_bot.py", "dashboard.py", "rag.py"]:
            p = FLEET_DIR / core
            if p.exists():
                files.append(p)

    all_findings = []
    for f in files:
        if f.name.startswith("__"):
            continue
        all_findings.extend(_scan_file(f))

    filtered = [f for f in all_findings if SEVERITY_ORDER.get(f["severity"], 0) >= min_level]
    filtered.sort(key=lambda x: SEVERITY_ORDER.get(x["severity"], 0), reverse=True)

    fixes_queued = 0
    if auto_fix:
        import sys
        sys.path.insert(0, str(FLEET_DIR))
        import db
        files_with_issues: dict[str, list] = {}
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

    # ── Delta comparison — only write report if findings changed ──
    from _delta_review import DeltaReviewer
    _QUALITY_REVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    dr = DeltaReviewer("quality", _QUALITY_REVIEWS_DIR)
    delta = dr.compare(filtered)

    if not delta["changed"] and not target:
        return {
            "status": "no_change",
            "files_scanned": len(files),
            "total_findings": len(filtered),
            "unchanged_since": delta["previous_timestamp"],
            "message": f"No new findings (unchanged: {delta['unchanged_count']})",
        }

    header_extra = [
        f"**Files scanned:** {len(files)}",
        f"**Findings:** {len(filtered)} (min severity: {min_severity})",
        f"**Auto-fix tasks queued:** {fixes_queued}",
    ]
    report_path = dr.write_report(delta, "Code Quality Review", header_extra=header_extra)

    by_sev: dict[str, int] = {}
    for f in filtered:
        by_sev[f["severity"]] = by_sev.get(f["severity"], 0) + 1

    return {
        "files_scanned":    len(files),
        "findings":         filtered[:30],
        "total_findings":   len(filtered),
        "new_findings":     len(delta["new_findings"]),
        "resolved":         len(delta["resolved_finding_keys"]),
        "by_severity":      by_sev,
        "by_category":      {k: len(v) for k, v in {cat: [f for f in filtered if f["category"] == cat] for cat in {f["category"] for f in filtered}}.items()},
        "auto_fixes_queued": fixes_queued,
        "saved_to":         str(report_path),
    }


# ---------------------------------------------------------------------------
# Action: refactor
# ---------------------------------------------------------------------------

_REFACTOR_DIR = KNOWLEDGE_DIR / "code_writes" / "refactors"

_REFACTOR_SYSTEM = """You are a senior code reviewer specializing in refactoring.
Analyze the provided code and suggest refactoring improvements.

IMPORTANT:
- Do NOT change external behavior (inputs, outputs, side effects must stay the same).
- Focus only on the requested principles.
- Be specific: reference line numbers, function names, variable names.

You MUST respond with EXACTLY this JSON format (no markdown fences, no extra text):
{
  "issues": [{"description": "...", "location": "...", "principle": "..."}],
  "changes": [{"description": "what to change", "before": "snippet", "after": "snippet"}],
  "risk": "low" or "medium" or "high",
  "refactored_code": "full refactored file content (or empty string if too large)"
}

risk levels:
- low: cosmetic/naming/formatting only
- medium: structural changes (extract function, reorder logic)
- high: changes to control flow, error handling, or data structures"""


def _refactor(payload: dict, config: dict) -> dict:
    """Suggest refactoring improvements without changing external behavior.

    Payload:
      file_path   str        path to the file to refactor (required)
      principles  list[str]  refactoring axes (default: ["DRY", "readability"])
      dry_run     bool       if True, report only; if False, include full refactored code (default True)
    """
    file_path  = payload.get("file_path", "")
    principles = payload.get("principles", ["DRY", "readability"])
    dry_run    = payload.get("dry_run", True)

    if not file_path:
        return {"status": "error", "error": "No file_path provided"}

    target = Path(file_path)
    try:
        content = target.read_text(encoding="utf-8", errors="ignore")
    except FileNotFoundError:
        return {"status": "error", "error": f"File not found: {file_path}"}
    except Exception as e:
        return {"status": "error", "error": f"Cannot read file: {e}"}

    if not content.strip():
        return {"status": "error", "error": f"File is empty: {file_path}"}

    principles_str = ", ".join(principles)
    user_prompt = (
        f"## File: {target.name}\n\n"
        f"## Refactoring Principles: {principles_str}\n\n"
        f"## Code:\n```\n{content[:8000]}\n```\n\n"
        f"Analyze this code and suggest refactoring improvements based on: {principles_str}.\n"
        f"Show the specific changes as before/after snippets. Do NOT change external behavior."
    )
    if not dry_run:
        user_prompt += "\n\nInclude the FULL refactored file content in refactored_code."

    try:
        response = call_complex(
            system=_REFACTOR_SYSTEM,
            user=user_prompt,
            config=config,
            max_tokens=2048,
            skill_name=SKILL_NAME,
        )
    except Exception as e:
        return {"status": "error", "file_path": file_path, "principles": principles,
                "error": f"Model call failed: {e}"}

    result = _extract_json(response, "issues") or {
        "issues":   [{"description": response[:500], "location": "unknown", "principle": "unknown"}],
        "changes":  [],
        "risk":     "medium",
        "refactored_code": "",
    }
    issues   = result.get("issues", [])
    changes  = result.get("changes", [])
    risk     = result.get("risk", "medium")
    if risk not in ("low", "medium", "high"):
        risk = "medium"
    refactored_code  = result.get("refactored_code", "")
    changes_summary  = [c.get("description", "unknown change") for c in changes]

    report_path = ""
    try:
        _REFACTOR_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d_%H%M")
        report   = _REFACTOR_DIR / f"{target.stem}_refactor_{date_str}.md"
        lines_out = [
            f"# Refactoring Report: `{target.name}`",
            f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"**Principles:** {principles_str}",
            f"**Risk:** {risk}",
            f"**Issues Found:** {len(issues)}",
            f"**Changes Suggested:** {len(changes)}",
            "",
            "## Issues",
        ]
        for i, issue in enumerate(issues, 1):
            lines_out.append(
                f"{i}. **{issue.get('principle', '?')}** — {issue.get('description', '?')} "
                f"(at {issue.get('location', '?')})"
            )
        lines_out += ["", "## Suggested Changes"]
        for i, change in enumerate(changes, 1):
            lines_out.append(f"### Change {i}: {change.get('description', '?')}")
            if change.get("before"):
                lines_out.append(f"**Before:**\n```\n{change['before']}\n```")
            if change.get("after"):
                lines_out.append(f"**After:**\n```\n{change['after']}\n```")
            lines_out.append("")
        if not dry_run and refactored_code:
            lines_out += ["## Full Refactored Code", f"```\n{refactored_code}\n```"]
        report.write_text("\n".join(lines_out), encoding="utf-8")
        report_path = str(report)
    except Exception:
        log.warning("refactor: failed to save report", exc_info=True)

    out: dict = {
        "file_path":        file_path,
        "principles":       principles,
        "issues_found":     len(issues),
        "changes_suggested": changes_summary,
        "risk":             risk,
        "report_path":      report_path,
        "status":           "ok",
    }
    if not dry_run:
        out["refactored_code"] = refactored_code
    return out


# ---------------------------------------------------------------------------
# Action: discuss
# ---------------------------------------------------------------------------

_DISCUSS_DIR = KNOWLEDGE_DIR / "code_discussion"

_CODE_KEYWORDS = [
    "code", "function", "class", "module", "import", "def ", "return",
    "error", "bug", "refactor", "performance", "algorithm", "sql", "api",
    "skill", "worker", "fleet", "supervisor", "db.", "payload", "config",
]


def _load_code_context(topic: str, code_context_hint: str) -> str:
    """Return code context: explicit snippet, or scan knowledge/skills for topic."""
    if code_context_hint:
        return code_context_hint[:3000]
    texts = []
    for skill_file in sorted((_FLEET_DIR := FLEET_DIR / "skills").glob("*.py"))[:5]:
        name = skill_file.stem
        if name in topic.lower() or topic.lower() in name:
            content = skill_file.read_text(encoding="utf-8")[:1200]
            texts.append(f"# {skill_file.name}\n{content}")
            break
    summaries_dir = KNOWLEDGE_DIR / "summaries"
    if summaries_dir.exists():
        for f in sorted(summaries_dir.glob("*.md"), reverse=True)[:20]:
            content = f.read_text(encoding="utf-8")
            if any(kw in content.lower() for kw in _CODE_KEYWORDS):
                texts.append(content[:600])
                if len(texts) >= 4:
                    break
    return "\n\n---\n\n".join(texts[:4]) if texts else ""


def _discuss(payload: dict, config: dict) -> dict:
    """Contribute a perspective to a code discussion thread.

    Payload:
      agent_name       str  agent doing the discussing (default "coder")
      topic            str  what to discuss (default "fleet code quality")
      role_perspective str  perspective label; auto-derived from agent_name if omitted
      round            int  discussion round (default 1)
      code_context     str  optional code snippet to focus the discussion
    """
    import sys
    sys.path.insert(0, str(FLEET_DIR))
    import db

    agent_name       = payload.get("agent_name", "coder")
    topic            = payload.get("topic", "fleet code quality")
    role_perspective = payload.get("role_perspective",
                                   PERSPECTIVE_MAP.get(agent_name, "generalist coder"))
    round_num        = payload.get("round", 1)
    code_context     = _load_code_context(topic, payload.get("code_context", ""))
    prior_discussion = _load_discussion(topic)

    system_prompt = f"""You are the {role_perspective} in a technical code review session.
As the {role_perspective}, provide your analysis of the topic.
Be specific and technical. Reference actual code patterns or line-level details where relevant.
Build on prior contributions if any exist — don't repeat what was already said.
4-6 bullet points max."""

    user_prompt = f"""TOPIC: {topic}
ROUND: {round_num}

{f"CODE CONTEXT:{chr(10)}{code_context[:3000]}" if code_context else ""}

{"PRIOR DISCUSSION:" + chr(10) + prior_discussion[:2000] if prior_discussion else "You are opening the technical discussion."}"""

    contribution = call_complex(system_prompt, user_prompt, config, skill_name=SKILL_NAME)

    db.post_message(
        from_agent=agent_name,
        to_agent="all",
        body_json=json.dumps({
            "topic":            topic,
            "round":            round_num,
            "role_perspective": role_perspective,
            "contribution":     contribution,
            "timestamp":        datetime.now().isoformat(),
        }),
        channel="agent",
    )

    _DISCUSS_DIR.mkdir(exist_ok=True)
    log_file = _DISCUSS_DIR / f"{topic[:40].replace(' ', '_')}_round{round_num}.md"
    with open(log_file, "a") as fh:
        fh.write(f"\n## [{agent_name}] — {role_perspective}\n{contribution}\n")

    return {"contribution": contribution, "topic": topic, "round": round_num}


# ---------------------------------------------------------------------------
# Action: evaluate
# ---------------------------------------------------------------------------

_EVAL_DIR = KNOWLEDGE_DIR / "evaluations"

_EVAL_SYSTEM = """You are a critical, adversarial reviewer for an AI agent fleet.
Your job is to evaluate skill outputs rigorously against given criteria.

You MUST respond with EXACTLY this JSON format (no markdown fences, no extra text):
{"verdict": "PASS" or "FAIL", "critique": "detailed critique per criterion", "suggestions": ["improvement 1", "improvement 2"]}

Rules:
- Be strict but fair. Minor style issues = PASS. Substantive gaps = FAIL.
- Critique must address EACH criterion individually.
- suggestions list is required if verdict is FAIL, optional if PASS."""


def _evaluate(payload: dict, config: dict) -> dict:
    """Adversarial PASS/FAIL evaluation of any skill output.

    Payload:
      skill_name  str        skill that produced the output (required)
      output      str|dict   the output to evaluate (required)
      criteria    list[str]  evaluation axes (default: ["accuracy", "completeness", "clarity"])
      strict      bool       include suggestions on FAIL (default False)
    """
    skill_name = payload.get("skill_name", "")
    output     = payload.get("output", "")
    criteria   = payload.get("criteria", ["accuracy", "completeness", "clarity"])
    strict     = payload.get("strict", False)

    if not skill_name:
        return {"status": "error", "verdict": "FAIL", "error": "No skill_name provided"}
    if not output:
        return {"status": "error", "verdict": "FAIL", "error": "No output provided"}

    output_str    = json.dumps(output, indent=2) if isinstance(output, dict) else str(output)
    criteria_str  = ", ".join(criteria)
    user_prompt   = (
        f"## Skill: {skill_name}\n\n"
        f"## Evaluation Criteria: {criteria_str}\n\n"
        f"## Output to Evaluate:\n```\n{output_str[:6000]}\n```\n\n"
        f"Evaluate this output against each criterion. "
        f"Give a verdict (PASS/FAIL) and specific critique per criterion."
    )
    if strict:
        user_prompt += "\n\nThis is a STRICT evaluation. If verdict is FAIL, include detailed suggested_improvements."

    try:
        response = call_complex(
            system=_EVAL_SYSTEM,
            user=user_prompt,
            config=config,
            max_tokens=1024,
            skill_name=SKILL_NAME,
        )
    except Exception as e:
        return {
            "status":    "error",
            "verdict":   "FAIL",
            "skill_name": skill_name,
            "criteria":  criteria,
            "critique":  f"Evaluation model error: {e}",
            "suggestions": [],
        }

    # Parse JSON — try direct extraction first, then simple regex pattern
    parsed = _extract_json(response, "verdict")
    if parsed is None:
        m = re.search(r'\{[^{}]*"verdict"[^{}]*\}', response, re.DOTALL)
        if m:
            try:
                parsed = json.loads(m.group())
            except (json.JSONDecodeError, TypeError):
                pass
    if parsed is None:
        upper   = response.upper()
        verdict = "FAIL" if "FAIL" in upper else "PASS"
        parsed  = {"verdict": verdict, "critique": response[:1000], "suggestions": []}

    verdict     = parsed.get("verdict", "FAIL").upper()
    critique    = parsed.get("critique", "No critique returned")
    suggestions = parsed.get("suggestions", [])

    if strict and verdict == "FAIL" and not suggestions:
        suggestions = [critique]
    if not strict and verdict == "PASS":
        suggestions = []

    try:
        _EVAL_DIR.mkdir(parents=True, exist_ok=True)
        date_str = datetime.now().strftime("%Y%m%d_%H%M")
        report   = _EVAL_DIR / f"{skill_name}_eval_{date_str}.md"
        report.write_text(
            f"# Evaluation: `{skill_name}`\n\n"
            f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"
            f"**Verdict:** {verdict}\n"
            f"**Criteria:** {criteria_str}\n\n"
            f"## Critique\n{critique}\n\n"
            f"## Suggestions\n"
            + ("\n".join(f"- {s}" for s in suggestions) if suggestions else "None")
            + "\n\n## Raw Output (truncated)\n```\n"
            + output_str[:2000]
            + "\n```\n", encoding="utf-8"
        )
    except Exception:
        log.warning("evaluate: failed to save report", exc_info=True)

    out: dict = {
        "verdict":    verdict,
        "skill_name": skill_name,
        "criteria":   criteria,
        "critique":   critique,
        "status":     "ok",
    }
    if strict or suggestions:
        out["suggestions"] = suggestions
    return out


# ---------------------------------------------------------------------------
# Action: pair
# ---------------------------------------------------------------------------

_PAIR_DIR = KNOWLEDGE_DIR / "pair_sessions"


def _pair(payload: dict, config: dict) -> dict:
    """Line-level pair-programming session: issues, suggestions, questions.

    Payload:
      file / file_path  str  path to the file to pair on (required)
      task / description str  session goal (default "General review")
    """
    task_desc = payload.get("task", payload.get("description", "General review"))
    file_path = payload.get("file", payload.get("file_path", ""))

    if not file_path:
        return {"status": "error", "message": "No file_path provided in payload"}

    target = Path(file_path)
    if not target.is_absolute():
        target = FLEET_DIR.parent / file_path

    if not target.exists():
        return {"status": "error", "message": f"File not found: {target}"}
    if not target.is_file():
        return {"status": "error", "message": f"Not a file: {target}"}

    try:
        content = target.read_text(encoding="utf-8")
    except Exception:
        log.warning("pair: cannot read %s", target, exc_info=True)
        return {"status": "error", "message": f"Cannot read file: {target}"}

    lines = content.splitlines()
    ext   = target.suffix.lower()

    issues:      list[dict] = []
    suggestions: list[dict] = []
    questions:   list[dict] = []
    stats = {
        "total_lines": len(lines), "blank_lines": 0, "comment_lines": 0,
        "functions": 0, "classes": 0, "imports": 0, "todos": 0,
    }

    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        stats["blank_lines"] += 1 if not stripped else 0

        if ext == ".py":
            if stripped.startswith("#"):
                stats["comment_lines"] += 1
            if stripped.startswith("def "):
                stats["functions"] += 1
                if i < len(lines) and '"""' not in lines[i] and "'''" not in lines[i]:
                    suggestions.append({"line": i, "type": "suggestion",
                                        "text": f"Function `{stripped.split('(')[0][4:]}` lacks a docstring"})
            if stripped.startswith("class "):
                stats["classes"] += 1
            if stripped.startswith(("import ", "from ")):
                stats["imports"] += 1

            if "except:" in stripped and "except Exception" not in stripped:
                issues.append({"line": i, "type": "issue",
                                "text": "Bare `except:` catches SystemExit/KeyboardInterrupt — use `except Exception:`"})
            if "sqlite3.connect" in stripped:
                issues.append({"line": i, "type": "issue",
                                "text": "Raw sqlite3.connect() — use db.get_conn() instead"})
            if re.search(r"urlopen\([^)]*\)$", stripped):
                issues.append({"line": i, "type": "issue",
                                "text": "urlopen() without timeout= parameter"})

        if any(tag in stripped for tag in ("TODO", "FIXME", "HACK")):
            stats["todos"] += 1
            questions.append({"line": i, "type": "question",
                               "text": f"TODO/FIXME marker: `{stripped[:80]}`"})
        if len(line) > 120:
            suggestions.append({"line": i, "type": "suggestion",
                                 "text": f"Line exceeds 120 chars ({len(line)} chars)"})

    code_lines = stats["total_lines"] - stats["blank_lines"] - stats["comment_lines"]
    if stats["functions"] > 0:
        avg_fn = code_lines / stats["functions"]
        if avg_fn > 50:
            suggestions.append({"line": 0, "type": "suggestion",
                                 "text": f"Average function length is {avg_fn:.0f} lines — consider breaking up large functions"})
    if code_lines > 20 and stats["comment_lines"] / max(code_lines, 1) < 0.05:
        suggestions.append({"line": 0, "type": "suggestion",
                             "text": "Low comment ratio (<5%) — consider adding more documentation"})

    today     = date.today().isoformat()
    safe_name = re.sub(r"[^\w.-]", "_", target.stem)
    md = [
        f"# Pair Programming Session — {today}",
        "",
        f"**Task:** {task_desc}",
        f"**File:** `{target}`",
        f"**Lines:** {stats['total_lines']} | **Functions:** {stats['functions']} "
        f"| **Classes:** {stats['classes']}",
        "",
        "---",
        "",
        "## File Statistics",
        "",
        f"- Total lines: {stats['total_lines']}",
        f"- Code lines: {code_lines}",
        f"- Blank lines: {stats['blank_lines']}",
        f"- Comment lines: {stats['comment_lines']}",
        f"- Functions: {stats['functions']}",
        f"- Classes: {stats['classes']}",
        f"- Imports: {stats['imports']}",
        f"- TODOs/FIXMEs: {stats['todos']}",
        "",
    ]
    for section, items in (("Issues", issues), ("Suggestions", suggestions), ("Questions", questions)):
        if items:
            md += [f"## {section}", ""]
            for item in items:
                loc = f"L{item['line']}" if item["line"] else "General"
                md.append(f"- **[{loc}]** {item['text']}")
            md.append("")
    if not issues and not suggestions and not questions:
        md += ["## Review", "", "No issues, suggestions, or questions found. Code looks clean.", ""]

    _PAIR_DIR.mkdir(parents=True, exist_ok=True)
    out_file = _PAIR_DIR / f"pair_{today}_{safe_name}.md"
    out_file.write_text("\n".join(md), encoding="utf-8")

    return {
        "status":      "ok",
        "saved_to":    str(out_file),
        "file":        str(target),
        "total_lines": stats["total_lines"],
        "functions":   stats["functions"],
        "classes":     stats["classes"],
        "issues":      len(issues),
        "suggestions": len(suggestions),
        "questions":   len(questions),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run(payload: dict, config: dict) -> dict:
    """Dispatch to one of 6 code-suite actions.

    Payload:
      action  str  "review" | "quality" | "refactor" | "discuss" | "evaluate" | "pair"
                   Default: "review"

    All other payload keys are forwarded to the selected action handler.
    """
    from skills._dispatch import dispatch_action
    return dispatch_action(payload, config, {
        "review":   _review,
        "quality":  _quality,
        "refactor": _refactor,
        "discuss":  _discuss,
        "evaluate": _evaluate,
        "pair":     _pair,
    }, default="review")
