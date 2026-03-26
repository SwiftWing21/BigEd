# BigEd CC — Swimlane Diagrams

Complete workflow documentation for all 16 major system processes, organized into 4 grouped charts with shared actor lanes.

## Charts

| # | Chart | Workflows | File |
|---|-------|-----------|------|
| 1 | **Core Runtime** | Boot/Startup, Task Lifecycle, Skill Execution, Model Routing | [01-core-runtime.md](01-core-runtime.md) |
| 2 | **Intelligence Loop** | Idle Evolution, Quality Flywheel, ML Experiments, RAG Pipeline | [02-intelligence-loop.md](02-intelligence-loop.md) |
| 3 | **Operations** | Dr. Ders Health, Self-Healing, Backup/Recovery, Dashboard API | [03-operations.md](03-operations.md) |
| 4 | **Enterprise** | Federation, Marketplace, Security Pipeline, Skill Lifecycle | [04-enterprise.md](04-enterprise.md) |

## Rendering

Charts use Mermaid `sequenceDiagram` syntax. To render PNGs:

```bash
# Install Mermaid CLI
npm install -g @mermaid-js/mermaid-cli

# Render all charts
for f in docs/swimlanes/0*.md; do
  mmdc -i "$f" -o "${f%.md}.png" -t dark -w 2400
done
```

Or view directly in any Mermaid-compatible viewer (GitHub, VS Code with Mermaid extension, etc.).

## Workflow Index

| # | Workflow | Chart | Key Actors |
|---|---------|-------|------------|
| 1 | Boot/Startup Sequence | Core Runtime | Launcher, Supervisor, Ollama, Workers |
| 2 | Task Lifecycle | Core Runtime | Client, DB, Worker, Skill Executor |
| 3 | Skill Execution Pipeline | Core Runtime | Worker, Suite Router, Security Gate, Skill Module |
| 4 | Model Routing & Fallback | Core Runtime | Providers, Claude, Gemini, Ollama, Circuit Breaker |
| 5 | Idle Evolution | Intelligence | Worker, Idle Evolution, DB, Skill Lifecycle |
| 6 | Quality Flywheel | Intelligence | Worker, Flywheel Grading, Gap Analysis |
| 7 | ML Experiment Pipeline | Intelligence | Agent, ExperimentFramework, Train/Eval Functions |
| 8 | RAG Pipeline | Intelligence | Knowledge Writer, RAG Indexer, FTS5, Reranker |
| 9 | Dr. Ders Health Monitoring | Operations | Dr. Ders, GPU, Ollama, hw_state.json |
| 10 | Self-Healing & Circuit Breaker | Operations | Supervisor, Self-Healing, Circuit Breaker |
| 11 | Backup/Recovery | Operations | Backup Manager, Fleet DB, RAG DB |
| 12 | Dashboard API | Operations | Client, Flask, Security Middleware, SSE |
| 13 | Federation | Enterprise | Federation Router, Remote Peers, Mesh Discovery |
| 14 | Marketplace | Enterprise | Publisher, Reviewer, Tenant, Package Storage |
| 15 | Security Pipeline | Enterprise | Security Scanner, Advisory Writer, Patch Applier |
| 16 | Skill Lifecycle | Enterprise | Drafter, Test Runner, Promoter, Deployer |
