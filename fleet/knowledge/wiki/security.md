# Security Posture

> Last updated: 2026-04-05 | Source: system initialization

## Status
All P0/P1/P2/P3 security issues resolved as of 0.050.05b.

## Hardening Summary
- **XSS:** All innerHTML injection points use escapeHTML() (40+ sites)
- **SQL injection:** ALLOWED_FLEET_TABLES + ALLOWED_TOOLS_TABLES frozensets; parameterized queries
- **SSRF:** validate_peer_url() on all federation peer URLs
- **Auth:** Bearer token auth on dashboard; HMAC compare_digest for all tokens
- **DLP:** Secret scrubbing (AWS, Azure, GCP, database URI, private key patterns)
- **Rate limiting:** Adaptive — triggers under DDoS, not normal use
- **File access:** filesystem_guard.py with SOC 2 zone-based permissions
- **RBAC:** 5 roles × 7 actions granular permission model

## Active Measures
- Content-Security-Policy header on all dashboard responses
- CORS restricted to configured origins
- TLS support (self-signed + optional Let's Encrypt)
- Docker sandbox for code_write/pen_test execution
- Circuit breaker with exponential backoff on provider failures

## Related
- [Overview](overview.md) | [Architecture](architecture.md)
- Raw reports: [security/](../security/)
- [Index](../index.md)
