# BigEd CC — Doctor in the Loop (DITL) Compliance Spec

**Status:** Planned (0.060.00b)
**Confirmation hex required for unmapped features within DITL**
**This spec covers the compliance framework — not the unmapped clinical features**

---

## Architecture Decision: Baked-in + Compliance Mode

**Normal agent UX (ALL users):** Multi-turn response flow (approve/reject/more info/feedback/discuss) with chat mode. This is standard HITL UX — no compliance enforcement.

**DITL Compliance Mode (opt-in, forced for healthcare):** Same UX but with HIPAA safeguards enforced. Toggle in fleet.toml. Enterprise installs can force it on.

**"Disable at own risk":** Available but requires explicit acknowledgment dialog + audit log entry. Shows persistent warning banner.

---

## HIPAA Requirements Summary (Research Findings)

### What MUST be implemented for DITL:

| Requirement | Standard | Implementation |
|-------------|----------|----------------|
| **Encryption at rest** | AES-256 (45 CFR 164.312) | SQLCipher on fleet.db (already exists via db_encrypt skill) |
| **Encryption in transit** | TLS 1.2+ (45 CFR 164.312) | All API calls already use HTTPS |
| **Audit logging** | 45 CFR 164.312(b) | New PHI audit table — who/when/what/action, 6-year retention |
| **Minimum necessary** | 45 CFR 164.502(b) | Prompt engineering to limit PHI in LLM context |
| **Access controls** | 45 CFR 164.312(a) | Role-based DITL access via FileSystemGuard zones |
| **BAA** | 45 CFR 164.502(e) | Required for cloud LLM PHI processing |
| **AI disclaimer** | State laws (TX, CA) | Every DITL response marked as AI-generated |
| **Human review** | Clinical standard | Log clinician accept/reject for every recommendation |
| **Data retention** | State law (typically 7 years) | Configurable retention engine with secure deletion |
| **De-identification** | Safe Harbor (18 identifiers) | Auto-strip before external API calls |

### Cloud LLM BAA Status:

| Provider | BAA Available | Plan Required | PHI Processing |
|----------|--------------|---------------|----------------|
| **Anthropic (Claude)** | Yes | Enterprise (sales-assisted) | Via API or AWS Bedrock/GCP/Azure |
| **Google (Gemini)** | Yes | Vertex AI + HIPAA flag | Covers Gemini 3, Workspace |
| **Ollama (Local)** | N/A | None | No BAA needed — PHI stays local |
| **MiniMax** | Unknown | Research needed | Not recommended for PHI |

### Simplest Compliant Path:
**Route ALL PHI through local Ollama.** No BAA needed, PHI never leaves the machine. Cloud APIs only for non-PHI tasks (literature search, general knowledge).

---

## fleet.toml Configuration

```toml
[ditl]
enabled = false                      # Enable DITL compliance mode
compliance_level = "hipaa"           # hipaa | soc2 | none
force_local_phi = true               # PHI never sent to cloud APIs (safest default)
data_retention_days = 2555           # ~7 years (HIPAA + most state laws)
auto_purge = true                    # Secure deletion beyond retention period
require_baa = true                   # Block external APIs for PHI without BAA
disable_at_own_risk = false          # Override compliance (shows warning + audit entry)
audit_all_phi_access = true          # Log every PHI read/write/delete
ai_disclaimer = true                 # Show "AI-generated, not clinical advice" on responses
phi_encryption = "sqlcipher"         # sqlcipher | aes256-file | none (none = violation)

# BAA status per provider (operator configures after signing)
[ditl.baa]
anthropic = false                    # Set true after Enterprise BAA signed
google = false                       # Set true after Vertex AI BAA signed
minimax = false                      # Set true if BAA obtained
local = true                         # Local always "compliant" (no third party)

# De-identification (Safe Harbor method)
[ditl.deidentification]
auto_strip_before_api = true         # Remove 18 HIPAA identifiers before cloud API calls
method = "safe_harbor"               # safe_harbor | expert_determination
strip_names = true
strip_dates = true                   # Except year for ages < 89
strip_locations = true               # Below state level
strip_phone_fax_email = true
strip_ssn_mrn = true
strip_device_ids = true
strip_urls_ips = true
strip_biometrics = true
strip_photos = true
```

---

## Data Flow

```
Doctor types/speaks input
        │
        ▼
[DITL Input Handler]
        │
        ├── PHI detected? ──Yes──► [De-identify for logging]
        │                          │
        │                          ▼
        │                   [PHI Audit Log] ← AES-256 encrypted
        │                          │
        │                          ▼
        │                   force_local_phi? ──Yes──► [Local Ollama ONLY]
        │                          │
        │                          No + BAA signed
        │                          │
        │                          ▼
        │                   [Cloud API with PHI] (Enterprise BAA)
        │
        ├── No PHI ──► [Normal routing] (Claude → Gemini → Local)
        │
        ▼
[Model Response]
        │
        ├── [AI Disclaimer injected]
        │
        ├── [5-Agent Review Cycle] (if configured)
        │     ├── Medical accuracy
        │     ├── Completeness
        │     ├── Compliance check
        │     ├── Billing alignment
        │     └── Adversarial review
        │
        ▼
[Doctor Review]
        │
        ├── Approve ──► Signed, timestamped, immutable
        ├── Reject + Reason ──► Logged for learning
        ├── More Info ──► Agent researches (local only for PHI)
        ├── Feedback ──► Re-process with doctor's input
        └── Discuss ──► Multi-agent debate
        │
        ▼
[Audit Trail] ← Every decision logged with:
        - Who (authenticated user ID)
        - When (timestamp)
        - What (action taken)
        - Why (approval/rejection reason)
        - Model used
        - PHI scope (what data was accessed)
```

---

## Implementation Phases

### Phase 1: Compliance Framework (0.060.00b)
- [ ] fleet.toml [ditl] configuration section
- [ ] DITL mode toggle in Settings with compliance level selector
- [ ] PHI audit table in fleet.db (encrypted, 6-year retention)
- [ ] AI disclaimer injection on all DITL responses
- [ ] Human review logging (accept/reject/reason for every recommendation)
- [ ] force_local_phi routing (bypass Claude/Gemini for PHI workloads)
- [ ] "Disable at own risk" dialog with explicit acknowledgment + audit entry
- [ ] Persistent compliance warning banner when DITL disabled

### Phase 2: Data Handling (0.060.01b)
- [ ] Safe Harbor de-identification engine (strip 18 HIPAA identifiers)
- [ ] Auto-strip before external API calls
- [ ] Retention policy engine (configurable auto-purge with secure deletion)
- [ ] Destruction audit trail (method, date, responsible party)
- [ ] PHI-scoped FileSystemGuard zones (DITL data isolated from fleet knowledge)
- [ ] SQLCipher encryption toggle for DITL databases

### Phase 3: Enhanced Review (0.060.02b) [REQUIRES HEX]
- [ ] 5-agent review cycle for clinical recommendations
- [ ] Voice/STT pipeline (local Whisper, HIPAA-compliant)
- [ ] State disclosure compliance (configurable per jurisdiction)
- [ ] BAA management UI (track signed agreements per provider)

---

## Security Boundaries

| Data Type | Can Send to Cloud? | Storage | Retention |
|-----------|-------------------|---------|-----------|
| PHI (patient data) | Only with signed BAA | AES-256 encrypted | 7 years (configurable) |
| De-identified data | Yes (no longer PHI) | Standard | Fleet default |
| AI recommendations | Yes (not PHI itself) | Standard | Fleet default |
| Audit logs | Never | AES-256 encrypted | 6 years minimum |
| Voice/audio | Only with BAA STT | AES-256, auto-delete | Session only |

---

## Penalties for Non-Compliance

| Tier | Penalty | Threshold |
|------|---------|-----------|
| Tier 1 | $141-$71,162 per violation | Unknowing |
| Tier 2 | $1,424-$71,162 | Reasonable cause |
| Tier 3 | $14,232-$71,162 | Willful neglect (corrected) |
| Tier 4 | $71,162-$2,134,831 | Willful neglect (not corrected) |
| Criminal | Up to $250,000 + 10 years | Knowing disclosure |

Annual cap: $2,134,831 per identical provision.

---

## Sources

Research conducted 2026-03-20. Key sources:
- HHS HIPAA Security Rule (45 CFR 164.312)
- Anthropic BAA documentation (privacy.claude.com)
- Google Cloud HIPAA Compliance (cloud.google.com)
- HIPAA Journal retention requirements
- PMC healthcare AI compliance studies
