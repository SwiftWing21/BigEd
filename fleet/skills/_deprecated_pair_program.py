"""
Pair programming skill — reads a target file, generates structured review
comments (issues, suggestions, questions), saves a session transcript.
"""
SKILL_NAME = "pair_program"
DESCRIPTION = "Facilitate AI pair programming sessions with structured code review."
VERSION = "1.0.0"
COMPLEXITY = "medium"
REQUIRES_NETWORK = False


def run(payload, config):
    import logging
    import re
    from datetime import date
    from pathlib import Path

    log = logging.getLogger(SKILL_NAME)

    FLEET_DIR = Path(__file__).parent.parent
    KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
    PAIR_DIR = KNOWLEDGE_DIR / "pair_sessions"
    PAIR_DIR.mkdir(parents=True, exist_ok=True)

    task_desc = payload.get("task", payload.get("description", "General review"))
    file_path = payload.get("file", payload.get("file_path", ""))

    if not file_path:
        return {"status": "error", "message": "No file_path provided in payload"}

    target = Path(file_path)
    if not target.is_absolute():
        # Resolve relative to project root
        target = FLEET_DIR.parent / file_path

    if not target.exists():
        return {"status": "error", "message": f"File not found: {target}"}

    if not target.is_file():
        return {"status": "error", "message": f"Not a file: {target}"}

    # Read the target file
    try:
        content = target.read_text(encoding="utf-8")
    except Exception:
        log.warning("Cannot read file %s", target, exc_info=True)
        return {"status": "error", "message": f"Cannot read file: {target}"}

    lines = content.splitlines()
    ext = target.suffix.lower()

    # Analyze the file
    issues = []
    suggestions = []
    questions = []
    stats = {
        "total_lines": len(lines),
        "blank_lines": sum(1 for l in lines if not l.strip()),
        "comment_lines": 0,
        "functions": 0,
        "classes": 0,
        "imports": 0,
        "todos": 0,
    }

    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        # Python-specific analysis
        if ext == ".py":
            if stripped.startswith("#"):
                stats["comment_lines"] += 1
            if stripped.startswith("def "):
                stats["functions"] += 1
                # Check for missing docstring
                if i < len(lines) and '"""' not in lines[i] and "'''" not in lines[i]:
                    suggestions.append({
                        "line": i, "type": "suggestion",
                        "text": f"Function `{stripped.split('(')[0][4:]}` "
                                f"lacks a docstring",
                    })
            if stripped.startswith("class "):
                stats["classes"] += 1
            if stripped.startswith("import ") or stripped.startswith("from "):
                stats["imports"] += 1

            # Common issues
            if "except:" in stripped and "except Exception" not in stripped:
                issues.append({
                    "line": i, "type": "issue",
                    "text": "Bare `except:` catches SystemExit/KeyboardInterrupt "
                            "— use `except Exception:`",
                })
            if "sqlite3.connect" in stripped:
                issues.append({
                    "line": i, "type": "issue",
                    "text": "Raw sqlite3.connect() — use db.get_conn() instead",
                })
            if re.search(r"urlopen\([^)]*\)$", stripped):
                issues.append({
                    "line": i, "type": "issue",
                    "text": "urlopen() without timeout= parameter",
                })

        # General checks
        if "TODO" in stripped or "FIXME" in stripped or "HACK" in stripped:
            stats["todos"] += 1
            questions.append({
                "line": i, "type": "question",
                "text": f"TODO/FIXME marker: `{stripped[:80]}`",
            })
        if len(line) > 120:
            suggestions.append({
                "line": i, "type": "suggestion",
                "text": f"Line exceeds 120 chars ({len(line)} chars)",
            })

    # Complexity heuristic
    code_lines = stats["total_lines"] - stats["blank_lines"] - stats["comment_lines"]
    if stats["functions"] > 0:
        avg_fn_size = code_lines / stats["functions"]
        if avg_fn_size > 50:
            suggestions.append({
                "line": 0, "type": "suggestion",
                "text": f"Average function length is {avg_fn_size:.0f} lines "
                        f"— consider breaking up large functions",
            })

    # Comment ratio
    if code_lines > 20 and stats["comment_lines"] / max(code_lines, 1) < 0.05:
        suggestions.append({
            "line": 0, "type": "suggestion",
            "text": "Low comment ratio (<5%) — consider adding more documentation",
        })

    # Build session transcript
    today = date.today().isoformat()
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

    if issues:
        md.append("## Issues")
        md.append("")
        for item in issues:
            loc = f"L{item['line']}" if item["line"] else "General"
            md.append(f"- **[{loc}]** {item['text']}")
        md.append("")

    if suggestions:
        md.append("## Suggestions")
        md.append("")
        for item in suggestions:
            loc = f"L{item['line']}" if item["line"] else "General"
            md.append(f"- **[{loc}]** {item['text']}")
        md.append("")

    if questions:
        md.append("## Questions")
        md.append("")
        for item in questions:
            loc = f"L{item['line']}" if item["line"] else "General"
            md.append(f"- **[{loc}]** {item['text']}")
        md.append("")

    if not issues and not suggestions and not questions:
        md.append("## Review")
        md.append("")
        md.append("No issues, suggestions, or questions found. Code looks clean.")
        md.append("")

    report = "\n".join(md)
    out_file = PAIR_DIR / f"pair_{today}_{safe_name}.md"
    out_file.write_text(report, encoding="utf-8")

    return {
        "status": "ok",
        "saved_to": str(out_file),
        "file": str(target),
        "total_lines": stats["total_lines"],
        "functions": stats["functions"],
        "classes": stats["classes"],
        "issues": len(issues),
        "suggestions": len(suggestions),
        "questions": len(questions),
    }
