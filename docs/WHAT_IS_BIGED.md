# What BigEd CC Is (and Is Not)

> **BigEd CC** — Big Edge Compute Command
> Local-first autonomous AI agent fleet for software engineering, research, and compliance.

---

## What BigEd IS

**A local AI workforce that runs on your hardware.** BigEd manages a fleet of specialized AI agents that collaborate on code review, research, security auditing, knowledge management, and document processing — all running locally via Ollama, with optional cloud fallback.

**An operator-controlled system.** You dispatch tasks, agents execute them, and results flow back through a dashboard. Agents can request human input (HITL) but never act outside their scope without approval.

**A platform, not a chatbot.** BigEd doesn't chat — it works. Agents have roles (coder, researcher, security, analyst, planner, archivist), skills (84 registered), and quality scores. They evolve their capabilities during idle time.

## What BigEd IS NOT

- **Not a cloud service.** Runs on your machine. Your data stays local. Cloud APIs (Claude, Gemini) are optional fallbacks, never required.
- **Not a wrapper around ChatGPT.** Uses Ollama (local models) as the primary inference engine. Cloud is the backup, not the default.
- **Not fully autonomous.** Agents follow the skill contract — they can't install packages, modify system config, or access the internet without explicit skill authorization and network flags.
- **Not a replacement for human judgment.** HITL gates exist for security advisories, model changes, clinical reviews, and any decision with real-world impact.

---

## Feature Summary

### Fleet (6 active roles, 84 skills)

| Role | What it does | Key outputs |
|------|-------------|-------------|
| **Coder (x3)** | Code review, generation, refactoring, skill development, git management | `knowledge/code_reviews/`, `code_drafts/`, `refactors/` |
| **Researcher** | Web search, paper fetching, content synthesis, IoT integration | `knowledge/summaries/`, `leads/` |
| **Archivist** | RAG indexing, document ingestion, flashcard generation, knowledge pruning | RAG index (rag.db), `flashcards.jsonl` |
| **Analyst** | Results analysis, benchmarks, cost optimization, quality evaluation, regression detection | `knowledge/evaluations/`, `stability/`, `quality/` |
| **Security** | Security audits, penetration testing, secret rotation, encryption, advisory management | `knowledge/security/pending/`, `applied/` |
| **Planner** | Workload planning, curriculum updates, evolution coordination, model recommendations | `knowledge/reports/` |

### Skills by Category (84 total)

| Category | Count | Examples |
|----------|-------|---------|
| Code & Development | 24 | code_review, code_write, git_manager, skill_evolve, deploy_skill |
| Research & Analysis | 11 | web_search, arxiv_fetch, summarize, research_loop |
| Security | 8 | security_audit, pen_test, secret_rotate, db_encrypt |
| Data & Knowledge | 10 | rag_index, ingest, flashcard, dataset_synthesize |
| Operations & Planning | 12 | plan_workload, hardware_profiler, model_recommend, oom_prevent |
| Output Generation | 7 | screenshot, vision_analyze, generate_image |
| Cost & Quality | 5 | token_optimizer, regression_detector, billing_ocr |
| System Monitoring | 3 | home_assistant, unifi_manage, mqtt_inspect |
| Specialized | 4 | clinical_review, speech_to_text, legal_draft |

### Data Systems

| System | Storage | Purpose |
|--------|---------|---------|
| **fleet.db** (SQLite) | 11 tables | Tasks, agents, messages, usage, audit, context, alerts, locks |
| **rag.db** (FTS5) | 3 tables | Full-text search index on markdown files (BM25 ranking) |
| **tools.db** | Launcher data | Module state, CRM contacts, boot timing |
| **audit.jsonl** | HMAC-signed log | Tamper-evident event trail |
| **knowledge/** | 20+ directories | Agent outputs: reviews, drafts, reports, security advisories |
| **hw_state.json** | Runtime | GPU/CPU temps, VRAM, loaded models (updated every 5s) |

### Provider Chain (HA Fallback)

```
Claude (Sonnet 4.6) → Gemini (2.0 Flash) → MiniMax (M1-80k) → Local (Ollama qwen3:8b)
```

Circuit breaker: 3 failures in 5 minutes triggers failover. Automatic recovery with exponential backoff.

### Dual Supervisor Architecture

| Supervisor | Responsibility | Cadence |
|-----------|---------------|---------|
| **Supervisor** (`supervisor.py`) | Process lifecycle, worker scaling, task dispatch, federation, config reload | Main loop (~1s) |
| **Dr. Ders** (`hw_supervisor.py`) | Model health, VRAM/thermal monitoring, keepalive, GPU scaling | Event-driven (5-30s) |

### Dashboard (45+ API endpoints)

Web UI at `localhost:5555` with:
- Live agent status + sparklines (SSE real-time updates)
- Cost intelligence (daily/weekly/monthly spend by skill/model/agent)
- Knowledge browser (code reviews, security advisories, reports)
- SLA monitoring (task completion times, success rates)
- Audit trail viewer (filtered, paginated, CSV/JSON export)
- Cache stats, federation peers, alert management

### Security & Compliance

| Feature | Status |
|---------|--------|
| SQLCipher encryption | Optional (BIGED_DB_KEY env var) |
| TLS (auto-generated self-signed) | Dashboard + remote access |
| RBAC (admin/operator/viewer) | 5 roles, 7 permission actions |
| HIPAA/DITL mode | Safe Harbor de-identification, PHI audit, 7-year retention |
| SOC 2 file access | FileSystemGuard with per-zone permissions |
| Tamper-evident audit | HMAC-SHA256 signed JSONL events |
| SSRF protection | Blocks private IPs, metadata endpoints |
| XSS protection | 49 escapeHTML calls, CSP headers |

### Deployment Options

| Mode | How |
|------|-----|
| **Desktop** | `python BigEd/launcher/launcher.py` (customtkinter GUI) |
| **CLI** | `python fleet/lead_client.py status/task/export` |
| **Web** | `python fleet/web_app.py` (Flask, browser-based) |
| **Docker** | `docker-compose up` (fleet + ollama + dashboard) |
| **Kubernetes** | Helm chart in `deploy/helm/biged-cc/` |
| **USB offline** | `python BigEd/launcher/create_usb_media.py` |
| **Air-gap** | `air_gap_mode = true` in fleet.toml |

---

## How Agents Operate

### Task Lifecycle

```
1. Task created → PENDING (queue)
2. Worker claims task → RUNNING (assigned_to = agent_name)
3. Skill executes → produces result JSON
4. Quality scored → intelligence_score (0.0-1.0)
5. Review gate → optional evaluator-optimizer cycle (max 2 rounds)
6. Task completed → DONE (result stored) or FAILED (error logged)
```

### What Agents Output

Every skill produces a structured result dict. Common patterns:

| Skill Type | Output | Storage |
|-----------|--------|---------|
| Code review | Markdown review with line-level findings | `knowledge/code_reviews/<file>_review_<date>.md` |
| Security audit | Advisory with severity + fix recommendations | `knowledge/security/pending/advisory_<id>.md` |
| Skill evolution | Modified skill code (draft) | `knowledge/code_drafts/<name>_draft_<date>.py` |
| Web search | Ranked results with snippets | Result JSON in task table |
| RAG index | Updated full-text search index | `rag.db` (FTS5 chunks) |
| Summarize | Markdown summary | `knowledge/summaries/` |
| Stability report | System health analysis | `knowledge/stability/stability_<date>.md` |
| Clinical review | 5-stage pipeline with PHI audit | `knowledge/ditl/<pipeline_id>.json` |

### Agent Intelligence

- **Tier 1 scoring** (every task): Mechanical checks — format, completeness, error detection (0.0-1.0)
- **Tier 2 scoring** (10% sample): LLM-based coherence/correctness evaluation
- **IQ display**: Per-agent average shown on Fleet tab cards
- **Expertise tracking**: Top skills by IQ score tracked over 7-day window
- **Affinity routing**: Tasks routed to agents with highest historical IQ for that skill type

### Context & Memory

- **Per-agent context window**: Last 5 turns, 4096 token budget, summarize-on-overflow
- **RAG knowledge base**: All markdown files indexed with FTS5, BM25-ranked search
- **Fleet messages**: Inter-agent communication via channels (fleet/supervisor/agent/pool)
- **Cost tracking**: Per-call token counts, cache hits, provider attribution

### Event Triggers

| Trigger | What it does |
|---------|-------------|
| **File watch** | New file in ~/Downloads → auto-ingest task |
| **Scheduled tasks** | Cron-like: daily code review, hourly health check |
| **Webhook** | POST /api/trigger → dispatch any skill |
| **Idle evolution** | Queue empty → agents self-improve skills |
| **Predictive scaling** | Queue acceleration → proactive agent scale-up |
| **Federation overflow** | >85% capacity → route to peer fleet |

---

## Document Throughput

| Provider | Speed | Estimated Pages/Hour (per agent) |
|----------|-------|----------------------------------|
| Local (qwen3:8b, GPU) | ~114 tok/s | ~28 pages |
| Local (qwen3:4b, CPU) | ~30 tok/s | ~7 pages |
| Claude (Sonnet) | ~200 tok/s | ~50 pages |
| Gemini (Flash) | ~300 tok/s | ~75 pages |

**Fleet throughput** (8 active agents, local GPU): ~224 pages/hour

*Estimate assumes 500 tokens/page for analysis tasks. Code review is slower (needs file reads). Summarization is faster (output-heavy).*

---

## Version: 0.170.04b (Beta)

85 skills | 45+ API endpoints | 11 DB tables | 6 agent roles | 30+ config sections

Apache 2.0 License | Windows/Linux/macOS | Offline/Air-Gap capable
