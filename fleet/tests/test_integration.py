#!/usr/bin/env python3
"""Integration tests — cross-module interactions without a running dashboard.

Tests import chains, function signatures, DB table creation, and module
connectivity. Each test returns (name, passed, detail) for a summary report.

Usage:
    python fleet/tests/test_integration.py
    python -m pytest fleet/tests/test_integration.py -v
"""

import os
import sys
from pathlib import Path

# ── Path setup ───────────────────────────────────────────────────────
FLEET_DIR = str(Path(__file__).resolve().parent.parent)
if FLEET_DIR not in sys.path:
    sys.path.insert(0, FLEET_DIR)

# Use in-memory DB to avoid polluting real fleet.db
os.environ.setdefault("FLEET_TEST_DB", ":memory:")

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"

results: list[tuple[str, bool, str]] = []


def record(name: str, passed: bool, detail: str):
    """Store and print one test result."""
    tag = PASS if passed else FAIL
    print(f"  [{tag}] {name}: {detail}")
    results.append((name, passed, detail))


# ── Test 1: Intent parse ─────────────────────────────────────────────

def test_intent_parse():
    """Import intent.parse_intent_with_maintainer, call with test text."""
    try:
        from intent import parse_intent_with_maintainer
        result = parse_intent_with_maintainer("search for machine learning papers")
        if not isinstance(result, tuple) or len(result) != 2:
            record("Intent parse", False, f"expected 2-tuple, got {type(result)}")
            return
        skill, payload = result
        if not isinstance(skill, str) or not isinstance(payload, dict):
            record("Intent parse", False,
                   f"expected (str, dict), got ({type(skill).__name__}, {type(payload).__name__})")
            return
        record("Intent parse", True, f"skill={skill!r}, payload keys={list(payload.keys())}")
    except Exception as e:
        record("Intent parse", False, str(e))


# ── Test 2: MCP Server object ────────────────────────────────────────

def test_mcp_server_loads():
    """Import mcp_server, verify mcp object exists with name 'BigEd Fleet'."""
    try:
        from mcp_server import mcp
        name = getattr(mcp, "name", None)
        if name != "BigEd Fleet":
            record("MCP Server loads", False, f"expected 'BigEd Fleet', got {name!r}")
            return
        record("MCP Server loads", True, f"mcp.name={name!r}")
    except Exception as e:
        record("MCP Server loads", False, str(e))


# ── Test 3: Skill catalog ────────────────────────────────────────────

def test_skill_catalog():
    """Scan fleet/skills/*.py for SKILL_NAME, verify 80+ skills."""
    try:
        skills_dir = Path(FLEET_DIR) / "skills"
        count = 0
        for py_file in sorted(skills_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
                for line in content.splitlines():
                    if line.startswith("SKILL_NAME"):
                        count += 1
                        break
            except Exception:
                continue
        passed = count >= 80
        record("Skill catalog", passed, f"{count} skills found (need 80+)")
    except Exception as e:
        record("Skill catalog", False, str(e))


# ── Test 4: Discovery module ─────────────────────────────────────────

def test_discovery_peers():
    """Import discovery, call get_discovered_peers(), expect empty list."""
    try:
        from discovery import get_discovered_peers
        peers = get_discovered_peers()
        if not isinstance(peers, list):
            record("Discovery peers", False, f"expected list, got {type(peers).__name__}")
            return
        record("Discovery peers", True, f"{len(peers)} peers (expected 0 on local)")
    except Exception as e:
        record("Discovery peers", False, str(e))


# ── Test 5: Self-healing callable ────────────────────────────────────

def test_self_healing():
    """Import self_healing, verify run_health_sweep is callable."""
    try:
        from self_healing import run_health_sweep
        if not callable(run_health_sweep):
            record("Self-healing", False, "run_health_sweep is not callable")
            return
        record("Self-healing", True, "run_health_sweep is callable")
    except Exception as e:
        record("Self-healing", False, str(e))


# ── Test 6: DAG builder ──────────────────────────────────────────────

def test_dag_builder():
    """Build a DAG from description, verify returns list of task dicts."""
    try:
        from dag_builder import build_dag_from_description
        dag = build_dag_from_description("review code then summarize")
        if not isinstance(dag, list):
            record("DAG builder", False, f"expected list, got {type(dag).__name__}")
            return
        if len(dag) == 0:
            record("DAG builder", False, "DAG is empty")
            return
        # Each entry should be a dict with at least 'skill'
        first = dag[0]
        if not isinstance(first, dict):
            record("DAG builder", False, f"first entry is {type(first).__name__}, expected dict")
            return
        record("DAG builder", True, f"{len(dag)} steps, first={first.get('skill', '?')}")
    except Exception as e:
        record("DAG builder", False, str(e))


# ── Test 7: Billing tables ───────────────────────────────────────────

def test_billing_tables():
    """Call ensure_billing_tables(), verify no error."""
    try:
        from billing import ensure_billing_tables
        ensure_billing_tables()
        record("Billing tables", True, "ensure_billing_tables() OK")
    except Exception as e:
        record("Billing tables", False, str(e))


# ── Test 8: Marketplace tables ────────────────────────────────────────

def test_marketplace_tables():
    """Import marketplace, verify table init works."""
    try:
        from marketplace import _ensure_tables
        _ensure_tables()
        record("Marketplace tables", True, "_ensure_tables() OK")
    except Exception as e:
        record("Marketplace tables", False, str(e))


# ── Test 9: Compliance callable ───────────────────────────────────────

def test_compliance():
    """Import compliance, verify get_compliance_status is callable."""
    try:
        from compliance import get_compliance_status
        if not callable(get_compliance_status):
            record("Compliance", False, "get_compliance_status not callable")
            return
        record("Compliance", True, "get_compliance_status is callable")
    except Exception as e:
        record("Compliance", False, str(e))


# ── Test 10: DB cancel_task ───────────────────────────────────────────

def test_db_cancel_task():
    """Import db, verify cancel_task function exists."""
    try:
        import db
        if not hasattr(db, "cancel_task"):
            record("DB cancel_task", False, "db.cancel_task not found")
            return
        if not callable(db.cancel_task):
            record("DB cancel_task", False, "db.cancel_task not callable")
            return
        record("DB cancel_task", True, "db.cancel_task exists and is callable")
    except Exception as e:
        record("DB cancel_task", False, str(e))


# ── Test 11: DB FORWARDED status ──────────────────────────────────────

def test_db_forwarded_status():
    """Verify FORWARDED is in db.VALID_TASK_STATUSES."""
    try:
        import db
        if not hasattr(db, "VALID_TASK_STATUSES"):
            record("DB FORWARDED status", False, "VALID_TASK_STATUSES not found")
            return
        has_forwarded = "FORWARDED" in db.VALID_TASK_STATUSES
        record("DB FORWARDED status", has_forwarded,
               f"statuses={sorted(db.VALID_TASK_STATUSES)}")
    except Exception as e:
        record("DB FORWARDED status", False, str(e))


# ── Runner ────────────────────────────────────────────────────────────

ALL_TESTS = [
    test_intent_parse,
    test_mcp_server_loads,
    test_skill_catalog,
    test_discovery_peers,
    test_self_healing,
    test_dag_builder,
    test_billing_tables,
    test_marketplace_tables,
    test_compliance,
    test_db_cancel_task,
    test_db_forwarded_status,
]


def main():
    print("Integration Tests")
    print("=" * 50)

    for fn in ALL_TESTS:
        fn()

    print("=" * 50)
    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"Result: {passed}/{total} passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
