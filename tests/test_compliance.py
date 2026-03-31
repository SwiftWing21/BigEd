"""Unit tests for fleet/compliance.py — SOC 2, HIPAA, SLA, audit reports."""
import os
import sys
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "fleet"))

import pytest

import compliance


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_table_flag():
    """Reset table-ready flag and mock expensive external calls per test."""
    compliance._table_ready = False
    yield
    compliance._table_ready = False


@pytest.fixture()
def _mock_external():
    """Mock audit and audit_log imports to avoid dependency on live DB audit tables."""
    with mock.patch("compliance.collect_access_logs", return_value=[]), \
         mock.patch("compliance.collect_change_logs", return_value=[]), \
         mock.patch("compliance.collect_incident_logs", return_value=[]), \
         mock.patch("compliance.collect_encryption_status", return_value={
             "db_encryption": False,
             "tls_enabled": False,
             "fleet_tls_enabled": False,
             "tenants": {},
         }), \
         mock.patch("compliance._save_report", return_value=None):
        yield


# ── 1. init_compliance_table — creates table ──────────────────────────────────

def test_init_compliance_table(tmp_db):
    compliance.init_compliance_table()
    import db
    conn = db.get_conn()
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    assert "compliance_reports" in tables


def test_init_compliance_table_idempotent(tmp_db):
    compliance.init_compliance_table()
    compliance.init_compliance_table()  # must not raise


# ── 2. _parse_period — parses day formats ────────────────────────────────────

def test_parse_period_days():
    from_ts, to_ts = compliance._parse_period("7d")
    assert from_ts < to_ts
    assert "2026" in from_ts or "2025" in from_ts


def test_parse_period_30d():
    from_ts, to_ts = compliance._parse_period("30d")
    assert from_ts < to_ts


def test_parse_period_monthly():
    from_ts, to_ts = compliance._parse_period("monthly")
    assert from_ts < to_ts


def test_parse_period_yyyymm():
    from_ts, to_ts = compliance._parse_period("2026-01")
    assert "2026-01" in from_ts
    assert from_ts < to_ts


def test_parse_period_dict():
    from_ts, to_ts = compliance._parse_period({"from": "2026-01-01", "to": "2026-01-31"})
    assert from_ts == "2026-01-01"
    assert to_ts == "2026-01-31"


def test_parse_period_unknown_returns_default():
    from_ts, to_ts = compliance._parse_period("unknown_period")
    # Falls back to 30 days — should return two non-empty strings
    assert from_ts
    assert to_ts


# ── 3. _redact_secrets — strips tokens and keys ───────────────────────────────

def test_redact_secrets_strips_api_key():
    text = 'api_key="sk-abcdefghijklmnopqrstuvwxyz1234567890"'
    result = compliance._redact_secrets(text)
    assert "sk-abcdef" not in result
    assert "[REDACTED]" in result


def test_redact_secrets_strips_long_hex():
    text = "token: abcdef1234567890abcdef1234567890abcdef12"
    result = compliance._redact_secrets(text)
    assert "abcdef1234567890" not in result


def test_redact_secrets_passthrough_normal():
    text = "normal status message: all good"
    result = compliance._redact_secrets(text)
    assert result == text


# ── 4. generate_soc2_report — structure ───────────────────────────────────────

def test_generate_soc2_report_structure(tmp_db, _mock_external):
    report = compliance.generate_soc2_report(period="7d")
    assert report["type"] == "soc2"
    assert "id" in report
    assert "generated_at" in report
    assert "period" in report
    sections = report["sections"]
    assert "access_controls" in sections
    assert "change_management" in sections
    assert "system_monitoring" in sections
    assert "incident_response" in sections
    assert "data_encryption" in sections


def test_generate_soc2_report_rbac_summary(tmp_db, _mock_external):
    report = compliance.generate_soc2_report(period="7d")
    rbac = report["sections"]["access_controls"]["rbac_roles"]
    assert "admin" in rbac
    assert "viewer" in rbac


# ── 5. generate_hipaa_report — structure ─────────────────────────────────────

def test_generate_hipaa_report_structure(tmp_db, _mock_external):
    report = compliance.generate_hipaa_report(period="7d")
    assert report["type"] == "hipaa"
    sections = report["sections"]
    assert "phi_access_audit" in sections
    assert "encryption_status" in sections
    assert "breach_log" in sections
    assert "minimum_necessary" in sections


# ── 6. generate_audit_summary — structure ────────────────────────────────────

def test_generate_audit_summary_structure(tmp_db, _mock_external):
    with mock.patch("compliance._save_report", return_value=None):
        report = compliance.generate_audit_summary(period="7d")
    assert report["type"] == "audit_summary"
    assert "sections" in report
    sections = report["sections"]
    assert "top_actors" in sections or "user_activity" in sections or "actions_summary" in sections


# ── 7. collect_access_logs — returns list (mocked audit) ─────────────────────

def test_collect_access_logs_returns_list():
    with mock.patch("audit.query_audit", return_value=[]):
        result = compliance.collect_access_logs("7d")
    assert isinstance(result, list)


def test_collect_access_logs_fallback_on_error():
    """Returns empty list if audit module raises."""
    with mock.patch("audit.query_audit", side_effect=Exception("db error")):
        result = compliance.collect_access_logs("7d")
    assert isinstance(result, list)
    assert result == []


# ── 8. collect_encryption_status — returns dict ──────────────────────────────

def test_collect_encryption_status_structure():
    result = compliance.collect_encryption_status()
    assert "db_encryption" in result
    assert "tls_enabled" in result
    assert "fleet_tls_enabled" in result
    assert isinstance(result["db_encryption"], bool)


# ── 9. get_compliance_status — returns dict ───────────────────────────────────

def test_get_compliance_status(tmp_db, _mock_external):
    with mock.patch("compliance._save_report", return_value=None):
        result = compliance.get_compliance_status()
    assert isinstance(result, dict)
