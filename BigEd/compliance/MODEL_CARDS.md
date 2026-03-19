# Model Cards — BigEd CC Fleet Skills

> EU AI Act Transparency Requirement | 66 skills documented

## Card Format
Each skill card documents: capability, limitations, intended use, risk level, data handling.

## High-Stakes Skills (require adversarial review)

### code_write
- **Capability:** Generate and modify source code files
- **Limitations:** May hallucinate APIs; no compilation verification
- **Risk Level:** HIGH — can modify production code
- **Review:** Mandatory adversarial review (3 providers)
- **Data:** Source code (potentially proprietary)

### security_audit
- **Capability:** Scan dependencies, check permissions, identify vulnerabilities
- **Limitations:** pip-audit coverage only; no SAST/DAST
- **Risk Level:** HIGH — security findings may be incomplete
- **Review:** Mandatory adversarial review
- **Data:** Dependency lists, file permissions

### pen_test
- **Capability:** Network scanning via nmap, service detection
- **Limitations:** Authorized networks only; nmap target validated
- **Risk Level:** HIGH — network scanning can trigger IDS
- **Review:** Mandatory adversarial review
- **Data:** Network topology, open ports

### legal_draft
- **Capability:** Draft business legal documents
- **Limitations:** Not legal advice; must be reviewed by attorney
- **Risk Level:** HIGH — legal implications
- **Review:** Mandatory adversarial review
- **Data:** Business terms, party names

## Standard Skills (no mandatory review)

### summarize
- **Capability:** Condense text into bullet points
- **Risk Level:** LOW
- **Data:** Input text (may contain PII — scanned pre-execution)

### web_search
- **Capability:** Search web via configured provider
- **Risk Level:** MEDIUM — external data retrieval
- **Data:** Search queries sent to external API

### rag_query
- **Capability:** Query local RAG index
- **Risk Level:** LOW — local only
- **Data:** Query text + indexed knowledge

## Skill Risk Classification

| Risk | Skills | Count |
|------|--------|-------|
| HIGH | code_write, code_write_review, legal_draft, security_audit, pen_test, skill_draft, skill_evolve, branch_manager, product_release, security_apply | 10 |
| MEDIUM | web_search, lead_research, marketing, generate_image, browser_crawl, unifi_manage, home_assistant, mqtt_inspect, github_sync, github_interact | 10 |
| LOW | summarize, discuss, flashcard, rag_query, rag_index, code_review, code_discuss, analyze_results, benchmark, ingest, code_quality, stability_report, marathon_log, evaluate, code_refactor, deploy_skill, service_manager, dataset_synthesize, knowledge_prune, rag_compress, swarm_consensus, ml_bridge, db_migrate, db_encrypt, secret_rotate, git_manager, vision_analyze | 27+ |
