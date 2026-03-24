# ML Data Scientist Agents + Experiment Framework

**Date:** 2026-03-23
**Status:** Draft (Section 1 approved, Sections 2-5 pending)
**Approach:** C — Agent Roles + Shared Experiment Framework

## Problem

BigEd has scattered ML infrastructure (ml_router, predictive_scaler, autoresearch, skill_train, reinforcement) but no unified experiment pipeline and no specialized agents to autonomously improve ML model quality. RAG retrieval suffers from both poor embeddings and poor ranking. The routing model and autoresearch pipeline run but aren't actively optimized.

## Goals

1. **Three specialized data scientist agents** — each owns a domain of ML improvement
2. **Shared experiment framework** — consistent train → eval → deploy/rollback pipeline
3. **End-to-end RAG quality** — better embeddings + learned reranker
4. **Autonomy dial** — safe-by-default with configurable auto-approve windows and per-type overrides
5. **Model storage** — lightweight models (embeddings, reranker) as standalone .pt/.onnx; fine-tuned LLMs through Ollama/GGUF

## Non-Goals

- Replacing the existing autoresearch/train.py (it stays as-is, the agent wraps it)
- Multi-GPU training (single-GPU constraint remains)
- Federated learning across fleets (future)

---

## Design Decisions (from brainstorming)

| Question | Answer |
|----------|--------|
| Primary goal | RAG intelligence upgrade (C) + data scientist agents (B) |
| RAG pain point | Both — bad embeddings feed bad retrieval (end-to-end) |
| Agent scope | 3 separate data scientists: RAG-focused, Fleet ML, Autoresearch |
| Autonomy level | Mixed — safe ops autonomous, risky ops HITL with configurable auto-approve windows |
| Model storage | Hybrid — embeddings/reranker standalone (.pt/.onnx), LLMs through Ollama (GGUF) |
| Architecture | Approach C — shared experiment framework, agents define experiments |

---

## 1. Experiment Framework (`fleet/experiment.py`)

**Status:** Approved

The core abstraction. Every ML operation — training an embedding, retraining the router, running an autoresearch trial — is an **Experiment**.

### Lifecycle

```
PROPOSED → APPROVED → RUNNING → EVALUATING → [DEPLOYED | ROLLED_BACK | REJECTED]
```

### Experiment Record

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Auto-increment primary key |
| `agent` | str | Which data scientist agent owns this |
| `experiment_type` | str | embedding_train, reranker_train, router_retrain, autoresearch_trial, evaluation, data_collection, etc. |
| `hypothesis` | str | What the agent expects to improve |
| `config` | JSON | Hyperparameters, model paths, dataset refs |
| `metrics_before` | JSON | Eval metrics measured before training |
| `metrics_after` | JSON | Eval metrics measured after training |
| `status` | str | PROPOSED / APPROVED / RUNNING / EVALUATING / DEPLOYED / ROLLED_BACK / REJECTED |
| `auto_approved` | bool | Whether this bypassed HITL |
| `artifact_path` | str | Path to trained model artifact |
| `previous_artifact` | str | Path to previous model (for rollback) |
| `created_at` | datetime | When proposed |
| `completed_at` | datetime | When finished (any terminal state) |

### Framework Responsibilities

- **GPU lock integration** — acquires training lock from `marathon.py` before GPU work, respects Dr. Ders thermal state via `hw_state.json`
- **Eval harness** — each experiment type registers an eval function; framework runs before/after training to measure delta
- **Deploy gate** — checks autonomy config: auto-approve window active OR experiment type whitelisted → deploy. Otherwise queue for HITL review.
- **Rollback** — stores previous model artifacts; if metrics degrade post-deploy, auto-reverts and logs warning
- **History** — all experiments logged to `experiments` table (schema already exists in db.py)

### Autonomy Configuration (`fleet.toml`)

```toml
[experiments]
# Global default — HITL required for all deploys
auto_approve = false

# Per-type overrides (safe operations always autonomous)
[experiments.auto_approve_types]
evaluation = true          # running evals is always safe
data_collection = true     # collecting metrics is safe
embedding_train = false    # needs approval by default
reranker_train = false
router_retrain = false
autoresearch_trial = false

# Scheduled auto-approve windows (cron-style)
# During these periods, ALL experiment types can auto-deploy
[experiments.auto_windows]
windows = ["Sat 00:00-06:00", "Sun 00:00-06:00"]
```

### API

```python
# Skills/agents use this interface
from experiment import ExperimentFramework

fw = ExperimentFramework()

# Propose an experiment
exp_id = fw.propose(
    agent="ds_rag",
    experiment_type="embedding_train",
    hypothesis="Domain-specific fine-tuning improves recall@10 by 15%",
    config={"base_model": "all-MiniLM-L6-v2", "epochs": 5, "lr": 2e-5},
)

# Framework handles: approval check → GPU lock → run → eval → deploy/rollback
fw.run(exp_id, train_fn=my_train_function, eval_fn=my_eval_function)

# Query history
results = fw.history(agent="ds_rag", limit=20)
best = fw.best_experiment(experiment_type="embedding_train", metric="recall_at_10")
```

---

## 2. Agent: ds_rag (RAG Data Scientist)

**Status:** Pending design

Owns end-to-end RAG quality: embeddings, reranking, retrieval evaluation.

### Planned Responsibilities
- Train/fine-tune embedding models on fleet knowledge corpus
- Build and train a learned reranker (cross-encoder)
- Maintain RAG eval dataset (query → expected chunks)
- Run retrieval benchmarks (MRR, recall@k, nDCG)
- A/B test retrieval strategies (embedding swap, reranker toggle)

### Model Storage
- Embeddings: `fleet/models/embeddings/<name>.pt` (standalone, always loaded)
- Reranker: `fleet/models/reranker/<name>.onnx` (lightweight cross-encoder)

---

## 3. Agent: ds_fleet (Fleet ML Data Scientist)

**Status:** Pending design

Owns fleet operational ML: task routing, predictive scaling, cost optimization.

### Planned Responsibilities
- Monitor and retrain routing model (ml_router.py) with better features
- Train predictive scaler model with collected scaling_history data
- Analyze skill cost patterns, recommend complexity reclassifications
- Run A/B tests on routing strategies

### Model Storage
- Routing model: `fleet/data/routing_model.pkl` (existing path)
- Scaler model: `fleet/data/scaler_model.pkl` (existing path)

---

## 4. Agent: ds_research (Autoresearch Data Scientist)

**Status:** Pending design

Owns autoresearch experiment management: hyperparameter search, result analysis, model selection.

### Planned Responsibilities
- Analyze autoresearch/results.tsv, identify promising hyperparameter directions
- Propose and run autoresearch trials with varied configs
- Track val_bpb trends, detect plateau/regression
- Recommend best checkpoints for deployment

### Model Storage
- Autoresearch models: `autoresearch/checkpoints/` (existing path, through Ollama for deployment)

---

## 5. Integration

**Status:** Pending design

### Agent Registration
- 3 new agent roles in `fleet.toml [fleet]`: ds_rag, ds_fleet, ds_research
- Disabled by default (added to `disabled_agents` until operator enables)
- Each agent has dedicated skills mapped in supervisor

### Supervisor Integration
- Experiment framework registered as supervisor module
- Idle cycle checks for pending experiments needing HITL approval
- Dashboard: experiment history panel, approval queue, metrics charts

### Skill Mapping
- ds_rag: rag_eval, embedding_train, reranker_train, rag_benchmark
- ds_fleet: router_analyze, router_retrain, scaler_train, cost_analyze
- ds_research: autoresearch_analyze, autoresearch_trial, checkpoint_eval

### New Files (estimated)
| File | Purpose |
|------|---------|
| `fleet/experiment.py` | Experiment framework |
| `fleet/models/` | Trained model artifacts directory |
| `fleet/skills/rag_eval.py` | RAG evaluation benchmarks |
| `fleet/skills/embedding_train.py` | Embedding fine-tuning |
| `fleet/skills/reranker_train.py` | Cross-encoder reranker training |
| `fleet/skills/rag_benchmark.py` | End-to-end RAG benchmark |
| `fleet/skills/router_analyze.py` | Routing model analysis |
| `fleet/skills/scaler_train.py` | Predictive scaler training |
| `fleet/skills/cost_analyze.py` | Cost pattern analysis |
| `fleet/skills/autoresearch_analyze.py` | Experiment result analysis |
| `fleet/skills/checkpoint_eval.py` | Model checkpoint evaluation |

### Modified Files
| File | Change |
|------|--------|
| `fleet/fleet.toml` | [experiments] config section, agent roles |
| `fleet/db.py` | experiments table schema (extend existing) |
| `fleet/supervisor.py` | Experiment HITL queue check in idle loop |
| `fleet/rag.py` | Pluggable embedding/reranker model loading |
| `fleet/dashboard.py` | Experiment dashboard panel |

---

## Testing Per Section

- **Experiment framework**: Unit tests for lifecycle, GPU lock, autonomy config, rollback
- **ds_rag**: RAG eval benchmark with known-good queries, embedding swap test
- **ds_fleet**: Routing model A/B with held-out task set
- **ds_research**: Autoresearch trial with 1-min budget, metric tracking
- **Integration**: Smoke test for experiment HITL flow end-to-end
