"""Diagnostics compatibility shim — imports from health_monitor.py.

All functionality has moved to health_monitor.py. This file exists
only for backward compatibility with existing imports.
"""
from health_monitor import (
    quarantine_agent,
    clear_quarantine,
    get_failure_streaks,
    get_stuck_reviews,
)

__all__ = [
    "quarantine_agent",
    "clear_quarantine",
    "get_failure_streaks",
    "get_stuck_reviews",
]
