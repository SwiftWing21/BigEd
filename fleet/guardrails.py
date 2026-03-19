"""
Optional guardrails integration — provides configurable safety rails for skill outputs.
Works standalone (no NeMo dependency required) with optional NeMo Guardrails enhancement.

Usage:
    from guardrails import evaluate_output, GuardrailConfig

    config = GuardrailConfig(toxicity=True, factuality=True, pii=True)
    result = evaluate_output(text, config)
"""
import json
import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GuardrailConfig:
    """Configuration for which guardrails to apply."""
    toxicity: bool = True
    pii_detection: bool = True
    factuality: bool = False  # requires LLM call
    topic_rails: list = field(default_factory=list)  # blocked topics
    max_output_length: int = 0  # 0 = no limit
    custom_validators: list = field(default_factory=list)


@dataclass
class GuardrailResult:
    """Result of guardrail evaluation."""
    passed: bool
    findings: list  # list of {type, severity, detail}
    original_text: str
    sanitized_text: Optional[str] = None  # text with PII/secrets redacted


# Built-in validators
_TOXICITY_PATTERNS = [
    re.compile(r'\b(kill|murder|bomb|attack|hack|exploit)\b.*\b(how|instructions|guide|steps)\b', re.I),
]

_BLOCKED_OUTPUT_PATTERNS = [
    re.compile(r'(?:as an ai|i cannot|i\'m unable|i don\'t have the ability)', re.I),
]


def check_toxicity(text: str) -> list:
    """Check for potentially harmful content patterns."""
    findings = []
    for pattern in _TOXICITY_PATTERNS:
        if pattern.search(text):
            findings.append({
                "type": "toxicity",
                "severity": "high",
                "detail": f"Harmful instruction pattern detected",
            })
    return findings


def check_pii(text: str) -> list:
    """Check for PII in output text."""
    findings = []
    patterns = {
        "email": re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
        "ssn": re.compile(r'\b\d{3}[-.]?\d{2}[-.]?\d{4}\b'),
        "credit_card": re.compile(r'\b(?:\d{4}[-\s]?){3}\d{4}\b'),
        "phone": re.compile(r'\b\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'),
    }
    for pii_type, pattern in patterns.items():
        matches = pattern.findall(text)
        if matches:
            findings.append({
                "type": "pii",
                "severity": "medium",
                "detail": f"{pii_type}: {len(matches)} instance(s) found",
            })
    return findings


def check_refusal(text: str) -> list:
    """Check if the model refused to answer (wasted tokens)."""
    findings = []
    for pattern in _BLOCKED_OUTPUT_PATTERNS:
        if pattern.search(text[:200]):  # check start of response
            findings.append({
                "type": "refusal",
                "severity": "low",
                "detail": "Model appears to have refused the request",
            })
            break
    return findings


def check_length(text: str, max_length: int) -> list:
    """Check output length limit."""
    if max_length > 0 and len(text) > max_length:
        return [{
            "type": "length",
            "severity": "low",
            "detail": f"Output {len(text)} chars exceeds limit {max_length}",
        }]
    return []


def check_topic_rails(text: str, blocked_topics: list) -> list:
    """Check if output discusses blocked topics."""
    findings = []
    text_lower = text.lower()
    for topic in blocked_topics:
        if topic.lower() in text_lower:
            findings.append({
                "type": "topic_rail",
                "severity": "medium",
                "detail": f"Blocked topic detected: {topic}",
            })
    return findings


def sanitize_pii(text: str) -> str:
    """Redact PII from text."""
    text = re.sub(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', '[EMAIL]', text)
    text = re.sub(r'\b\d{3}[-.]?\d{2}[-.]?\d{4}\b', '[SSN]', text)
    text = re.sub(r'\b(?:\d{4}[-\s]?){3}\d{4}\b', '[CARD]', text)
    text = re.sub(r'\b\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b', '[PHONE]', text)
    return text


def evaluate_output(text: str, config: GuardrailConfig = None) -> GuardrailResult:
    """Run all configured guardrails on output text.

    Returns GuardrailResult with pass/fail, findings, and optionally sanitized text.
    """
    if config is None:
        config = GuardrailConfig()

    findings = []

    if config.toxicity:
        findings.extend(check_toxicity(text))

    if config.pii_detection:
        findings.extend(check_pii(text))

    findings.extend(check_refusal(text))

    if config.max_output_length:
        findings.extend(check_length(text, config.max_output_length))

    if config.topic_rails:
        findings.extend(check_topic_rails(text, config.topic_rails))

    # Run custom validators
    for validator in config.custom_validators:
        try:
            result = validator(text)
            if result:
                findings.extend(result if isinstance(result, list) else [result])
        except Exception:
            pass

    # Determine pass/fail (high severity = fail)
    passed = not any(f["severity"] == "high" for f in findings)

    # Sanitize if PII found
    sanitized = sanitize_pii(text) if any(f["type"] == "pii" for f in findings) else None

    return GuardrailResult(
        passed=passed,
        findings=findings,
        original_text=text[:100] + "..." if len(text) > 100 else text,
        sanitized_text=sanitized,
    )
