"""
CVE watch skill — checks project dependencies against the OSV.dev
vulnerability database and generates a security advisory report.
"""
import json
import re
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

SKILL_NAME = "cve_watch"
DESCRIPTION = "Check dependencies against OSV vulnerability database for new CVEs."
REQUIRES_NETWORK = True

FLEET_DIR = Path(__file__).parent.parent
PROJECT_DIR = FLEET_DIR.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
SECURITY_DIR = KNOWLEDGE_DIR / "security"


def _parse_req_line(line):
    """Parse a single requirements.txt line into (name, version) or None."""
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


def _discover_deps():
    """Collect dependencies from requirements.txt and pyproject.toml."""
    deps = {}

    # Project-root requirements.txt
    req_file = PROJECT_DIR / "requirements.txt"
    if req_file.exists():
        for line in req_file.read_text(encoding="utf-8").splitlines():
            parsed = _parse_req_line(line)
            if parsed:
                deps[parsed[0]] = parsed[1]

    # pyproject.toml (simple regex — avoids toml import)
    pyproject = PROJECT_DIR / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
            for match in re.finditer(
                r'"([a-zA-Z0-9_.-]+)\s*(?:[><=!~]+\s*([0-9][0-9a-zA-Z.*-]*))?',
                text,
            ):
                name = match.group(1).lower().replace("-", "_")
                version = match.group(2) or ""
                if name not in deps:
                    deps[name] = version
        except Exception:
            pass

    # Fleet-specific requirements.txt
    fleet_req = FLEET_DIR / "requirements.txt"
    if fleet_req.exists():
        for line in fleet_req.read_text(encoding="utf-8").splitlines():
            parsed = _parse_req_line(line)
            if parsed and parsed[0] not in deps:
                deps[parsed[0]] = parsed[1]

    return deps


def _query_osv(pkg_name, version):
    """Query OSV.dev for vulnerabilities on a single PyPI package."""
    query = {"package": {"name": pkg_name, "ecosystem": "PyPI"}}
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


def _extract_severity(vuln):
    """Derive a severity label from an OSV vuln entry."""
    for s in vuln.get("severity", []):
        if s.get("type") == "CVSS_V3":
            score_str = s.get("score", "")
            # Try to extract the base score number from the vector string
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
            return "high"  # CVSS present but unparseable — assume high
    return "unknown"


def _build_report(checked, total, vulnerabilities):
    """Render the Markdown report."""
    critical = [v for v in vulnerabilities if v["severity"] in ("critical", "high")]
    medium = [v for v in vulnerabilities if v["severity"] == "medium"]
    low = [v for v in vulnerabilities if v["severity"] in ("low", "unknown")]

    md = [
        f"# CVE Watch Report — {date.today().isoformat()}",
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
            md.append(f"### {v['id']} — {v['package']} {v['version']}")
            md.append(f"- **Summary:** {v['summary']}")
            md.append(f"- **Aliases:** {aliases}")
            md.append(
                f"- **Published:** "
                f"{v['published'][:10] if v['published'] else 'unknown'}"
            )
            md.append("")

    if medium:
        md.append("## Medium Severity")
        md.append("")
        for v in medium:
            md.append(
                f"- **{v['id']}** — {v['package']} {v['version']}: "
                f"{v['summary'][:100]}"
            )
        md.append("")

    if low:
        md.append("## Low / Unknown Severity")
        md.append("")
        for v in low:
            md.append(
                f"- **{v['id']}** — {v['package']} {v['version']}: "
                f"{v['summary'][:100]}"
            )
        md.append("")

    if not vulnerabilities:
        md.append("## No Vulnerabilities Found")
        md.append("")
        md.append(f"All {checked} checked packages are clean.")
        md.append("")

    return "\n".join(md), critical, medium, low


def run(payload, config):
    """Check project dependencies against the OSV vulnerability database."""
    SECURITY_DIR.mkdir(parents=True, exist_ok=True)

    deps = _discover_deps()
    if not deps:
        return {"status": "skip", "message": "No dependencies found to check"}

    vulnerabilities = []
    checked = 0
    errors = 0

    for pkg_name, version in deps.items():
        try:
            result = _query_osv(pkg_name, version)
            for v in result.get("vulns", []):
                vulnerabilities.append({
                    "package": pkg_name,
                    "version": version,
                    "id": v.get("id", "unknown"),
                    "summary": v.get("summary", "No summary"),
                    "severity": _extract_severity(v),
                    "aliases": v.get("aliases", []),
                    "published": v.get("published", ""),
                })
            checked += 1
        except urllib.error.HTTPError:
            checked += 1  # 404 means no vulns — that's fine
        except Exception:
            errors += 1
            if errors > 5:
                break  # Don't hammer API on repeated failures

    report, critical, medium, low = _build_report(
        checked, len(deps), vulnerabilities
    )

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
            f"CRITICAL: {len(critical)} high-severity CVEs found "
            f"— review {out_file.name}"
        )

    return result
