# fleet/skills/_contract.py
"""Skill contract validator — checks module compliance without breaking anything."""
import inspect
import logging

log = logging.getLogger(__name__)

REQUIRED_CONSTANTS = ("SKILL_NAME", "DESCRIPTION")
OPTIONAL_CONSTANTS = {
    "VERSION": "0.0.0",
    "REQUIRES_NETWORK": False,
    "COMPLEXITY": "medium",  # matches providers.py fallback default
    "TIMEOUT": 600,
    "SUITE": "",
    "TAGS": [],
}


def validate_skill(module) -> list[str]:
    """Return list of contract violations (empty = compliant)."""
    warnings = []
    for const in REQUIRED_CONSTANTS:
        if not hasattr(module, const):
            warnings.append(f"missing {const}")

    if not hasattr(module, "VERSION"):
        warnings.append("missing VERSION (defaulting to 0.0.0)")

    if not hasattr(module, "run") or not callable(module.run):
        warnings.append("missing callable run()")
        return warnings

    sig = inspect.signature(module.run)
    params = list(sig.parameters.keys())
    if len(params) < 2:
        warnings.append(f"run() has {len(params)} params, need at least 2")
    if len(params) > 2:
        third = params[2]
        if sig.parameters[third].default is inspect.Parameter.empty:
            warnings.append(
                f"run() 3rd param '{third}' has no default — will crash at runtime"
            )
    if sig.return_annotation is str:
        warnings.append("run() -> str annotation: will cause double-serialization")

    return warnings


def get_metadata(module) -> dict:
    """Extract contract metadata from a skill module."""
    meta = {}
    for const in REQUIRED_CONSTANTS:
        meta[const.lower()] = getattr(module, const, None)
    for const, default in OPTIONAL_CONSTANTS.items():
        meta[const.lower()] = getattr(module, const, default)
    return meta
