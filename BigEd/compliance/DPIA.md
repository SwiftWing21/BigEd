# Data Protection Impact Assessment (DPIA) — BigEd CC

> GDPR Article 35 | EU AI Act Risk Assessment

## 1. System Description
BigEd CC is a local AI agent fleet managing 66 skills across 11 worker roles. Processing occurs primarily on the local machine (Ollama) with optional external API calls (Claude, Gemini).

## 2. Necessity & Proportionality
- **Purpose:** Automate research, code review, security auditing, business operations
- **Data minimization:** Skills receive only task-specific payloads; no bulk data collection
- **Retention:** Configurable; GDPR erasure API available; audit logs rotate at 12 months

## 3. Risk Assessment

| Risk | Likelihood | Impact | Mitigation | Residual Risk |
|------|-----------|--------|------------|---------------|
| PII in task payloads | Medium | High | Input PII scan + DLP output scrub | Low |
| API key leakage | Medium | Critical | 14 regex patterns + base64 + env-match | Low |
| Prompt injection | Medium | High | 8 injection patterns + blocking on detect | Medium |
| Unauthorized access | Low | High | Dashboard bearer auth + air-gap mode | Low |
| Data breach via API | Low | High | Offline mode; local Ollama default | Low |
| Knowledge file tampering | Low | Medium | SHA-256 integrity manifest + watchdog | Low |
| Model hallucination in legal/security | Medium | High | Adversarial review (3 providers) | Medium |

## 4. Measures to Address Risks
- 5-layer defense-in-depth (input → execution → output → monitoring → audit)
- OWASP LLM Top 10 coverage (grade B+)
- Right to erasure implemented (GDPR Art. 17)
- Incident response SOP documented (SOC 2 CC8)
- Data classification on tasks (public/internal/confidential/restricted)

## 5. Consultation
- [DPO sign-off required]
- [Date of assessment]
- [Next review date: 6 months]
