# Security & Compliance Blueprint — BigEd CC

> **Generated:** 2026-03-19 | **System:** BigEd CC v1.0 | **Purpose:** Framework-level security architecture, industry comparison, and compliance mapping
> **Audience:** Architecture decisions, audit preparation, roadmap planning

---

## 1. Current Security Architecture

### 1.1 Defense-in-Depth Layers

```
User Input (Payload)
    |
[Layer 1: Input Validation]
    |-- scan_input() — PII detection (email, SSN, CC, phone)
    |-- _contains_secret() — 7 API key patterns + base64 decode
    |-- safe_path() — path traversal prevention (FLEET_DIR whitelist)
    |-- sanitize_filename() — strips path separators, .., control chars
    |-- JSON schema validation — post_task() validates payloads
    |
[Layer 2: Execution Controls]
    |-- Air-gap whitelist — 14 approved skills (deny-by-default)
    |-- Offline mode — REQUIRES_NETWORK flag blocks external calls
    |-- Affinity routing — role-based skill dispatch
    |-- Sandbox policy — Docker detection (execution deferred)
    |-- Skill timeout — thread-based, DEFAULT_SKILL_TIMEOUT=600s
    |
[Layer 3: Output Guardrails]
    |-- guardrails.py — toxicity, PII redaction, refusal detection, topic rails
    |-- _review.py — adversarial review for 10 HIGH_STAKES_SKILLS
    |-- _redact_secrets() — regex + env-match + base64 decode replacement
    |
[Layer 4: Post-Execution Monitoring]
    |-- scrub_recent_results() — last 50 DONE tasks scanned for secrets
    |-- scrub_knowledge_files() — recursive *.md/*.json/*.jsonl/*.txt scan
    |-- check_failure_streaks() — 3+ consecutive failures = QUARANTINE
    |-- get_stuck_reviews() — >30min REVIEW tasks auto-passed
    |
[Layer 5: Audit & Observability]
    |-- fleet.db — task lifecycle, agent heartbeats, usage tracking
    |-- Per-worker logs — fleet/logs/{role}.log
    |-- Cost tracking — per-skill/model/agent token attribution
    |-- Debug reports — JSON snapshots on crash
    |-- Resolution tracking — data/resolutions.jsonl
```

### 1.2 Implemented Security Controls

| Control | Implementation | File | Status |
|---------|---------------|------|--------|
| PII Detection | Email, SSN, CC (16-digit), US phone patterns | _watchdog.py:110-115 | Active |
| API Key Detection | 7 patterns: sk-*, AIza*, ghp_*, gho_*, xoxb-*, AKIA*, tvly-* | _watchdog.py:27-35 | Active |
| Base64 Secret Detection | Decode + pattern match (catches LLM-encoded keys) | _watchdog.py:55-67 | Active |
| Path Traversal Prevention | safe_path() validates against FLEET_DIR/BigEd/autoresearch roots | _security.py | Active |
| Output Guardrails | Toxicity, PII redaction, refusal detection, topic rails | guardrails.py | Active |
| Adversarial Review | 3-provider review (Claude/Gemini/Local) for high-stakes skills | _review.py | Active (opt-in) |
| Agent Quarantine | 3+ consecutive failures = auto-quarantine | diagnostics.py:40-74 | Active |
| DLP Scrubbing | Task results + knowledge/ files, every 60s/10min | _watchdog.py:224-253 | Active |
| Offline Mode | REQUIRES_NETWORK skills blocked; local Ollama works | worker.py:98-106 | Active |
| Air-Gap Mode | 14 approved skills only; dashboard disabled; secrets not loaded | config.py:9-14 | Active |
| Parameterized SQL | ? placeholders throughout db.py | db.py | Active |
| WAL Mode | Write-ahead logging for concurrent access safety | db.py:23-24 | Active |
| Jittered Backoff | 8 retries with exponential delay + random jitter on DB locks | db.py:114-122 | Active |
| Cost Budgets | Per-skill daily USD limits (warn-only) | fleet.toml [budgets] | Active |
| Human-in-the-Loop | WAITING_HUMAN status; security advisory approve/dismiss | db.py | Active |

---

## 2. Industry Framework Security Comparison

### 2.1 Multi-Agent Security Matrix

| Security Dimension | BigEd CC | CrewAI | AutoGen | LangGraph | OpenAI Agents SDK | Google ADK |
|--------------------|----------|--------|---------|-----------|------------------|-----------|
| **Input PII/Secret Scanning** | Strong (7 patterns + base64 + PII) | None | None | None | Optional guardrails | Optional |
| **Output Guardrails** | Strong (guardrails.py: toxicity, PII redaction, refusal, topics) | None | Optional | None | Optional guardrails | None |
| **DLP (Secret Scrubbing)** | Strong (task results + knowledge/ filesystem) | None | None | None | None | Session-level only |
| **Adversarial Review** | Strong (3-provider, 10 high-stakes skills) | None | None | Custom graph nodes | None | None |
| **Code Execution Sandbox** | Weak (Docker detection only, execution deferred) | None | **Strong** (Docker with resource limits) | None | None | Optional |
| **State Checkpoint/Rollback** | Partial (autoresearch checkpoints) | None | None | **Strong** (every node) | Optional | Partial |
| **Human-in-the-Loop** | Strong (WAITING_HUMAN + REVIEW + security advisory) | Callback hooks | **Strong** (max_consecutive_auto_reply) | Interrupt before/after | Handoff validation | Approval chains |
| **Tool/Skill Restrictions** | Strong (affinity routing + air-gap whitelist) | Agent tools list | Function registry | Node-level | agent.allowed_tools | Schema validation |
| **Failure Detection** | Strong (watchdog: streaks + stuck reviews + DLP) | Error handlers | Max consecutive | Conditional edges | None | Timeout only |
| **Offline/Air-Gap Mode** | **Strong** (deny-by-default, no sockets, no secrets) | None | None | None | None | None |
| **Secret Storage** | Separate ~/.secrets file (not in DB or git) | Env vars | Env vars | Env vars | API key management | Secret manager |
| **Audit Logging** | Good (fleet.db + per-worker logs + cost tracking) | In-process only | Conversation history | Checkpoint log | **Strong** (Trace API) | **Strong** (Cloud Logging) |
| **Network Isolation** | Good (localhost binding + pen_test validation) | None | Docker network isolation | None | None | Service mesh |

### 2.2 Where BigEd CC Leads

1. **Input-Side DLP** — Only framework with built-in secret/PII scanning before LLM dispatch
2. **Output Guardrails** — guardrails.py provides toxicity, PII redaction, refusal detection, topic rails — no other framework ships this
3. **Offline/Air-Gap Mode** — Unique capability for classified/isolated environments
4. **Hardware-Aware Security** — Thermal throttling prevents VRAM OOM (DoS mitigation at hardware level)
5. **Multi-Provider Adversarial Review** — 3-provider review pipeline for high-stakes outputs

### 2.3 Where BigEd CC Trails

1. **Code Execution Sandbox** — AutoGen's Docker sandbox with cgroups is production-grade; BigEd's is detection-only
2. **Distributed Tracing** — OpenAI Agents SDK trace_id correlates requests end-to-end; BigEd lacks trace propagation
3. **State Checkpoint/Rollback** — LangGraph creates checkpoint at every node; BigEd's only checkpoint is autoresearch
4. **Managed Audit Logging** — Google ADK integrates with Cloud Logging (retention, search, export); BigEd uses file-based logs
5. **Session Encryption** — Google ADK encrypts session state at rest; BigEd's fleet.db is plaintext

---

## 3. OWASP LLM Top 10 (2025) Mapping

| # | Risk | BigEd CC Coverage | Grade | Remediation |
|---|------|-------------------|-------|-------------|
| LLM01 | **Prompt Injection** | Input scanning detects secrets/PII but NOT prompt injection syntax (role-switching, jailbreak patterns) | C | Add regex: `ignore.*instructions`, `you are now`, `DAN mode`, quote-escaping markers |
| LLM02 | **Insecure Output Handling** | Adversarial review on high-stakes; guardrails.py for toxicity/PII; DLP post-execution | A- | Autoresearch outputs skip review — add batch review |
| LLM03 | **Training Data Poisoning** | N/A — uses pre-trained models, no custom fine-tuning in fleet | N/A | Monitor autoresearch dataset integrity |
| LLM04 | **Model Denial of Service** | Token budgets per skill; VRAM scaling; no prompt complexity scoring | B- | Add prompt length/complexity check pre-execution; reject if cost > budget |
| LLM05 | **Supply Chain Vulnerabilities** | pip-audit in security_audit skill; smoke test imports all skills | B | Run pip-audit at fleet startup; integrate into CI |
| LLM06 | **Sensitive Info Disclosure** | DLP patterns (API keys, DB URIs); output redaction; secrets in ~/.secrets not DB | A- | Add data classification (public/internal/secret) |
| LLM07 | **Insecure Plugin Design** | safe_path() for file ops; parameterized SQL; JSON validation in post_task() | B+ | Standardize input schema validation across all skills |
| LLM08 | **Excessive Agency** | Affinity routing; QUARANTINE; WAITING_HUMAN; air-gap whitelist (14 skills) | A | Add per-agent capability budget (tool calls per session) |
| LLM09 | **Overreliance** | Adversarial review (PASS/FAIL); watchdog failure streaks; REVIEW re-review loop | A- | Add confidence threshold — reject outputs below 0.3 |
| LLM10 | **Model Theft** | Local models (Ollama, no leak); API keys in ~/.secrets; skills on localhost only | A | No additional measures needed |

**Composite OWASP Grade: B+**

---

## 4. NIST AI RMF Alignment

| Function | Requirement | BigEd CC Implementation | Grade |
|----------|-------------|------------------------|-------|
| **Govern** | Strategy, risk tolerance, accountability | fleet.toml declarative policy; supervisor enforces; no formal RBAC matrix | C+ |
| **Map** | System boundaries, risks, mitigations | FRAMEWORK_BLUEPRINT.md (1000+ lines); TECH_DEBT.md; no formal threat model | B |
| **Measure** | Continuous monitoring, metrics | 39 dashboard endpoints; cost tracking; failure detection; no fairness/bias metrics | B+ |
| **Manage** | Mitigation, incident response, rollback | QUARANTINE; WAITING_HUMAN; DLP; no formal incident response playbook | B- |
| **Govern (Accountability)** | Audit trails, explainability | Debug reports; usage logs; resolutions.jsonl; no model cards | B- |
| **Implement** | Testing, deployment, rollback | Smoke 15/15; soak 25/25; backup scripts; no blue/green deployment | B |

**Composite NIST Grade: B**

---

## 5. Regulatory Compliance Mapping

### 5.1 GDPR

| Article | Requirement | Status | Priority |
|---------|-------------|--------|----------|
| Art. 5 | Lawfulness, purpose limitation, data minimization | Partial (DLP + DATA_SCHEMA contracts) | P1 |
| Art. 13 | Right to information (transparency) | Weak (no privacy notice) | P2 |
| Art. 17 | Right to erasure | **Not implemented** — no automated delete | **P0** |
| Art. 20 | Data portability | Partial (export_data() exists, no standard format) | P2 |
| Art. 22 | Automated decision-making (human review) | Good (WAITING_HUMAN + REVIEW gate) | Done |
| Art. 28 | Data Processor Agreement (DPA) | **Not signed** with Claude/Gemini | **P0** |
| Art. 30 | Record of processing (ROPA) | Partial (fleet.db tracks tasks; no GDPR template) | P1 |
| Art. 32 | Security of processing | Partial (DLP + audit; no TLS or encryption at rest) | P1 |
| Art. 33 | Breach notification (72h) | Weak (DLP alerts exist; no formal SOP) | P1 |
| Art. 35 | DPIA for high-risk processing | **Not conducted** | **P0** |

### 5.2 SOC 2 Type II

| TSC | Requirement | Status | Priority |
|-----|-------------|--------|----------|
| CC6 | Access controls | Good (affinity routing, dashboard auth, air-gap whitelist) | P2 |
| CC7 | Monitoring & alerting | Good (watchdog, dashboard, SSE) | P2 |
| CC8 | Incident response | Weak (quarantine exists; no SOP, no notification) | P1 |
| A1 | Availability | Good (HA fallback, model scaling, stale recovery) | P2 |
| PI1 | Privacy disclosure | Partial (DATA_SCHEMA; no TOS/privacy notice) | P2 |

### 5.3 EU AI Act

| Requirement | Status | Priority |
|-------------|--------|----------|
| Risk assessment & mitigation | Weak (no formal RIMA) | P0 |
| Human oversight | Good (WAITING_HUMAN + REVIEW) | Done |
| Transparency & documentation | Good (FRAMEWORK_BLUEPRINT + OPERATIONS) | P2 |
| Testing & validation | Partial (smoke/soak; no adversarial testing) | P1 |
| Record-keeping | Partial (audit logs; no retention policy) | P1 |

### 5.4 HIPAA (if processing health data)

| Requirement | Status | Priority |
|-------------|--------|----------|
| Encryption at rest | **Not implemented** | P0 |
| Encryption in transit (TLS) | **Not implemented** for dashboard | P0 |
| BAA with API vendors | **Not signed** | P0 |
| PHI-specific audit trail | Not implemented | P1 |
| Access controls (MFA) | Not implemented | P1 |

---

## 6. Compliance Data Flow Architecture

```
User Input (Payload)
    |
[GDPR Art. 6: Lawful Basis Check] ── consent? contract? legitimate interest?
    |
[GDPR Art. 35: DPIA-Covered Skills] ── code_write, lead_research, security_audit
    |
[Layer 1: Pre-Processing Scan]
    |-- PII detection → BLOCK if GDPR-critical (Art. 5: data minimization)
    |-- Secret detection → BLOCK (prevent credential leakage)
    |-- Prompt injection detection → BLOCK (OWASP LLM01)
    |
[Layer 2: Task Queue] ── fleet.db (ENCRYPTED at rest for HIPAA/GDPR)
    |
[Layer 3: Agent Dispatch]
    |-- RBAC check (SOC 2 CC6, EU AI Act human oversight)
    |-- Affinity routing (least privilege)
    |-- Air-gap enforcement (classified environments)
    |
[Layer 4: External APIs] ── Claude, Gemini (HTTPS; DPA signed per GDPR Art. 28)
    |
[Layer 5: Local Processing] ── Ollama (localhost; no data leaves machine)
    |
[Layer 6: Result Processing]
    |-- REVIEW gate (EU AI Act human oversight; OWASP LLM09)
    |-- DLP scrubbing (GDPR Art. 32; OWASP LLM06)
    |-- Audit log (SOC 2 CC7; GDPR Art. 30)
    |-- Output guardrails (toxicity, PII redaction)
    |
[Layer 7: Knowledge Storage] ── with retention_days policy (GDPR Art. 5(1)(e))
    |
[Layer 8: Data Subject Rights]
    |-- Right to erasure: db.delete_task_and_artifacts() (GDPR Art. 17)
    |-- Right to portability: export_data() (GDPR Art. 20)
    |-- Right to restrict: agent quarantine/pause (GDPR Art. 18)
```

---

## 7. Emerging Agent Security Standards

### 7.1 Anthropic Responsible Scaling Policy

| Practice | BigEd CC | Status |
|----------|----------|--------|
| Test for capability limitations | Smoke/soak tests | Partial |
| Red-team before deployment | security_audit + pen_test skills | Partial (no formal red-team SOP) |
| Human oversight for high-stakes | WAITING_HUMAN + REVIEW gate | Good |
| Transparency about capabilities | FRAMEWORK_BLUEPRINT + OPERATIONS | Good |
| Monitor for unintended behaviors | Watchdog (failure streaks, DLP) | Good |
| Maintain human control | Pause workers, approve advisories | Good |

### 7.2 Google SAIF (Secure AI Framework)

| Practice | BigEd CC | Status |
|----------|----------|--------|
| Defense in depth | 5 security layers (input→execution→output→monitoring→audit) | Good |
| Zero trust | Air-gap whitelist; role-based dispatch | Good |
| Secure by default | Offline mode; local Ollama default | Good |
| Least privilege | Role affinity; skill restrictions | Good |
| Continuous monitoring | hw_supervisor 5s; watchdog 60s; SSE real-time | Good |
| Incident response | Quarantine; DLP alerts; no formal SOP | Partial |

### 7.3 OWASP Agentic AI Threats (Beyond LLM Top 10)

| Threat | BigEd CC Mitigation | Gap |
|--------|-------------------|-----|
| Prompt injection → tool abuse | Input scanning warns (doesn't block); safe_path() prevents traversal | Add prompt injection regex; make input scan blocking |
| Agent impersonation | Agent registration via supervisor; heartbeat tracking | No cryptographic agent identity |
| Cascading failures | DAG cascade-fail isolation; failure streak quarantine | No circuit breaker on Ollama calls |
| Knowledge poisoning | DLP scrubs knowledge/; adversarial review on writes | No integrity hash on knowledge files |
| Budget exhaustion | Per-skill daily budgets (warn-only) | Make budgets blocking; add per-task cost estimation |

---

## 8. Trust Architecture (CIA + Accountability)

### Confidentiality
- DLP scrubbing (API keys, DB URIs, private keys, base64 secrets)
- Air-gap mode (no sockets, no secrets loaded, 14 approved skills)
- Offline mode (external APIs blocked)
- Secret storage separate from DB (~/.secrets, not fleet.db)

### Integrity
- Parameterized SQL (no injection)
- WAL mode (crash-safe writes)
- Task DAG validation (cycle detection, cascade-fail)
- Adversarial review (correctness + safety checks)

### Availability
- 4-tier VRAM cascade (prevent OOM)
- Thermal throttling with hysteresis
- HA provider fallback (Claude -> Gemini -> Local)
- Stale task recovery (15min timeout, requeue)
- Worker respawn (15s cooldown)

### Accountability
- Per-worker logging (fleet/logs/{role}.log)
- Cost tracking per skill/model/agent
- Debug reports on crash (JSON, sanitized)
- Resolution tracking (data/resolutions.jsonl)
- Dashboard 39 API endpoints for observability

---

## 9. Remediation Priority Summary

### P0 — Compliance Blockers

| Action | Standards | Effort |
|--------|-----------|--------|
| Implement right to erasure API | GDPR Art. 17 | 1 sprint |
| Conduct DPIA for high-risk skills | EU AI Act, GDPR Art. 35 | 2 weeks |
| Sign DPAs with Claude/Gemini providers | GDPR Art. 28 | 1 week |
| Encrypt fleet.db at rest | HIPAA, GDPR Art. 32 | 1 sprint |
| Enable TLS on dashboard | HIPAA, PCI-DSS | 1 sprint |
| Create incident response SOP | SOC 2 CC8, GDPR Art. 33 | 1 week |

### P1 — Risk Mitigation

| Action | Standards | Effort |
|--------|-----------|--------|
| Formal risk assessment (RIMA) | EU AI Act, SOC 2 | 3 weeks |
| Make input PII scan blocking | GDPR Art. 5 | 1 sprint |
| Prompt injection pattern detection | OWASP LLM01 | 1 sprint |
| Adversarial testing suite | EU AI Act | 2 sprints |
| Add prompt cost estimation pre-execution | OWASP LLM04 | 1 sprint |
| Document ROPA (Record of Processing) | GDPR Art. 30 | 1 week |
| Audit log retention policy (12 months) | SOC 2 | 1 sprint |

### P2 — Governance

| Action | Standards | Effort |
|--------|-----------|--------|
| Data classification policy | ISO 27001 A.7 | 1 week |
| SIEM integration | SOC 2 CC7, ISO 27001 | 2 sprints |
| MFA for operator access | SOC 2 CC6, HIPAA | 1 sprint |
| Model cards for all skills | EU AI Act | 2 weeks |
| Compliance dashboard | All | 2 sprints |
| Red team / external pen test | SOC 2, ISO 27001 | 4 weeks |

---

## 10. Grade Projections

### Current Compliance Grades

| Standard | Grade | Key Factor |
|----------|-------|------------|
| OWASP LLM Top 10 | B+ | Strong on LLM02/06/07/08/09/10; weak on LLM01 (prompt injection) |
| NIST AI RMF | B | Good monitoring/measuring; weak governance/accountability |
| GDPR | C- | No right to erasure, no DPA, no DPIA |
| SOC 2 Type II | C+ | Good monitoring; weak incident response, no 12-month evidence |
| EU AI Act | C+ | Good human oversight; no risk assessment, no adversarial testing |
| HIPAA | D | No encryption at rest, no TLS, no BAA |
| ISO 27001 | C | Good operations; weak governance, suppliers, compliance tracking |

### After P0 Completion (~4 weeks)

| Standard | Current | After P0 | Delta |
|----------|---------|----------|-------|
| OWASP LLM Top 10 | B+ | A- | +1 |
| NIST AI RMF | B | B+ | +1 |
| GDPR | C- | B | +3 |
| SOC 2 Type II | C+ | B | +1 |
| EU AI Act | C+ | B+ | +2 |
| HIPAA | D | B- | +3 |
| ISO 27001 | C | B- | +2 |

### After Full Remediation (~3 months)

| Standard | Current | After P0 | After P1+P2 | Target |
|----------|---------|----------|-------------|--------|
| OWASP LLM Top 10 | B+ | A- | A | A+ |
| NIST AI RMF | B | B+ | A- | A |
| GDPR | C- | B | A- | A |
| SOC 2 Type II | C+ | B | B+ | A- |
| EU AI Act | C+ | B+ | A- | A |
| HIPAA | D | B- | B+ | A- |
| ISO 27001 | C | B- | B | A- |
