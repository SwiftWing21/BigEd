"""
Key manager skill — scans skill files for API key usage, cross-references
keys_registry.toml, infers purpose for unknown keys, and can suggest or
apply wiring for newly added keys.

Actions:
  scan     — discover all env vars used across skills, report vs registry
  status   — check which registered keys are set in ~/.secrets
  infer    — use Ollama to infer purpose + suggest skill wiring for a new key
  wire     — insert env usage into a skill file (requires approval payload)
"""
import os
import re
from pathlib import Path

from skills._models import call_complex

FLEET_DIR    = Path(__file__).parent.parent
SKILLS_DIR   = FLEET_DIR / "skills"
REGISTRY_FILE = FLEET_DIR / "keys_registry.toml"
SECRETS_FILE  = Path.home() / ".secrets"
SKILL_NAME = "key_manager"
DESCRIPTION = "Key manager skill — scans skill files for API key usage, cross-references"

REQUIRES_NETWORK = True



def _load_registry():
    """Parse keys_registry.toml into a dict keyed by env_var name."""
    try:
        import tomllib
        with open(REGISTRY_FILE, "rb") as f:
            data = tomllib.load(f)
        return {k["env_var"]: k for k in data.get("key", [])}
    except Exception:
        return {}


def _scan_skills():
    """
    Regex-scan all skill .py files for os.environ references.
    Returns dict: {env_var_name: [skill_file, ...]}
    """
    pattern = re.compile(
        r'os\.environ(?:\.get)?\(\s*["\']([A-Z_][A-Z0-9_]+)["\']'
        r'|environ\[["\']([A-Z_][A-Z0-9_]+)["\']\]'
        r'|config\[["\']([a-z_]+)["\']\]\[["\']([a-z_]+)["\']\]',
        re.MULTILINE
    )
    usage = {}
    for skill_file in SKILLS_DIR.glob("*.py"):
        if skill_file.name == "__init__.py":
            continue
        try:
            content = skill_file.read_text(errors="ignore")
            for m in pattern.finditer(content):
                var = next((g for g in m.groups() if g), None)
                if var:
                    usage.setdefault(var, []).append(skill_file.stem)
        except Exception:
            pass
    return usage


def _read_secrets_status():
    """
    Read ~/.secrets and return dict of {KEY_NAME: masked_value}.
    Keys present but empty are marked as EMPTY.
    """
    status = {}
    if not SECRETS_FILE.exists():
        return status
    try:
        for line in SECRETS_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:]
            if line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if val and val != "REPLACE_ME":
                # Mask: show first 6 + ... + last 4
                if len(val) > 12:
                    status[key] = val[:6] + "..." + val[-4:]
                else:
                    status[key] = "***set***"
            else:
                status[key] = "EMPTY"
    except Exception:
        pass
    return status


def _write_secret(key_name, value):
    """
    Upsert KEY=value in ~/.secrets.
    Updates existing line or appends new one.
    """
    if not SECRETS_FILE.exists():
        SECRETS_FILE.write_text("")
    lines = SECRETS_FILE.read_text().splitlines()
    updated = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("export "):
            stripped = stripped[7:].strip()
        if stripped.startswith(f"{key_name}="):
            new_lines.append(f"export {key_name}={value}")
            updated = True
        else:
            new_lines.append(line)
    if not updated:
        new_lines.append(f"export {key_name}={value}")
    SECRETS_FILE.write_text("\n".join(new_lines) + "\n")
    return True


def _infer_purpose(key_name, config):
    """Use Ollama to infer purpose and suggest skill wiring for an unknown key."""
    registry = _load_registry()
    skill_names = [f.stem for f in SKILLS_DIR.glob("*.py") if f.stem != "__init__"]
    existing_keys = list(registry.keys())

    system = "You are a software architect reviewing an API key configuration for a local AI agent fleet."

    user = f"""Key name: {key_name}

Available skills in the fleet: {', '.join(skill_names)}
Already registered keys: {', '.join(existing_keys)}

Based on the key name alone, provide:
1. Most likely service/provider this key belongs to
2. Purpose in 1 sentence
3. Which of the available skills could use this key (list up to 3)
4. Suggested os.environ.get() usage snippet for a Python skill file
5. Pricing tier guess: free / freemium / paid

Be concise. Format as:
SERVICE: ...
PURPOSE: ...
SKILLS: ...
USAGE: ...
TIER: ..."""

    return call_complex(system, user, config)


def _suggest_wiring(key_name, target_skill, config):
    """
    Read a skill file and suggest where/how to add the new key usage.
    Returns a suggestion string — does NOT modify the file without approval.
    """
    skill_file = SKILLS_DIR / f"{target_skill}.py"
    if not skill_file.exists():
        return f"Skill '{target_skill}' not found."

    content = skill_file.read_text(errors="ignore")[:3000]

    system = "You are reviewing a Python skill file to suggest where to add a new API key."

    user = f"""Key to add: {key_name}
Skill file: {target_skill}.py

File content (truncated):
{content}

Suggest the minimal code change to add {key_name} usage to this skill.
Show the exact line(s) to add and where to insert them.
Be specific: include the os.environ.get() call and how to use it in context.
Keep it short — just the diff, no explanation."""

    return call_complex(system, user, config)


def run(payload, config):
    action = payload.get("action", "scan")

    if action == "scan":
        usage    = _scan_skills()
        registry = _load_registry()
        secrets  = _read_secrets_status()

        # Keys in code but not in registry (undocumented)
        unknown = {k: v for k, v in usage.items() if k not in registry}
        # Registered keys not found in any skill
        unwired = {k: v for k, v in registry.items()
                   if v["env_var"] not in usage and v.get("skills")}
        # Registered keys that ARE wired
        wired   = {k: usage.get(k, []) for k in registry}

        report_lines = ["# Key Manager Scan Report", ""]
        report_lines.append("## Registered Keys")
        for name, info in registry.items():
            in_code  = name in usage
            is_set   = name in secrets and secrets[name] != "EMPTY"
            code_sym = "✓ wired" if in_code else "✗ not in skills"
            set_sym  = "✓ set"   if is_set  else "✗ missing"
            report_lines.append(
                f"- **{name}** [{info['tier']}] — {info['purpose'][:60]}")
            report_lines.append(
                f"  - Secret: {set_sym}  |  Code: {code_sym}"
                + (f" ({', '.join(usage[name])})" if in_code else ""))

        if unknown:
            report_lines += ["", "## Unknown Keys (in code, not in registry)"]
            for name, skills in unknown.items():
                report_lines.append(f"- **{name}** used in: {', '.join(skills)}")
                report_lines.append("  → Run `key_manager infer` to document this key")

        if unwired:
            report_lines += ["", "## Registered but Unwired"]
            for name in unwired:
                report_lines.append(
                    f"- **{name}** is documented but not called in any skill")
                report_lines.append(
                    f"  → Run `key_manager wire` to add usage to: "
                    + ", ".join(registry[name].get("skills", [])))

        report = "\n".join(report_lines)
        reports_dir = FLEET_DIR / "knowledge" / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        (reports_dir / "key_scan.md").write_text(report)
        return {
            "wired": len(wired),
            "unknown": list(unknown.keys()),
            "unwired": list(unwired.keys()),
            "missing_secrets": [k for k in registry if secrets.get(k, "EMPTY") == "EMPTY"],
            "report": report,
        }

    elif action == "status":
        registry = _load_registry()
        secrets  = _read_secrets_status()
        out = {}
        for name, info in registry.items():
            out[name] = {
                "label":   info["label"],
                "set":     name in secrets and secrets[name] != "EMPTY",
                "masked":  secrets.get(name, "not set"),
                "tier":    info["tier"],
                "purpose": info["purpose"],
            }
        return {"keys": out}

    elif action == "infer":
        key_name = payload.get("key_name", "")
        if not key_name:
            return {"error": "key_name required for infer action"}
        result = _infer_purpose(key_name, config)
        return {"key_name": key_name, "inference": result}

    elif action == "wire":
        key_name     = payload.get("key_name", "")
        target_skill = payload.get("skill", "")
        approved     = payload.get("approved", False)
        if not key_name or not target_skill:
            return {"error": "key_name and skill required"}
        suggestion = _suggest_wiring(key_name, target_skill, config)
        return {
            "key_name":    key_name,
            "skill":       target_skill,
            "suggestion":  suggestion,
            "note":        "Review suggestion above. Re-run with approved=true to apply." if not approved else "approved=true — manual edit required, auto-apply not implemented for safety.",
        }

    elif action == "set":
        key_name = payload.get("key_name", "")
        value    = payload.get("value", "")
        if not key_name or not value:
            return {"error": "key_name and value required"}
        _write_secret(key_name, value)
        os.environ[key_name] = value
        return {"status": "ok", "key": key_name, "note": "Written to ~/.secrets. Restart fleet to propagate."}

    else:
        return {"error": f"Unknown action: {action}. Use: scan, status, infer, wire, set"}