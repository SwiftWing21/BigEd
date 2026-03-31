"""Tests for miscellaneous audit fixes."""
import sys
sys.path.insert(0, "fleet")
import inspect


def test_providers_no_conn_close():
    from providers import get_agent_affinity
    source = inspect.getsource(get_agent_affinity)
    assert "conn.close()" not in source, "Must not close pooled connections"


def test_billing_fails_closed():
    from billing import check_quota
    source = inspect.getsource(check_quota)
    lines = source.split("\n")
    in_except = False
    for line in lines:
        if "except" in line and "Exception" in line:
            in_except = True
        if in_except and '"exceeded"' in line:
            assert "True" in line, f"Must fail closed with exceeded=True, found: {line.strip()}"
            break


def test_filesystem_guard_audit_log_warning():
    from filesystem_guard import FileSystemGuard
    source = inspect.getsource(FileSystemGuard)
    # The OSError handler must log at warning level, not debug
    assert "logger.warning" in source and "audit log" in source.lower(), \
        "Audit log failure must log at WARNING level"
