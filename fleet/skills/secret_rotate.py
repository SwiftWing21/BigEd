"""0.06.00: Secret rotation — auto-rotate API keys on DLP alerts or schedule."""
import json
import os
import time
from datetime import datetime
from pathlib import Path

SKILL_NAME = "secret_rotate"
DESCRIPTION = "Rotate API keys — automated for Slack/AWS, semi-automated for others"
REQUIRES_NETWORK = True

FLEET_DIR = Path(__file__).parent.parent
SECRETS_FILE = Path.home() / ".secrets"
METADATA_FILE = FLEET_DIR / "data" / "secret_metadata.json"

# Provider capabilities
PROVIDERS = {
    "ANTHROPIC_API_KEY": {"auto_rotate": False, "console_url": "https://console.anthropic.com/settings/keys"},
    "GEMINI_API_KEY": {"auto_rotate": False, "console_url": "https://aistudio.google.com/apikey"},
    "GITHUB_TOKEN": {"auto_rotate": False, "console_url": "https://github.com/settings/tokens"},
    "SLACK_BOT_TOKEN": {"auto_rotate": True, "method": "slack_api"},
    "AWS_ACCESS_KEY_ID": {"auto_rotate": True, "method": "boto3_iam"},
    "TAVILY_API_KEY": {"auto_rotate": False, "console_url": "https://app.tavily.com"},
}


def run(payload: dict, config: dict) -> str:
    action = payload.get("action", "status")

    if action == "status":
        return _get_status()
    elif action == "rotate":
        key_name = payload.get("key")
        return _rotate_key(key_name)
    elif action == "check_age":
        return _check_key_ages(payload.get("max_days", 90))
    elif action == "deactivate":
        return _deactivate_key(payload.get("key"))
    else:
        return json.dumps({"error": f"Unknown action: {action}"})


def _load_metadata():
    """Load secret metadata (creation dates, rotation history)."""
    if METADATA_FILE.exists():
        return json.loads(METADATA_FILE.read_text(encoding="utf-8"))
    return {}


def _save_metadata(data):
    """Save secret metadata."""
    METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    METADATA_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _get_status():
    """Check rotation status of all configured keys."""
    metadata = _load_metadata()
    keys = {}

    if SECRETS_FILE.exists():
        for line in SECRETS_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith("export ") and "=" in line:
                key_name = line.split("=", 1)[0].replace("export ", "").strip()
                value = line.split("=", 1)[1].strip().strip("'\"")

                provider = PROVIDERS.get(key_name, {})
                meta = metadata.get(key_name, {})

                keys[key_name] = {
                    "configured": bool(value),
                    "masked": value[:6] + "..." + value[-4:] if len(value) > 12 else "***",
                    "auto_rotate": provider.get("auto_rotate", False),
                    "last_rotated": meta.get("last_rotated"),
                    "age_days": _key_age_days(meta),
                    "console_url": provider.get("console_url"),
                }

    return json.dumps({"keys": keys, "total": len(keys)})


def _key_age_days(meta):
    """Calculate key age in days."""
    if not meta.get("last_rotated"):
        return None
    try:
        rotated = datetime.fromisoformat(meta["last_rotated"])
        return (datetime.now() - rotated).days
    except Exception:
        return None


def _check_key_ages(max_days):
    """Check for keys older than max_days."""
    metadata = _load_metadata()
    expired = []
    for key_name, meta in metadata.items():
        age = _key_age_days(meta)
        if age and age > max_days:
            expired.append({"key": key_name, "age_days": age, "max_days": max_days})
    return json.dumps({"expired": expired, "checked": len(metadata)})


def _rotate_key(key_name):
    """Rotate a specific key. Auto for Slack/AWS, semi-auto for others."""
    if not key_name:
        return json.dumps({"error": "key name required"})

    provider = PROVIDERS.get(key_name, {})

    if provider.get("auto_rotate"):
        method = provider.get("method")
        if method == "slack_api":
            return _rotate_slack()
        elif method == "boto3_iam":
            return _rotate_aws()

    # Semi-automated: deactivate old + instruct operator
    return json.dumps({
        "status": "semi_automated",
        "key": key_name,
        "instruction": f"1. Go to {provider.get('console_url', 'provider console')}\n"
                       f"2. Create a new key\n"
                       f"3. Run: lead_client.py secret set {key_name} <new_value>\n"
                       f"4. The old key remains active until you deactivate it",
        "console_url": provider.get("console_url"),
    })


def _rotate_slack():
    """Rotate Slack bot token via tooling.tokens.rotate API."""
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    refresh = os.environ.get("SLACK_REFRESH_TOKEN", "")
    if not token or not refresh:
        return json.dumps({"error": "SLACK_BOT_TOKEN and SLACK_REFRESH_TOKEN required"})

    import urllib.request
    try:
        data = json.dumps({
            "refresh_token": refresh,
            "grant_type": "refresh_token",
        }).encode()
        req = urllib.request.Request(
            "https://slack.com/api/tooling.tokens.rotate",
            data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())

        if result.get("ok"):
            new_token = result["token"]
            new_refresh = result["refresh_token"]
            _update_secret("SLACK_BOT_TOKEN", new_token)
            _update_secret("SLACK_REFRESH_TOKEN", new_refresh)
            _record_rotation("SLACK_BOT_TOKEN")
            return json.dumps({"status": "rotated", "key": "SLACK_BOT_TOKEN"})
        return json.dumps({"error": f"Slack rotation failed: {result.get('error')}"})
    except Exception as e:
        return json.dumps({"error": f"Slack rotation error: {e}"})


def _rotate_aws():
    """Rotate AWS access key via boto3 IAM."""
    try:
        import boto3
        iam = boto3.client("iam")
        user = iam.get_user()["User"]["UserName"]

        # Create new key
        new_key = iam.create_access_key(UserName=user)["AccessKey"]
        new_id = new_key["AccessKeyId"]
        new_secret = new_key["SecretAccessKey"]

        # Update secrets file
        old_id = os.environ.get("AWS_ACCESS_KEY_ID", "")
        _update_secret("AWS_ACCESS_KEY_ID", new_id)
        _update_secret("AWS_SECRET_ACCESS_KEY", new_secret)

        # Deactivate old key
        if old_id:
            iam.update_access_key(UserName=user, AccessKeyId=old_id, Status="Inactive")

        _record_rotation("AWS_ACCESS_KEY_ID")
        return json.dumps({"status": "rotated", "key": "AWS_ACCESS_KEY_ID", "new_id": new_id[:8] + "..."})
    except Exception as e:
        return json.dumps({"error": f"AWS rotation error: {e}"})


def _deactivate_key(key_name):
    """Deactivate (not delete) an old key at the provider."""
    if key_name == "ANTHROPIC_API_KEY":
        # Anthropic Admin API can deactivate
        admin_key = os.environ.get("ANTHROPIC_ADMIN_KEY", "")
        if not admin_key:
            return json.dumps({"error": "ANTHROPIC_ADMIN_KEY required for deactivation"})
        # Would call Admin API here
        return json.dumps({"status": "not_implemented", "note": "Anthropic Admin API deactivation planned"})
    return json.dumps({"status": "manual", "instruction": f"Deactivate {key_name} manually at provider console"})


def _update_secret(key_name, value):
    """Atomically update a secret in ~/.secrets and os.environ."""
    lines = []
    if SECRETS_FILE.exists():
        lines = SECRETS_FILE.read_text(encoding="utf-8").splitlines()
    prefix = f"export {key_name}="
    lines = [l for l in lines if not l.startswith(prefix)]
    lines.append(f"export {key_name}='{value}'")
    tmp = SECRETS_FILE.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tmp.replace(SECRETS_FILE)
    # Live update
    os.environ[key_name] = value


def _record_rotation(key_name):
    """Record rotation timestamp in metadata."""
    metadata = _load_metadata()
    metadata[key_name] = {
        "last_rotated": datetime.now().isoformat(),
        "rotated_by": "secret_rotate",
    }
    _save_metadata(metadata)
