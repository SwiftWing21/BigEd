# fleet/skills/_report.py
"""Markdown report builder for skill output."""
from datetime import date


class ReportBuilder:
    """Fluent builder for markdown reports."""

    def __init__(self, title: str, include_date: bool = True):
        self._lines: list[str] = []
        date_str = f" -- {date.today().isoformat()}" if include_date else ""
        self._lines.append(f"# {title}{date_str}")
        self._lines.append("")

    def metadata(self, **kwargs) -> "ReportBuilder":
        """Add a metadata line: **Key:** value | **Key2:** value2."""
        parts = [f"**{k}:** {v}" for k, v in kwargs.items()]
        self._lines.append(" | ".join(parts))
        self._lines.append("")
        return self

    def section(self, heading: str, body: str = "", level: int = 2) -> "ReportBuilder":
        """Add a section heading with optional body text."""
        prefix = "#" * level
        self._lines.append(f"{prefix} {heading}")
        self._lines.append("")
        if body:
            self._lines.append(body)
            self._lines.append("")
        return self

    def table(self, headers: list[str], rows: list[list]) -> "ReportBuilder":
        """Add a markdown table."""
        self._lines.append("| " + " | ".join(str(h) for h in headers) + " |")
        self._lines.append("|" + "|".join("---" for _ in headers) + "|")
        for row in rows:
            self._lines.append("| " + " | ".join(str(c) for c in row) + " |")
        self._lines.append("")
        return self

    def findings(self, items: list[dict], severity_key: str = "severity",
                 detail_key: str = "detail") -> "ReportBuilder":
        """Add a findings list: - [SEVERITY] detail."""
        for item in items:
            sev = item.get(severity_key, "NOTE")
            det = item.get(detail_key, "")
            self._lines.append(f"- [{sev}] {det}")
        self._lines.append("")
        return self

    def line(self, text: str) -> "ReportBuilder":
        """Add a raw line."""
        self._lines.append(text)
        return self

    def blank(self) -> "ReportBuilder":
        """Add a blank line."""
        self._lines.append("")
        return self

    def build(self) -> str:
        """Build the final markdown string."""
        return "\n".join(self._lines)
