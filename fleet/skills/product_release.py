"""
Product release — tags a product branch, generates a changelog from commits
and discussion history, and optionally creates a GitHub release.

Payload:
  product_name    str   e.g. "edd", "eddie" (required)
  version         str   semver tag (e.g. "0.1.0") — auto-increments if omitted
  create_release  bool  create GitHub release via gh CLI (default false)

Output: knowledge/releases/<product>_v<version>.md
Returns: {product, version, tag, changelog_preview, saved_to}
"""
import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
KNOWLEDGE_DIR = FLEET_DIR / "knowledge"
RELEASES_DIR = KNOWLEDGE_DIR / "releases"
REQUIRES_NETWORK = True


def _git(args, cwd):
    env = os.environ.copy()
    pat = os.environ.get("GITHUB_PAT", "")
    if pat:
        env["GIT_ASKPASS"] = "/bin/true"
        env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        r = subprocess.run(
            ["git"] + args, cwd=str(cwd),
            capture_output=True, text=True, timeout=30, env=env,
        )
        return r.returncode, r.stdout.strip()
    except Exception as e:
        return 1, str(e)


def _get_latest_tag(cwd, product_name):
    """Find the latest version tag for this product."""
    code, out = _git(["tag", "-l", f"{product_name}/v*", "--sort=-v:refname"], cwd)
    tags = [t.strip() for t in out.splitlines() if t.strip()]
    if tags:
        match = re.search(r"v(\d+\.\d+\.\d+)", tags[0])
        if match:
            return match.group(1)
    return None


def _bump_version(version: str) -> str:
    """Increment patch version."""
    parts = version.split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    return ".".join(parts)


def _generate_changelog(cwd, product_name, since_tag=None):
    """Generate changelog from git log."""
    if since_tag:
        code, out = _git(["log", f"{since_tag}..HEAD", "--oneline", "--no-decorate"], cwd)
    else:
        code, out = _git(["log", "--oneline", "--no-decorate", "-20"], cwd)

    commits = [line.strip() for line in out.splitlines() if line.strip()]

    lines = [
        f"# {product_name} Release Changelog",
        f"**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "## Changes",
    ]
    for commit in commits[:20]:
        lines.append(f"- {commit}")
    if not commits:
        lines.append("- Initial release")

    return "\n".join(lines)


def run(payload, config):
    product_name = payload.get("product_name", "")
    version = payload.get("version", "")
    create_release = payload.get("create_release", False)

    if not product_name:
        return {"error": "product_name required"}

    agents_dir = KNOWLEDGE_DIR / "biged_agents"
    if not (agents_dir / ".git").exists():
        return {"error": "BigEds_Agents not cloned — run branch_manager create first"}

    branch = f"product/{product_name}"
    _git(["fetch", "origin"], agents_dir)
    code, _ = _git(["checkout", branch], agents_dir)
    if code != 0:
        return {"error": f"Branch {branch} not found"}

    # Determine version
    latest = _get_latest_tag(agents_dir, product_name)
    if not version:
        version = _bump_version(latest) if latest else "0.1.0"

    tag = f"{product_name}/v{version}"

    # Generate changelog
    since = f"{product_name}/v{latest}" if latest else None
    changelog = _generate_changelog(agents_dir, product_name, since)

    # Save changelog
    RELEASES_DIR.mkdir(parents=True, exist_ok=True)
    release_file = RELEASES_DIR / f"{product_name}_v{version}.md"
    release_file.write_text(changelog)

    # Tag
    _git(["tag", "-a", tag, "-m", f"Release {tag}"], agents_dir)
    _git(["push", "origin", tag], agents_dir)

    # GitHub release (optional)
    gh_output = ""
    if create_release:
        try:
            r = subprocess.run(
                ["gh", "release", "create", tag,
                 "--repo", "SwiftWing21/BigEds_Agents",
                 "--title", f"{product_name} v{version}",
                 "--notes", changelog[:3000]],
                capture_output=True, text=True, timeout=30,
            )
            gh_output = r.stdout + r.stderr
        except Exception as e:
            gh_output = f"gh release failed: {e}"

    return {
        "product": product_name,
        "version": version,
        "tag": tag,
        "changelog_preview": changelog[:500],
        "saved_to": str(release_file),
        "gh_release": gh_output[:300] if create_release else "skipped",
    }
