"""Unit tests for fleet/billing.py — usage-based billing and quota enforcement."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fleet"))

import pytest

import billing


# ── Fixture: reset table-ensured flag so each test creates fresh tables ────────

@pytest.fixture(autouse=True)
def _reset_billing_state():
    billing._tables_ensured = False
    yield
    billing._tables_ensured = False


# ── 1. ensure_billing_tables — idempotent ─────────────────────────────────────

def test_ensure_billing_tables_idempotent(tmp_db):
    billing.ensure_billing_tables()
    billing.ensure_billing_tables()  # should not raise
    import db
    conn = db.get_conn()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "tenant_usage" in tables
    assert "tenant_quotas" in tables


# ── 2. record_usage — inserts a row ──────────────────────────────────────────

def test_record_usage(tmp_db):
    billing.record_usage("tenant_a", "summarize", 100, 50, "claude-haiku-4-5", 0.001)
    import db
    conn = db.get_conn()
    row = conn.execute(
        "SELECT * FROM tenant_usage WHERE tenant_id='tenant_a'"
    ).fetchone()
    assert row is not None
    assert row["skill"] == "summarize"
    assert row["tokens_in"] == 100
    assert row["tokens_out"] == 50
    assert row["model"] == "claude-haiku-4-5"


# ── 3. get_tenant_usage — returns totals for recorded usage ───────────────────

def test_get_tenant_usage(tmp_db):
    billing.record_usage("tenant_b", "web_search", 200, 80, "local", 0.0)
    billing.record_usage("tenant_b", "web_search", 100, 40, "local", 0.0)

    result = billing.get_tenant_usage("tenant_b", period="month")
    assert result["tenant_id"] == "tenant_b"
    assert result["total_tokens_in"] == 300
    assert result["total_tokens_out"] == 120
    assert len(result["by_skill"]) >= 1


# ── 4. get_tenant_usage — empty result for unknown tenant ────────────────────

def test_get_tenant_usage_unknown_tenant(tmp_db):
    result = billing.get_tenant_usage("no_such_tenant", period="month")
    assert result["total_tokens_in"] == 0
    assert result["total_tokens_out"] == 0
    assert result["total_cost_usd"] == pytest.approx(0.0)


# ── 5. get_all_tenant_usage — returns list ────────────────────────────────────

def test_get_all_tenant_usage(tmp_db):
    billing.record_usage("tenant_c", "ingest", 50, 20, "local", 0.0)
    result = billing.get_all_tenant_usage(period="month")
    assert isinstance(result, list)
    tenants = {r["tenant_id"] for r in result}
    assert "tenant_c" in tenants


# ── 6. check_quota — returns structure with expected keys ────────────────────

def test_check_quota_structure(tmp_db):
    result = billing.check_quota("new_tenant")
    assert "tenant_id" in result
    assert "quota" in result
    assert "used" in result
    assert "remaining" in result
    assert "exceeded" in result
    assert result["exceeded"] is False  # fresh tenant, no usage


# ── 7. check_quota — fail-closed on error ────────────────────────────────────

def test_check_quota_fail_closed(tmp_db):
    """If quota check fails, it fails closed (exceeded=True)."""
    from unittest import mock
    with mock.patch("billing._get_conn", side_effect=Exception("db down")):
        result = billing.check_quota("error_tenant")
    assert result["exceeded"] is True


# ── 8. enforce_quota — True when within limits ───────────────────────────────

def test_enforce_quota_within_limits(tmp_db):
    assert billing.enforce_quota("fresh_tenant", "tokens") is True
    assert billing.enforce_quota("fresh_tenant", "tasks") is True


# ── 9. set_quota + get_quota round-trip ──────────────────────────────────────

def test_set_and_get_quota(tmp_db):
    billing.set_quota("quota_tenant", {"max_tasks_day": 50, "max_tokens_day": 5000})
    result = billing.get_quota("quota_tenant")
    assert result["max_tasks_day"] == 50
    assert result["max_tokens_day"] == 5000
    assert result["max_agents"] == 10  # default


# ── 10. set_quota — ignores unknown keys ─────────────────────────────────────

def test_set_quota_ignores_unknown_keys(tmp_db):
    """Unknown keys in limits dict are silently filtered out."""
    billing.set_quota("quota_tenant2", {"max_tasks_day": 10, "evil_field": "hack"})
    result = billing.get_quota("quota_tenant2")
    assert result["max_tasks_day"] == 10
    assert "evil_field" not in result


# ── 11. get_quota_usage — percentage calculations ────────────────────────────

def test_get_quota_usage_structure(tmp_db):
    result = billing.get_quota_usage("usage_tenant")
    assert "tasks" in result
    assert "tokens" in result
    assert "pct" in result["tasks"]
    assert "pct" in result["tokens"]
    assert result["tasks"]["pct"] >= 0.0


# ── 12. calculate_invoice — returns expected structure ────────────────────────

def test_calculate_invoice(tmp_db):
    billing.record_usage("inv_tenant", "summarize", 1000, 500, "claude-haiku-4-5", 0.002)
    invoice = billing.calculate_invoice("inv_tenant", period="month")
    assert invoice["tenant_id"] == "inv_tenant"
    assert "subtotal" in invoice
    assert "total" in invoice
    assert "line_items" in invoice
    assert invoice["total"] >= invoice["subtotal"]


# ── 13. export_invoice_csv — returns CSV string ───────────────────────────────

def test_export_invoice_csv(tmp_db):
    billing.record_usage("csv_tenant", "ingest", 500, 200, "local", 0.0)
    csv_str = billing.export_invoice_csv("csv_tenant", period="month")
    assert isinstance(csv_str, str)
    assert "Skill" in csv_str
    assert "Model" in csv_str
    assert "Total" in csv_str


# ── 14. get_pricing — returns expected keys ───────────────────────────────────

def test_get_pricing():
    pricing = billing.get_pricing()
    assert "tokens_per_dollar" in pricing
    assert "base_monthly" in pricing
    assert "overage_rate" in pricing
    assert "currency" in pricing
    assert isinstance(pricing["tokens_per_dollar"], (int, float))
