#!/usr/bin/env python3
"""
Comprehensive REST endpoint test suite for the BigEd Fleet Dashboard.

Tests every registered API endpoint for:
  - HTTP connectivity and correct status codes (200/201/400/404/500)
  - Valid JSON response bodies
  - Expected top-level keys in the response payload

Usage:
    python fleet/tests/test_endpoints.py                      # live test against running dashboard
    python fleet/tests/test_endpoints.py --dry-run             # list all endpoints without hitting them
    python fleet/tests/test_endpoints.py --base-url http://host:port  # custom base URL

Requirements: Python 3.10+ stdlib only (no external deps).
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import List, Optional

DEFAULT_BASE_URL = "http://127.0.0.1:5555"
HTTP_TIMEOUT = 15  # seconds per request


# ── Test definition ──────────────────────────────────────────────────────────

@dataclass
class EndpointTest:
    """Single endpoint test specification."""
    method: str
    path: str
    group: str
    expected_status: int = 200
    expected_keys: List[str] = field(default_factory=list)
    body: Optional[dict] = None
    description: str = ""


# ── Test catalog — organised by feature group ────────────────────────────────

ENDPOINT_TESTS: List[EndpointTest] = [
    # ── Federation (v0.100) ──────────────────────────────────────────────────
    EndpointTest("GET", "/api/federation/discovered", "Federation",
                 expected_keys=["peers"]),
    EndpointTest("GET", "/api/federation/capacity", "Federation",
                 expected_keys=["total_capacity"]),
    EndpointTest("GET", "/api/federation/routing-stats", "Federation",
                 expected_keys=["stats"]),
    EndpointTest("GET", "/api/federation/cert-status", "Federation",
                 expected_keys=["tls_enabled"]),
    EndpointTest("GET", "/api/federation/hitl", "Federation",
                 expected_keys=["pending"]),
    EndpointTest("GET", "/api/cluster/agents", "Federation",
                 expected_keys=["agents"]),
    EndpointTest("GET", "/api/cluster/tasks", "Federation",
                 expected_keys=["tasks"]),
    EndpointTest("GET", "/api/cluster/metrics", "Federation",
                 expected_keys=["metrics"]),

    # ── Health (v0.200) ──────────────────────────────────────────────────────
    EndpointTest("GET", "/api/health/agents", "Health",
                 expected_keys=["agents"]),
    EndpointTest("GET", "/api/health/skills", "Health",
                 expected_keys=["skills"]),
    EndpointTest("GET", "/api/health/circuit-breakers", "Health",
                 expected_keys=["breakers"]),
    EndpointTest("GET", "/api/health/rollback-candidates", "Health",
                 expected_keys=["candidates"]),
    EndpointTest("GET", "/api/health/recovery-log", "Health",
                 expected_keys=["log"]),

    # ── Routing / ML (v0.200) ────────────────────────────────────────────────
    EndpointTest("GET", "/api/routing/model-status", "Routing",
                 expected_keys=["model"]),
    EndpointTest("GET", "/api/recommendations/popular", "Routing",
                 expected_keys=["popular"]),
    EndpointTest("GET", "/api/experiments", "Routing",
                 expected_keys=["experiments"]),
    EndpointTest("GET", "/api/scaling/prediction", "Routing",
                 expected_keys=["prediction"]),

    # ── DAG (v0.200) ────────────────────────────────────────────────────────
    EndpointTest("POST", "/api/dag/create", "DAG", expected_status=200,
                 expected_keys=["dag"],
                 body={"description": "review code then summarize"},
                 description="Create DAG from natural language"),

    # ── Billing (v0.300) ─────────────────────────────────────────────────────
    EndpointTest("GET", "/api/billing/pricing", "Billing",
                 expected_keys=["tiers"]),
    EndpointTest("GET", "/api/billing/overview", "Billing",
                 expected_keys=["tenants"]),

    # ── Compliance (v0.300) ──────────────────────────────────────────────────
    EndpointTest("GET", "/api/compliance/status", "Compliance",
                 expected_keys=["status"]),
    EndpointTest("GET", "/api/compliance/reports", "Compliance",
                 expected_keys=["reports"]),

    # ── Tenants (v0.300) ─────────────────────────────────────────────────────
    EndpointTest("GET", "/api/tenants", "Tenants",
                 expected_keys=["tenants"]),

    # ── Platform (v0.400) ────────────────────────────────────────────────────
    # Note: control_plane (platform_bp) and self_service (self_service_bp)
    # may not be registered in dashboard.py — these could return 404.
    EndpointTest("GET", "/api/platform/fleets", "Platform",
                 expected_keys=["fleets"]),
    EndpointTest("GET", "/api/platform/health", "Platform",
                 expected_keys=["status"]),
    EndpointTest("GET", "/api/platform/metrics", "Platform",
                 expected_keys=["metrics"]),
    EndpointTest("GET", "/api/plans", "Platform",
                 expected_keys=["plans"]),
    EndpointTest("GET", "/api/marketplace/packages", "Platform",
                 expected_keys=["packages"]),
    EndpointTest("GET", "/api/regions", "Platform",
                 expected_keys=["regions"]),

    # ── Core dashboard endpoints (regression baseline) ───────────────────────
    EndpointTest("GET", "/api/status", "Core",
                 expected_keys=["agents"]),
    EndpointTest("GET", "/api/health", "Core",
                 expected_keys=["status"]),
    EndpointTest("GET", "/api/agents/performance", "Core"),
    EndpointTest("GET", "/api/skills", "Core",
                 expected_keys=["skills"]),
    EndpointTest("GET", "/api/activity", "Core"),
    EndpointTest("GET", "/api/thermal", "Core"),
    EndpointTest("GET", "/api/mcp/status", "Core",
                 expected_keys=["servers"]),
    EndpointTest("GET", "/api/settings", "Core"),
    EndpointTest("GET", "/api/queue", "Core"),
    EndpointTest("GET", "/api/tasks/queue", "Core"),
    EndpointTest("GET", "/api/skills/available", "Core",
                 expected_keys=["skills"]),
    EndpointTest("GET", "/api/evolution", "Core"),
    EndpointTest("GET", "/api/fleet/agent-cards", "Core"),
    EndpointTest("GET", "/api/fleet/workers", "Core"),
    EndpointTest("GET", "/api/fleet/health", "Core"),
    EndpointTest("GET", "/api/sla", "Core"),
    EndpointTest("GET", "/api/cache/stats", "Core"),
    EndpointTest("GET", "/api/audit", "Core"),
    EndpointTest("GET", "/api/feedback", "Core"),
    EndpointTest("GET", "/api/feedback/stats", "Core"),
    EndpointTest("GET", "/api/usage", "Core"),
    EndpointTest("GET", "/api/usage/dashboard", "Core"),
    EndpointTest("GET", "/api/logs/recent", "Core"),
    EndpointTest("GET", "/api/logs/sources", "Core"),
    EndpointTest("GET", "/api/recommendations", "Core"),
    EndpointTest("GET", "/api/queue/status", "Core"),
    EndpointTest("GET", "/api/settings/schema", "Core"),
    EndpointTest("GET", "/api/integrity", "Core"),
    EndpointTest("GET", "/api/data_stats", "Core"),
]


# ── Result tracking ─────────────────────────────────────────────────────────

@dataclass
class TestResult:
    """Outcome of a single endpoint test."""
    test: EndpointTest
    passed: bool
    status_code: int = 0
    error: str = ""
    response_keys: List[str] = field(default_factory=list)
    elapsed_ms: float = 0.0


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def _make_request(base_url: str, test: EndpointTest) -> TestResult:
    """Execute a single HTTP request and return the test result."""
    url = f"{base_url.rstrip('/')}{test.path}"
    start = time.time()
    try:
        if test.body is not None:
            data = json.dumps(test.body).encode("utf-8")
            req = urllib.request.Request(
                url, data=data, method=test.method,
                headers={"Content-Type": "application/json"},
            )
        else:
            req = urllib.request.Request(url, method=test.method)

        resp = urllib.request.urlopen(req, timeout=HTTP_TIMEOUT)
        status = resp.getcode()
        body_bytes = resp.read()
        elapsed = (time.time() - start) * 1000

        # Parse JSON
        try:
            payload = json.loads(body_bytes)
        except (json.JSONDecodeError, ValueError) as je:
            return TestResult(
                test=test, passed=False, status_code=status,
                error=f"Invalid JSON: {je}", elapsed_ms=elapsed,
            )

        # Check status code
        if status != test.expected_status:
            return TestResult(
                test=test, passed=False, status_code=status,
                error=f"Expected {test.expected_status}, got {status}",
                response_keys=list(payload.keys()) if isinstance(payload, dict) else [],
                elapsed_ms=elapsed,
            )

        # Check expected keys
        if test.expected_keys and isinstance(payload, dict):
            missing = [k for k in test.expected_keys if k not in payload]
            if missing:
                return TestResult(
                    test=test, passed=False, status_code=status,
                    error=f"Missing keys: {missing}",
                    response_keys=list(payload.keys()),
                    elapsed_ms=elapsed,
                )

        return TestResult(
            test=test, passed=True, status_code=status,
            response_keys=list(payload.keys()) if isinstance(payload, dict) else [],
            elapsed_ms=elapsed,
        )

    except urllib.error.HTTPError as he:
        elapsed = (time.time() - start) * 1000
        # A 4xx/5xx is still a valid response — check if it was expected
        body_str = ""
        try:
            body_str = he.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        # Try parsing as JSON even for error responses
        is_json = False
        resp_keys = []
        try:
            err_payload = json.loads(body_str)
            is_json = True
            if isinstance(err_payload, dict):
                resp_keys = list(err_payload.keys())
        except Exception:
            pass

        if he.code == test.expected_status:
            return TestResult(
                test=test, passed=True, status_code=he.code,
                response_keys=resp_keys, elapsed_ms=elapsed,
            )
        return TestResult(
            test=test, passed=False, status_code=he.code,
            error=f"HTTP {he.code}: {body_str[:200]}",
            response_keys=resp_keys, elapsed_ms=elapsed,
        )

    except urllib.error.URLError as ue:
        elapsed = (time.time() - start) * 1000
        return TestResult(
            test=test, passed=False, status_code=0,
            error=f"Connection error: {ue.reason}", elapsed_ms=elapsed,
        )

    except Exception as exc:
        elapsed = (time.time() - start) * 1000
        return TestResult(
            test=test, passed=False, status_code=0,
            error=f"Unexpected error: {exc}", elapsed_ms=elapsed,
        )


def _check_dashboard_reachable(base_url: str) -> bool:
    """Quick connectivity check — hit /api/health."""
    try:
        resp = urllib.request.urlopen(
            f"{base_url.rstrip('/')}/api/health", timeout=5,
        )
        return resp.getcode() == 200
    except Exception:
        return False


# ── Dry-run printer ──────────────────────────────────────────────────────────

def _print_dry_run():
    """Print all endpoint definitions without making requests."""
    groups = {}
    for t in ENDPOINT_TESTS:
        groups.setdefault(t.group, []).append(t)

    total = len(ENDPOINT_TESTS)
    print(f"\n=== Endpoint Test Catalog ({total} endpoints) ===\n")
    for group_name, tests in groups.items():
        print(f"  [{group_name}] ({len(tests)} endpoints)")
        for t in tests:
            body_hint = f"  body={json.dumps(t.body)}" if t.body else ""
            keys_hint = f"  expect_keys={t.expected_keys}" if t.expected_keys else ""
            print(f"    {t.method:6s} {t.path}{body_hint}{keys_hint}")
        print()
    print(f"Total: {total} endpoints across {len(groups)} groups")


# ── Result printer ───────────────────────────────────────────────────────────

def _print_results(results: List[TestResult]):
    """Print a coloured pass/fail summary."""
    groups = {}
    for r in results:
        groups.setdefault(r.test.group, []).append(r)

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    total = len(results)

    print(f"\n{'=' * 70}")
    print(f"  ENDPOINT TEST RESULTS  —  {passed}/{total} passed, {failed} failed")
    print(f"{'=' * 70}\n")

    for group_name, group_results in groups.items():
        group_pass = sum(1 for r in group_results if r.passed)
        group_total = len(group_results)
        status_icon = "PASS" if group_pass == group_total else "FAIL"
        print(f"  [{status_icon}] {group_name} ({group_pass}/{group_total})")
        for r in group_results:
            icon = "PASS" if r.passed else "FAIL"
            timing = f"{r.elapsed_ms:.0f}ms" if r.elapsed_ms > 0 else "---"
            line = f"    [{icon}] {r.test.method:6s} {r.test.path}"
            line += f"  ({r.status_code}, {timing})"
            if not r.passed and r.error:
                line += f"\n           Error: {r.error}"
            print(line)
        print()

    # Summary
    print(f"{'─' * 70}")
    total_ms = sum(r.elapsed_ms for r in results)
    print(f"  Total: {passed} passed, {failed} failed, {total} total "
          f"({total_ms:.0f}ms)")
    if failed > 0:
        print(f"\n  FAILED endpoints:")
        for r in results:
            if not r.passed:
                print(f"    - {r.test.method} {r.test.path}: {r.error}")
    print(f"{'─' * 70}\n")


# ── Main runner ──────────────────────────────────────────────────────────────

def run_tests(base_url: str = DEFAULT_BASE_URL,
              dry_run: bool = False) -> int:
    """Run all endpoint tests. Returns exit code (0 = all pass, 1 = failures)."""
    if dry_run:
        _print_dry_run()
        return 0

    # Connectivity check
    print(f"\nTesting against: {base_url}")
    print(f"Endpoints to test: {len(ENDPOINT_TESTS)}")
    print(f"Checking dashboard connectivity... ", end="", flush=True)

    if not _check_dashboard_reachable(base_url):
        print("UNREACHABLE")
        print("\nDashboard not running -- skipping live tests.")
        print("Start the dashboard with: python fleet/dashboard.py")
        print("Or use --dry-run to list endpoints without hitting them.\n")
        return 2  # distinct exit code for "not running"

    print("OK\n")

    # Run all tests
    results: List[TestResult] = []
    for i, test in enumerate(ENDPOINT_TESTS, 1):
        label = f"[{i}/{len(ENDPOINT_TESTS)}] {test.method} {test.path}"
        print(f"  {label}... ", end="", flush=True)
        result = _make_request(base_url, test)
        results.append(result)
        icon = "PASS" if result.passed else "FAIL"
        timing = f"{result.elapsed_ms:.0f}ms"
        print(f"{icon} ({result.status_code}, {timing})")

    _print_results(results)

    # Return 0 if all passed, 1 if any failed
    return 0 if all(r.passed for r in results) else 1


def main():
    parser = argparse.ArgumentParser(
        description="BigEd Fleet Dashboard endpoint test suite",
    )
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL,
        help=f"Dashboard base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List all endpoints without making requests",
    )
    args = parser.parse_args()

    exit_code = run_tests(base_url=args.base_url, dry_run=args.dry_run)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
