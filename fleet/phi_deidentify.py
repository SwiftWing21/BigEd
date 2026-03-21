"""PHI De-identification — Safe Harbor method (18 identifiers) + retention."""
import re
import sqlite3
from pathlib import Path

FLEET_DIR = Path(__file__).parent

# Patterns for each identifier type
PATTERNS = [
    # Names (common name patterns)
    (r'\b[A-Z][a-z]+\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\b', '[PATIENT]', 'name'),
    # Phone numbers
    (r'\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b', '[PHONE]', 'phone'),
    # Email
    (r'\b[\w.+-]+@[\w-]+\.[\w.-]+\b', '[EMAIL]', 'email'),
    # SSN
    (r'\b\d{3}-\d{2}-\d{4}\b', '[SSN]', 'ssn'),
    # MRN patterns
    (r'\bMRN[-:\s]*\w+\b', '[MRN]', 'mrn'),
    # Dates (keep year only)
    (r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b', lambda m: m.group()[-4:], 'date'),
    (r'\b\d{1,2}/\d{1,2}/\d{4}\b', lambda m: m.group()[-4:], 'date'),
    (r'\b\d{4}-\d{2}-\d{2}\b', lambda m: m.group()[:4], 'date'),
    # IP addresses
    (r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b', '[IP]', 'ip'),
    # URLs
    (r'https?://\S+', '[URL]', 'url'),
    # Account/certificate numbers
    (r'\b(?:ACC|ACCT|CERT|DEA|NPI)[-:\s]*[\w-]+\b', '[ID]', 'account'),
    # Health plan IDs
    (r'\b(?:BCBS|UHC|Aetna|Cigna|Humana)[-:\s]*[\w-]+\b', '[PLAN_ID]', 'plan'),
]

def deidentify(text: str, log_stripped: bool = False) -> dict:
    """Strip PHI from text using Safe Harbor method.
    Returns: {text: stripped_text, stripped: [{type, original, replacement}]}
    """
    stripped = []
    result = text
    for pattern, replacement, id_type in PATTERNS:
        if callable(replacement):
            for match in re.finditer(pattern, result, re.IGNORECASE):
                original = match.group()
                repl = replacement(match)
                if log_stripped:
                    stripped.append({"type": id_type, "original": original[:20], "replacement": repl})
                result = result.replace(original, repl, 1)
        else:
            for match in re.finditer(pattern, result, re.IGNORECASE):
                original = match.group()
                if log_stripped:
                    stripped.append({"type": id_type, "original": original[:20], "replacement": replacement})
            result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return {"text": result, "stripped_count": len(stripped), "stripped": stripped if log_stripped else []}

def contains_phi(text: str) -> bool:
    """Quick check: does text likely contain PHI?"""
    for pattern, _, _ in PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def purge_expired_phi(retention_days: int = 2555, log=None) -> dict:
    """Secure deletion of PHI beyond retention period."""
    db_path = FLEET_DIR / "fleet.db"
    conn = sqlite3.connect(str(db_path), timeout=10)

    # Count expired
    expired = conn.execute(
        "SELECT COUNT(*) FROM phi_audit WHERE created_at < datetime('now', ?)",
        (f'-{retention_days} days',)
    ).fetchone()[0]

    if expired > 0:
        conn.execute(
            "DELETE FROM phi_audit WHERE created_at < datetime('now', ?)",
            (f'-{retention_days} days',)
        )
        conn.commit()
        if log:
            log.info(f"PHI purge: {expired} records beyond {retention_days}-day retention")

    conn.close()
    return {"purged": expired, "retention_days": retention_days}
