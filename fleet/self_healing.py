"""Self-healing compatibility shim — imports from health_monitor.py.

All functionality has moved to health_monitor.py. This file exists
only for backward compatibility with existing imports.
"""
from health_monitor import (
    check_agent_health,
    recover_agent,
    retry_failed_task,
    circuit_breaker_record_failure,
    circuit_breaker_is_open,
    get_circuit_breaker_status,
    run_health_sweep,
    detect_skill_regression,
    get_rollback_candidates,
    rollback_skill,
    get_agent_health_summary,
    get_skill_health_summary,
    get_recovery_log,
)

__all__ = [
    "check_agent_health",
    "recover_agent",
    "retry_failed_task",
    "circuit_breaker_record_failure",
    "circuit_breaker_is_open",
    "get_circuit_breaker_status",
    "run_health_sweep",
    "detect_skill_regression",
    "get_rollback_candidates",
    "rollback_skill",
    "get_agent_health_summary",
    "get_skill_health_summary",
    "get_recovery_log",
]
