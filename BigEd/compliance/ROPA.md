# Record of Processing Activities (ROPA) — BigEd CC

> GDPR Article 30 | Last updated: 2026-03-19

## Controller Information
- **Organization:** [Your Organization]
- **Contact:** [DPO/Contact]
- **System:** BigEd CC Fleet v1.0+

## Processing Activities

| # | Activity | Purpose | Data Subjects | Data Categories | Legal Basis | Retention | Recipients |
|---|----------|---------|---------------|-----------------|-------------|-----------|------------|
| 1 | Task dispatch & execution | AI fleet operations | Operators, end users | Task payloads (text, JSON) | Legitimate interest | Until task deletion or GDPR erasure | Local processing (Ollama), Claude API, Gemini API |
| 2 | CRM lead management | Business development | Prospects, leads | Name, email, phone, company | Consent / Legitimate interest | Until manual delete or GDPR erasure | Local storage (tools.db) |
| 3 | Cost tracking | Budget management | Operators | API usage tokens, costs | Legitimate interest | 12 months (audit retention) | Local storage (fleet.db) |
| 4 | Security monitoring | DLP & compliance | All system users | Task results, knowledge files | Legitimate interest | 12 months (audit log) | Local storage |
| 5 | ML training | Model improvement | N/A (synthetic data) | Training metrics, checkpoints | N/A | Indefinite | Local storage |

## Data Transfers
- **Claude API (Anthropic):** Task payloads sent to US-based API. Requires DPA.
- **Gemini API (Google):** Task payloads sent to Google infrastructure. Requires DPA.
- **Local Ollama:** No data transfer — all processing on local machine.

## Technical Measures
- DLP scanning (input + output)
- Encryption at rest available (SQLCipher)
- Dashboard bearer token auth
- Air-gap mode for classified environments
- Audit logging with HMAC integrity

## Data Subject Rights
- Right to erasure: `lead_client.py gdpr-erase <identifier> --confirm`
- Right to portability: `export_data()` in each module
- Right to restrict: Agent quarantine/pause
