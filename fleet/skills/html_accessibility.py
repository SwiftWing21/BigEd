"""
HTML accessibility skill — WCAG 2.1 Level A checker for templates.

Pure-Python accessibility auditor using regex and html.parser.  Checks for:
  - Images without alt text
  - Form inputs without labels or aria-label
  - Links with no text content
  - Missing lang attribute on <html>
  - Missing <title> element
  - Buttons without accessible text
  - Icon-only buttons missing aria-label
  - Inline color-contrast issues (light-on-light)
  - Missing role on interactive custom elements
  - Autoplaying media
  - onclick without tabindex (not keyboard accessible)
  - Heading hierarchy skips (h1 -> h3)

Payload:
  file        str   single HTML file to check
  directory   str   directory to scan for *.html (default: fleet/templates)

Output: knowledge/quality/a11y_report_<date>.md
Returns: {files_scanned, total_issues, errors, warnings, infos, saved_to}
"""

SKILL_NAME = "html_accessibility"
DESCRIPTION = (
    "Check HTML templates for accessibility issues "
    "— missing alt text, ARIA labels, color contrast, keyboard navigation."
)
REQUIRES_NETWORK = False


def run(payload, config):
    """Scan HTML files and produce an accessibility report."""
    import re
    from datetime import date
    from pathlib import Path

    FLEET_DIR = Path(__file__).parent.parent
    KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
    QUALITY_DIR = KNOWLEDGE_DIR / "quality"
    QUALITY_DIR.mkdir(parents=True, exist_ok=True)

    target = payload.get("file")
    directory = payload.get("directory")

    if not target and not directory:
        # Default: scan all HTML templates
        directory = str(FLEET_DIR / "templates")

    files_to_check = []
    if target:
        p = Path(target)
        if not p.exists():
            p = FLEET_DIR.parent / target
        if p.exists():
            files_to_check.append(p)
    if directory:
        d = Path(directory)
        if not d.exists():
            d = FLEET_DIR.parent / directory
        if d.exists():
            files_to_check.extend(sorted(d.rglob("*.html")))

    if not files_to_check:
        return {"status": "skip", "message": "No HTML files found"}

    all_issues = []

    for file_path in files_to_check:
        try:
            html = file_path.read_text(encoding="utf-8")
            issues = _check_accessibility(html, str(file_path))
            all_issues.extend(issues)
        except Exception:
            all_issues.append({
                "file": str(file_path),
                "line": 0,
                "severity": "error",
                "rule": "parse_error",
                "message": "Failed to parse HTML file",
            })

    # Classify
    errors = [i for i in all_issues if i["severity"] == "error"]
    warnings = [i for i in all_issues if i["severity"] == "warning"]
    infos = [i for i in all_issues if i["severity"] == "info"]

    # Build markdown report
    md = [
        f"# Accessibility Report — {date.today().isoformat()}",
        "",
        (
            f"**Files scanned:** {len(files_to_check)} | "
            f"**Issues:** {len(all_issues)} "
            f"({len(errors)} errors, {len(warnings)} warnings, {len(infos)} info)"
        ),
        "",
    ]

    if errors:
        md.append("## Errors (WCAG A violations)")
        md.append("")
        for i in errors:
            fname = Path(i["file"]).name
            md.append(
                f"- **{i['rule']}** [{fname}:{i['line']}] — {i['message']}"
            )
        md.append("")

    if warnings:
        md.append("## Warnings")
        md.append("")
        for i in warnings[:30]:
            fname = Path(i["file"]).name
            md.append(
                f"- **{i['rule']}** [{fname}:{i['line']}] — {i['message']}"
            )
        if len(warnings) > 30:
            md.append(f"- ... and {len(warnings) - 30} more warnings")
        md.append("")

    if infos:
        md.append("## Info")
        md.append("")
        for i in infos[:20]:
            fname = Path(i["file"]).name
            md.append(
                f"- **{i['rule']}** [{fname}:{i['line']}] — {i['message']}"
            )
        md.append("")

    if not all_issues:
        md.append("## All Clear")
        md.append("")
        md.append("No accessibility issues found.")

    report = "\n".join(md)
    out_file = QUALITY_DIR / f"a11y_report_{date.today().isoformat()}.md"
    out_file.write_text(report, encoding="utf-8")

    return {
        "status": "ok",
        "saved_to": str(out_file),
        "files_scanned": len(files_to_check),
        "total_issues": len(all_issues),
        "errors": len(errors),
        "warnings": len(warnings),
        "infos": len(infos),
    }


# ── internal helpers ───────────────────────────────────────────────

def _check_accessibility(html, file_path):
    """Check a single HTML file for accessibility issues."""
    import re

    issues = []
    lines = html.split("\n")

    for line_no, line in enumerate(lines, 1):
        # 1. Images without alt
        for match in re.finditer(r"<img\b[^>]*>", line, re.IGNORECASE):
            tag = match.group()
            if "alt=" not in tag.lower():
                issues.append({
                    "file": file_path, "line": line_no,
                    "severity": "error", "rule": "img_no_alt",
                    "message": "Image missing alt attribute",
                })

        # 2. Input without label / aria-label
        for match in re.finditer(r"<input\b[^>]*>", line, re.IGNORECASE):
            tag = match.group()
            tag_lower = tag.lower()
            if 'type="hidden"' not in tag_lower and 'type="submit"' not in tag_lower:
                if "aria-label" not in tag_lower and "id=" not in tag_lower:
                    issues.append({
                        "file": file_path, "line": line_no,
                        "severity": "warning", "rule": "input_no_label",
                        "message": (
                            "Input without aria-label or id "
                            "(for label association)"
                        ),
                    })

        # 3. onclick without tabindex or role (non-native interactive)
        for match in re.finditer(
            r"<(?!button|a|input|select|textarea)\w+[^>]*\bonclick\b[^>]*>",
            line,
            re.IGNORECASE,
        ):
            tag = match.group()
            tag_lower = tag.lower()
            if "tabindex" not in tag_lower and "role=" not in tag_lower:
                issues.append({
                    "file": file_path, "line": line_no,
                    "severity": "warning", "rule": "onclick_no_keyboard",
                    "message": (
                        "Element with onclick but no tabindex/role "
                        "— not keyboard accessible"
                    ),
                })

        # 4. Empty links
        if re.search(r"<a\b[^>]*>\s*</a>", line, re.IGNORECASE):
            issues.append({
                "file": file_path, "line": line_no,
                "severity": "error", "rule": "empty_link",
                "message": "Link with no text content",
            })

        # 5. Vague link text
        if re.search(
            r"<a\b[^>]*>\s*(click here|here|more|read more)\s*</a>",
            line,
            re.IGNORECASE,
        ):
            issues.append({
                "file": file_path, "line": line_no,
                "severity": "warning", "rule": "vague_link_text",
                "message": "Link text is vague (e.g. 'click here')",
            })

        # 6. Autoplay media
        if re.search(
            r"<(video|audio)\b[^>]*\bautoplay\b", line, re.IGNORECASE
        ):
            issues.append({
                "file": file_path, "line": line_no,
                "severity": "warning", "rule": "autoplay",
                "message": "Media with autoplay — may disrupt screen readers",
            })

        # 7. Buttons without accessible text
        if re.search(r"<button\b[^>]*>\s*</button>", line, re.IGNORECASE):
            tag_match = re.search(
                r"<button\b[^>]*>", line, re.IGNORECASE
            )
            if tag_match:
                tag = tag_match.group().lower()
                if "aria-label" not in tag and "title=" not in tag:
                    issues.append({
                        "file": file_path, "line": line_no,
                        "severity": "error", "rule": "button_no_text",
                        "message": (
                            "Button with no text and no aria-label"
                        ),
                    })

        # 8. Icon-only buttons (contains <i> or <svg> but no text)
        if re.search(
            r"<button\b[^>]*>\s*<(?:i|svg)\b[^>]*>.*?</(?:i|svg)>\s*</button>",
            line,
            re.IGNORECASE,
        ):
            tag_match = re.search(
                r"<button\b[^>]*>", line, re.IGNORECASE
            )
            if tag_match:
                tag = tag_match.group().lower()
                if "aria-label" not in tag:
                    issues.append({
                        "file": file_path, "line": line_no,
                        "severity": "warning",
                        "rule": "icon_button_no_label",
                        "message": (
                            "Icon-only button missing aria-label"
                        ),
                    })

        # 9. Inline color contrast (light text on light background)
        for match in re.finditer(
            r'style="[^"]*color:\s*#([0-9a-fA-F]{3,6})[^"]*'
            r'background(?:-color)?:\s*#([0-9a-fA-F]{3,6})',
            line,
            re.IGNORECASE,
        ):
            fg = _hex_luminance(match.group(1))
            bg = _hex_luminance(match.group(2))
            if fg is not None and bg is not None:
                ratio = _contrast_ratio(fg, bg)
                if ratio < 4.5:
                    issues.append({
                        "file": file_path, "line": line_no,
                        "severity": "warning",
                        "rule": "low_contrast",
                        "message": (
                            f"Possible low contrast ({ratio:.1f}:1, "
                            f"WCAG AA requires 4.5:1)"
                        ),
                    })

    # ── document-level checks ──────────────────────────────────────
    html_lower = html.lower()

    # 10. Missing lang on <html>
    if "<html" in html_lower:
        html_tag = html_lower.split("<html")[1].split(">")[0]
        if "lang=" not in html_tag:
            issues.append({
                "file": file_path, "line": 1,
                "severity": "error", "rule": "no_lang",
                "message": "Missing lang attribute on <html>",
            })

    # 11. Missing <title>
    if "<title>" not in html_lower and "<html" in html_lower:
        issues.append({
            "file": file_path, "line": 1,
            "severity": "warning", "rule": "no_title",
            "message": "Missing <title> element",
        })

    # 12. Heading hierarchy skips
    headings = []
    for line_no, line in enumerate(lines, 1):
        for match in re.finditer(r"<h(\d)\b", line, re.IGNORECASE):
            headings.append((int(match.group(1)), line_no))

    for i in range(1, len(headings)):
        curr_level, curr_line = headings[i]
        prev_level, _ = headings[i - 1]
        if curr_level > prev_level + 1:
            issues.append({
                "file": file_path, "line": curr_line,
                "severity": "warning", "rule": "heading_skip",
                "message": f"Heading skip: h{prev_level} -> h{curr_level}",
            })

    # 13. Missing role on custom interactive elements
    import re as _re
    for line_no, line in enumerate(lines, 1):
        for match in _re.finditer(
            r"<(?!button|a|input|select|textarea|option|label|form|"
            r"details|summary|dialog)\w+-\w+[^>]*>",
            line,
            re.IGNORECASE,
        ):
            tag = match.group().lower()
            if "role=" not in tag and (
                "onclick" in tag
                or "tabindex" in tag
                or "ng-click" in tag
                or "@click" in tag
            ):
                issues.append({
                    "file": file_path, "line": line_no,
                    "severity": "info", "rule": "custom_no_role",
                    "message": (
                        "Custom interactive element missing role attribute"
                    ),
                })

    return issues


def _hex_luminance(hex_str):
    """Relative luminance from a 3- or 6-char hex color string."""
    try:
        h = hex_str.strip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) != 6:
            return None
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        # sRGB → linear
        vals = []
        for c in (r, g, b):
            s = c / 255.0
            vals.append(s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4)
        return 0.2126 * vals[0] + 0.7152 * vals[1] + 0.0722 * vals[2]
    except Exception:
        return None


def _contrast_ratio(lum1, lum2):
    """WCAG contrast ratio between two relative luminances."""
    lighter = max(lum1, lum2)
    darker = min(lum1, lum2)
    return (lighter + 0.05) / (darker + 0.05)
