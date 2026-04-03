"""ScoreRift Claim Schema and Gap Taxonomy for fleet audit system.

Fleet's own copy of the ScoreRift claim types — not imported from
the ScoreRift codebase.  Provides typed divergence classification and a
human-readable tension report.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

log = logging.getLogger("tba_claim_schema")

# ── Threshold ────────────────────────────────────────────────────────────

DIVERGENCE_THRESHOLD = 0.15

# ── Enums ────────────────────────────────────────────────────────────────


class GapType(str, Enum):
    STALE_OPTIMISM = "stale_optimism"
    METRIC_BLINDNESS = "metric_blindness"
    FALSE_POSITIVE = "false_positive"
    CONTEXT_GAP = "context_gap"
    AGREEMENT = "agreement"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ClaimDimension(str, Enum):
    SECURITY = "security"
    PERFORMANCE = "performance"
    CORRECTNESS = "correctness"
    MAINTAINABILITY = "maintainability"
    TEST_COVERAGE = "test_coverage"
    ARCHITECTURE = "architecture"
    THREAD_SAFETY = "thread_safety"
    ERROR_HANDLING = "error_handling"


# ── Severity ordering (for sorting) ─────────────────────────────────────

_SEVERITY_ORDER = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MEDIUM: 2,
    Severity.LOW: 3,
}

_SEVERITY_ICON = {
    Severity.CRITICAL: "\U0001f534",  # red circle
    Severity.HIGH: "\U0001f7e0",      # orange circle
    Severity.MEDIUM: "\U0001f7e1",    # yellow circle
    Severity.LOW: "\U0001f535",        # blue circle
}

# ── Dataclasses ──────────────────────────────────────────────────────────


@dataclass
class Claim:
    source: str
    dimension: ClaimDimension
    statement: str
    confidence: float
    evidence: str
    file_path: Optional[str] = None
    line_range: Optional[tuple[int, int]] = None
    tags: list[str] = field(default_factory=list)


@dataclass
class Divergence:
    gap_type: GapType
    severity: Severity
    dimension: ClaimDimension
    auto_claim: Optional[Claim]
    manual_claim: Optional[Claim]
    explanation: str
    file_path: Optional[str] = None
    actionable: bool = True


# ── Qualitative keywords that signal context-sensitive gaps ──────────────

_CONTEXT_KEYWORDS = frozenset({
    "thread", "concurren", "deploy", "runtime", "environment",
    "race condition", "state", "side effect",
})


def _has_context_keywords(claim: Claim) -> bool:
    """Check if a claim's statement or evidence contains qualitative keywords."""
    text = (claim.statement + " " + claim.evidence).lower()
    return any(kw in text for kw in _CONTEXT_KEYWORDS)


# ── Classification ───────────────────────────────────────────────────────


def classify_divergence(
    auto: Optional[Claim],
    manual: Optional[Claim],
    *,
    last_review_timestamp: Optional[float] = None,
    last_commit_timestamp: Optional[float] = None,
) -> Divergence:
    """Classify the divergence between an auto claim and a manual claim.

    Returns a Divergence with gap_type, severity, and explanation.
    """
    dimension = (auto or manual).dimension  # type: ignore[union-attr]
    file_path = (auto.file_path if auto else None) or (manual.file_path if manual else None)

    # One side missing
    if auto is None or manual is None:
        return Divergence(
            gap_type=GapType.CONTEXT_GAP,
            severity=Severity.MEDIUM,
            dimension=dimension,
            auto_claim=auto,
            manual_claim=manual,
            explanation="Only one brain produced a claim for this dimension.",
            file_path=file_path,
            actionable=True,
        )

    auto_high = auto.confidence >= 0.5
    manual_high = manual.confidence >= 0.5
    gap = abs(auto.confidence - manual.confidence)

    # Staleness check takes priority when timestamps are available
    if (
        last_commit_timestamp is not None
        and last_review_timestamp is not None
        and last_commit_timestamp > last_review_timestamp
    ):
        return Divergence(
            gap_type=GapType.STALE_OPTIMISM,
            severity=Severity.MEDIUM,
            dimension=dimension,
            auto_claim=auto,
            manual_claim=manual,
            explanation=(
                "Manual review predates the latest commit — "
                "the manual grade may be stale."
            ),
            file_path=file_path,
            actionable=True,
        )

    # Both aligned within threshold
    if gap <= DIVERGENCE_THRESHOLD:
        return Divergence(
            gap_type=GapType.AGREEMENT,
            severity=Severity.LOW,
            dimension=dimension,
            auto_claim=auto,
            manual_claim=manual,
            explanation="Auto and manual claims are aligned.",
            file_path=file_path,
            actionable=False,
        )

    # Auto high, manual low — context gap or metric blindness
    if auto_high and not manual_high:
        if _has_context_keywords(manual):
            return Divergence(
                gap_type=GapType.CONTEXT_GAP,
                severity=Severity.HIGH,
                dimension=dimension,
                auto_claim=auto,
                manual_claim=manual,
                explanation=(
                    "Auto scored high but manual flagged qualitative concerns "
                    f"(keywords matched in manual evidence)."
                ),
                file_path=file_path,
                actionable=True,
            )
        return Divergence(
            gap_type=GapType.METRIC_BLINDNESS,
            severity=Severity.MEDIUM,
            dimension=dimension,
            auto_claim=auto,
            manual_claim=manual,
            explanation=(
                "Auto scored high but manual scored low — "
                "the automated check may miss qualitative issues."
            ),
            file_path=file_path,
            actionable=True,
        )

    # Auto low, manual high — false positive (not actionable)
    if not auto_high and manual_high:
        return Divergence(
            gap_type=GapType.FALSE_POSITIVE,
            severity=Severity.LOW,
            dimension=dimension,
            auto_claim=auto,
            manual_claim=manual,
            explanation=(
                "Auto scored low but manual scored high — "
                "the automated check may be overly strict."
            ),
            file_path=file_path,
            actionable=False,
        )

    # Both high or both low but gap > threshold — generic metric blindness
    return Divergence(
        gap_type=GapType.METRIC_BLINDNESS,
        severity=Severity.MEDIUM,
        dimension=dimension,
        auto_claim=auto,
        manual_claim=manual,
        explanation=(
            f"Confidence gap ({gap:.2f}) exceeds threshold "
            f"({DIVERGENCE_THRESHOLD}) despite same direction."
        ),
        file_path=file_path,
        actionable=True,
    )


def classify_divergences(
    auto_claims: list[Claim],
    manual_claims: list[Claim],
    **kwargs,
) -> list[Divergence]:
    """Batch-classify divergences by matching claims on (file_path, dimension)."""
    auto_index: dict[tuple, Claim] = {}
    for c in auto_claims:
        key = (c.file_path, c.dimension)
        auto_index[key] = c

    manual_index: dict[tuple, Claim] = {}
    for c in manual_claims:
        key = (c.file_path, c.dimension)
        manual_index[key] = c

    all_keys = set(auto_index.keys()) | set(manual_index.keys())
    divergences: list[Divergence] = []
    for key in sorted(all_keys, key=lambda k: (k[0] or "", k[1].value)):
        auto = auto_index.get(key)
        manual = manual_index.get(key)
        div = classify_divergence(auto, manual, **kwargs)
        divergences.append(div)
    return divergences


# ── Tension Report ───────────────────────────────────────────────────────


def tension_report(divergences: list[Divergence]) -> str:
    """Generate a markdown tension report from a list of divergences.

    Sorted by severity (critical first). Agreements are listed but dimmed.
    """
    if not divergences:
        return "## Tension Report\n\nNo divergences detected.\n"

    sorted_divs = sorted(
        divergences,
        key=lambda d: _SEVERITY_ORDER.get(d.severity, 99),
    )

    lines = ["## Tension Report", ""]
    for d in sorted_divs:
        icon = _SEVERITY_ICON.get(d.severity, "")
        sev_label = d.severity.value.upper()
        dim_label = d.dimension.value.replace("_", " ").title()
        gap_label = d.gap_type.value.replace("_", " ").title()
        actionable_tag = "" if d.actionable else " *(no action needed)*"

        lines.append(
            f"- {icon} **{sev_label}** | {dim_label} | {gap_label}{actionable_tag}"
        )
        lines.append(f"  {d.explanation}")
        if d.file_path:
            lines.append(f"  File: `{d.file_path}`")
        lines.append("")

    return "\n".join(lines)
