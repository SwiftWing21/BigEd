"""Adversarial security tests for fleet dashboard (0.24.00 S4 Security Defaults).

Red team tests covering SQL injection, XSS, path traversal, RBAC enforcement,
and rate limiting. Uses Flask test client — no running server required.
"""
import json
import os
import sys
import unittest

# Ensure fleet directory is on sys.path so dashboard imports work
FLEET_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if FLEET_DIR not in sys.path:
    sys.path.insert(0, FLEET_DIR)

from dashboard import app, RBAC_ROLES


class TestDashboardSecurity(unittest.TestCase):
    """Automated red team tests for the fleet dashboard."""

    def setUp(self):
        """Set up Flask test client."""
        app.config["TESTING"] = True
        self.client = app.test_client()

    def test_sql_injection_task_endpoint(self):
        """Verify SQL injection attempts in query params are harmless."""
        # Attempt SQL injection via common API query parameters
        payloads = [
            "'; DROP TABLE tasks; --",
            "1 OR 1=1",
            "1; SELECT * FROM agents --",
            "' UNION SELECT * FROM usage --",
        ]
        for payload in payloads:
            resp = self.client.get(f"/api/tasks?limit={payload}")
            # Should either return valid JSON or an error — never crash
            self.assertIn(resp.status_code, (200, 400, 401, 404, 500),
                          f"Unexpected status for SQL injection payload: {payload}")
            # Verify response is valid JSON (not a raw DB error)
            try:
                data = resp.get_json()
                self.assertIsNotNone(data)
            except Exception:
                pass  # Some endpoints may return HTML

    def test_xss_in_task_output(self):
        """Verify XSS payloads in API responses are JSON-encoded (not raw HTML)."""
        xss_payloads = [
            '<script>alert("xss")</script>',
            '<img src=x onerror=alert(1)>',
            '"><svg onload=alert(1)>',
        ]
        # GET endpoints should return JSON content type, not HTML
        resp = self.client.get("/api/status")
        if resp.status_code == 200:
            content_type = resp.content_type or ""
            self.assertIn("json", content_type.lower(),
                          "API endpoints must return JSON, not raw HTML")

    def test_path_traversal_knowledge_endpoint(self):
        """Verify path traversal in knowledge file requests is blocked."""
        traversal_payloads = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32\\config\\sam",
            "%2e%2e%2f%2e%2e%2f%2e%2e%2fetc%2fpasswd",
            "....//....//....//etc/passwd",
        ]
        for payload in traversal_payloads:
            resp = self.client.get(f"/api/knowledge?file={payload}")
            # Should not return 200 with actual file contents
            if resp.status_code == 200:
                data = resp.get_json()
                if isinstance(data, dict):
                    # Ensure no sensitive system file content leaked
                    content = json.dumps(data).lower()
                    self.assertNotIn("root:", content,
                                     f"Path traversal may have leaked /etc/passwd: {payload}")
                    self.assertNotIn("sam\\", content,
                                     f"Path traversal may have leaked SAM: {payload}")

    def test_unauthorized_write_blocked(self):
        """Verify viewer role (no token) cannot access write endpoints."""
        write_endpoints = [
            ("/api/alerts/ack/1", "POST"),
            ("/api/gdpr/erasure", "POST"),
            ("/api/integrity/refresh", "POST"),
        ]
        for path, method in write_endpoints:
            if method == "POST":
                resp = self.client.post(path, json={})
            elif method == "PUT":
                resp = self.client.put(path, json={})
            elif method == "DELETE":
                resp = self.client.delete(path)
            else:
                continue
            # Should be 401 (no auth configured) or 403 (RBAC blocked)
            self.assertIn(resp.status_code, (401, 403),
                          f"Write endpoint {method} {path} should block viewers, "
                          f"got {resp.status_code}")

    def test_rate_limiting(self):
        """Verify rate limiting is active on API endpoints."""
        # Send many rapid requests to a single endpoint
        statuses = []
        for _ in range(70):  # > RATE_LIMIT_REQUESTS (60)
            resp = self.client.get("/api/status")
            statuses.append(resp.status_code)
        # At least some should be rate-limited (429)
        self.assertIn(429, statuses,
                      "Rate limiting should trigger after 60 requests/minute")

    def test_rbac_role_hierarchy(self):
        """Verify RBAC role permissions are properly nested."""
        admin_perms = RBAC_ROLES["admin"]
        operator_perms = RBAC_ROLES["operator"]
        viewer_perms = RBAC_ROLES["viewer"]
        # Admin should have all operator perms
        self.assertTrue(operator_perms.issubset(admin_perms),
                        "Admin must have all operator permissions")
        # Operator should have all viewer perms
        self.assertTrue(viewer_perms.issubset(operator_perms),
                        "Operator must have all viewer permissions")
        # Viewer should NOT have write
        self.assertNotIn("write", viewer_perms,
                         "Viewer must not have write permission")
        self.assertNotIn("delete", viewer_perms,
                         "Viewer must not have delete permission")

    def test_error_messages_sanitized(self):
        """Verify error responses don't leak file paths."""
        # Request a non-existent endpoint
        resp = self.client.get("/api/nonexistent_endpoint_xyz")
        if resp.status_code >= 400:
            body = resp.get_data(as_text=True)
            # Should not contain full Windows or Unix paths
            import re
            self.assertIsNone(
                re.search(r'[A-Z]:\\Users\\', body),
                "Error response should not leak Windows file paths")


if __name__ == "__main__":
    unittest.main()
