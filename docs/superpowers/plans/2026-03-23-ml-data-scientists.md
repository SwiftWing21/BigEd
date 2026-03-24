# ML Data Scientist Agents + Experiment Framework — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three specialized data scientist agents (ds_rag, ds_fleet, ds_research) sharing a unified experiment framework with autonomy dial, GPU-aware scheduling, and automatic rollback.

**Architecture:** Shared experiment framework (`fleet/experiment.py`) manages the propose, approve, run, evaluate, deploy/rollback lifecycle. Each agent is a standard fleet worker role with dedicated skills. Lightweight models (.pt/.onnx) stored standalone in `fleet/models/`; LLMs go through Ollama. RAG upgrade adds vector search (sentence-transformers) alongside existing BM25/FTS5.

**Tech Stack:** Python 3.11+, scikit-learn, sentence-transformers (optional, for embeddings), ONNX Runtime (optional, for reranker), SQLite/FTS5, existing fleet infrastructure (db.py, marathon.py, supervisor.py, config.py)

**Spec:** `docs/superpowers/specs/2026-03-23-ml-data-scientists-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `fleet/experiment.py` | Experiment lifecycle framework — propose, approve, run, evaluate, deploy, rollback |
| `fleet/skills/rag_eval.py` | RAG retrieval benchmarking (MRR, recall@k, nDCG against eval dataset) |
| `fleet/skills/embedding_train.py` | Fine-tune sentence-transformer on fleet knowledge corpus |
| `fleet/skills/reranker_train.py` | Train lightweight cross-encoder reranker |
| `fleet/skills/rag_benchmark.py` | End-to-end RAG quality benchmark (embed, retrieve, rerank, score) |
| `fleet/skills/router_analyze.py` | Routing model performance analysis (accuracy by skill, agent, time) |
| `fleet/skills/router_retrain.py` | Retrain routing model with experiment framework |
| `fleet/skills/scaler_train.py` | Train predictive scaler from scaling_history |
| `fleet/skills/cost_analyze.py` | Cost pattern analysis, complexity reclassification |
| `fleet/skills/autoresearch_analyze.py` | Parse results.tsv, identify trends, suggest next configs |
| `fleet/skills/autoresearch_trial.py` | Run autoresearch trial through experiment framework |
| `fleet/skills/checkpoint_eval.py` | Evaluate autoresearch checkpoints, recommend best |
| `fleet/models/.gitkeep` | Trained model artifacts directory |

### Modified Files

| File | Change |
|------|--------|
| `fleet/db.py` | Add `ml_experiments` table (extends existing experiments schema) |
| `fleet/fleet.toml` | Add `[experiments]` config section + ds_* agent roles |
| `fleet/supervisor.py` | Add ds_rag/ds_fleet/ds_research to BASE_ROLES |
| `fleet/rag.py` | Add pluggable vector search + reranker hooks alongside BM25 |
| `fleet/views_blueprint.py` | Add `/api/experiments/*` endpoints (list, approve, reject, history) |

---

## Task 1: Experiment Framework Foundation

**Files:**
- Create: `fleet/experiment.py`
- Modify: `fleet/db.py` (add `ml_experiments` table after line 364)
- Modify: `fleet/fleet.toml` (add `[experiments]` section)
- Create: `fleet/models/.gitkeep`

- [ ] **Step 1: Add ml_experiments table to db.py**

In `fleet/db.py`, add after the `experiment_results` table creation (line 364), inside the SCHEMA string:

```sql
CREATE TABLE IF NOT EXISTS ml_experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent TEXT NOT NULL,
    experiment_type TEXT NOT NULL,
    hypothesis TEXT,
    config_json TEXT,
    metrics_before_json TEXT,
    metrics_after_json TEXT,
    status TEXT NOT NULL DEFAULT 'PROPOSED',
    auto_approved INTEGER NOT NULL DEFAULT 0,
    artifact_path TEXT,
    previous_artifact TEXT,
    created_at REAL NOT NULL,
    completed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_ml_exp_agent ON ml_experiments(agent, status);
CREATE INDEX IF NOT EXISTS idx_ml_exp_type ON ml_experiments(experiment_type, status);
```

- [ ] **Step 2: Add [experiments] section to fleet.toml**

Append to `fleet/fleet.toml`:

```toml
[experiments]
auto_approve = false

[experiments.auto_approve_types]
evaluation = true
data_collection = true
embedding_train = false
reranker_train = false
router_retrain = false
autoresearch_trial = false

[experiments.auto_windows]
windows = []
```

- [ ] **Step 3: Create fleet/models directories**

```bash
mkdir -p fleet/models/embeddings fleet/models/reranker
touch fleet/models/.gitkeep
```

- [ ] **Step 4: Write fleet/experiment.py**

Create the full ExperimentFramework class with:
- `propose(agent, experiment_type, hypothesis, config)` -> int
- `run(exp_id, train_fn, eval_fn)` -> dict
- `get(exp_id)` -> dict or None
- `history(agent, experiment_type, limit)` -> list
- `pending_approval()` -> list
- `approve(exp_id)` -> bool
- `reject(exp_id)` -> bool
- `best_experiment(experiment_type, metric)` -> dict or None

Internal methods:
- `_should_auto_approve(experiment_type)` — checks fleet.toml config
- `_in_auto_window(exp_cfg)` — parses "Day HH:MM-HH:MM" schedule strings
- `_check_thermal_safe()` — reads hw_state.json for GPU temp/VRAM
- `_acquire_gpu_lock(exp_id)` / `_release_gpu_lock(exp_id)` — uses db locks table
- `_update_status()`, `_update_metrics()`, `_update_artifact()` — db writes via _retry_write
- `_deploy_gate(exp_id, before, after, artifact_path)` — auto-deploy if approved + metrics improved, rollback if degraded, else queue for HITL

Use lazy `import db` inside methods. Use `db._retry_write()` for all writes. Use `db.get_conn()` for reads.

Statuses: PROPOSED, APPROVED, RUNNING, EVALUATING, DEPLOYED, ROLLED_BACK, REJECTED.

- [ ] **Step 5: Verify experiment.py imports cleanly**

Run: `cd fleet && python -c "from experiment import ExperimentFramework; print('OK')"`

- [ ] **Step 6: Run smoke test**

Run: `python fleet/smoke_test.py --fast`
Expected: 27/27 pass (no regressions)

- [ ] **Step 7: Commit**

```bash
git add fleet/experiment.py fleet/db.py fleet/fleet.toml fleet/models/.gitkeep
git commit -m "feat: experiment framework foundation — lifecycle, GPU lock, autonomy dial"
```

---

## Task 2: RAG Vector Search + Pluggable Models

**Files:**
- Modify: `fleet/rag.py` (add vector search alongside BM25)

**Context:** RAG currently uses BM25 via FTS5 only. This adds optional vector search using sentence-transformers embeddings, and a reranker hook. BM25 remains the default; vector search activates when an embedding model is available in `fleet/models/embeddings/`.

- [ ] **Step 1: Add vector search methods to rag.py**

Add these methods to the existing RAG class (after the `search` method around line 314):

- `_load_embedding_model()` — loads most recent .pt model from `fleet/models/embeddings/`, caches on instance. Falls back to None if sentence-transformers not installed.
- `vector_search(query, limit)` — encodes query + all chunks, cosine similarity ranking. Falls back to BM25 if no model.
- `hybrid_search(query, limit, bm25_weight=0.4)` — combines BM25 and vector search with reciprocal rank fusion (k=60).
- `rerank(query, results, limit)` — loads cross-encoder from `fleet/models/reranker/`, re-scores results. Falls back to original order if no model.

All methods must have `try/except Exception` with `log.warning()` and graceful fallback.

- [ ] **Step 2: Verify rag.py still imports and BM25 search works**

Run: `cd fleet && python -c "from rag import *; print('rag imports OK')"`

- [ ] **Step 3: Run smoke test**

Run: `python fleet/smoke_test.py --fast`

- [ ] **Step 4: Commit**

```bash
git add fleet/rag.py
git commit -m "feat: pluggable vector search + reranker hooks in rag.py"
```

---

## Task 3: ds_rag Skills (4 skills)

**Files:**
- Create: `fleet/skills/rag_eval.py`
- Create: `fleet/skills/embedding_train.py`
- Create: `fleet/skills/reranker_train.py`
- Create: `fleet/skills/rag_benchmark.py`

All skills follow the standard contract: SKILL_NAME, DESCRIPTION, REQUIRES_NETWORK, `run(payload, config) -> dict`.

- [ ] **Step 1: Write rag_eval.py**

Skill that benchmarks RAG retrieval quality. Auto-generates an eval dataset from successful task queries (query + top chunks from DONE tasks = positive pairs). Stores in `fleet/models/rag_eval_dataset.json`. Measures MRR and recall@10 using current BM25 search. Returns metrics dict.

- [ ] **Step 2: Write embedding_train.py**

Skill that fine-tunes a sentence-transformer (default: all-MiniLM-L6-v2) on fleet knowledge corpus using MultipleNegativesRankingLoss. Uses ExperimentFramework for lifecycle. Saves model to `fleet/models/embeddings/fleet_embed_<timestamp>`. REQUIRES_NETWORK = True (may need to download base model).

- [ ] **Step 3: Write reranker_train.py**

Skill that trains a cross-encoder reranker (default: cross-encoder/ms-marco-MiniLM-L-6-v2) on query-doc relevance pairs derived from task history. Uses ExperimentFramework. Saves to `fleet/models/reranker/fleet_reranker_<timestamp>`. REQUIRES_NETWORK = True.

- [ ] **Step 4: Write rag_benchmark.py**

Skill that runs end-to-end comparison: BM25 vs vector vs hybrid search. Uses rag_eval dataset. Reports MRR and recall@10 for each strategy. REQUIRES_NETWORK = False.

- [ ] **Step 5: Verify all skills import**

Run: `cd fleet && python -c "from skills.rag_eval import SKILL_NAME; from skills.embedding_train import SKILL_NAME; from skills.reranker_train import SKILL_NAME; from skills.rag_benchmark import SKILL_NAME; print('All rag skills OK')"`

- [ ] **Step 6: Run smoke test**

Run: `python fleet/smoke_test.py --fast`

- [ ] **Step 7: Commit**

```bash
git add fleet/skills/rag_eval.py fleet/skills/embedding_train.py fleet/skills/reranker_train.py fleet/skills/rag_benchmark.py
git commit -m "feat: ds_rag skills — eval, embedding train, reranker train, benchmark"
```

---

## Task 4: ds_fleet Skills (4 skills)

**Files:**
- Create: `fleet/skills/router_analyze.py`
- Create: `fleet/skills/router_retrain.py`
- Create: `fleet/skills/scaler_train.py`
- Create: `fleet/skills/cost_analyze.py`

- [ ] **Step 1: Write router_analyze.py**

Analyzes routing model performance over N days. Queries tasks table for DONE/FAILED, computes accuracy by skill and agent, identifies worst-performing skill-agent pairs (>50% failure rate with 3+ samples). Returns skill_stats, agent_stats, worst_skills.

- [ ] **Step 2: Write router_retrain.py**

Wraps `ml_router.train_routing_model()` in ExperimentFramework. Eval function reads `ml_router.get_model_status()` accuracy. REQUIRES_NETWORK = False.

- [ ] **Step 3: Write scaler_train.py**

Wraps `predictive_scaler.train_scaler_model()` in ExperimentFramework. REQUIRES_NETWORK = False.

- [ ] **Step 4: Write cost_analyze.py**

Queries usage table aggregated by skill+model over N days. Computes cost per call, identifies expensive skills, recommends complexity tier downgrades. REQUIRES_NETWORK = False.

- [ ] **Step 5: Verify imports and run smoke test**

Run: `cd fleet && python -c "from skills.router_analyze import SKILL_NAME; from skills.router_retrain import SKILL_NAME; from skills.scaler_train import SKILL_NAME; from skills.cost_analyze import SKILL_NAME; print('OK')"`
Run: `python fleet/smoke_test.py --fast`

- [ ] **Step 6: Commit**

```bash
git add fleet/skills/router_analyze.py fleet/skills/router_retrain.py fleet/skills/scaler_train.py fleet/skills/cost_analyze.py
git commit -m "feat: ds_fleet skills — router analyze/retrain, scaler train, cost analyze"
```

---

## Task 5: ds_research Skills (3 skills)

**Files:**
- Create: `fleet/skills/autoresearch_analyze.py`
- Create: `fleet/skills/autoresearch_trial.py`
- Create: `fleet/skills/checkpoint_eval.py`

- [ ] **Step 1: Write autoresearch_analyze.py**

Parses `autoresearch/results.tsv` (TSV with val_bpb column). Sorts by val_bpb, detects plateau (last 5 within 1% of each other), suggests next hyperparameters. REQUIRES_NETWORK = False.

- [ ] **Step 2: Write autoresearch_trial.py**

Proposes and runs autoresearch trial through ExperimentFramework. Spawns `autoresearch/train.py` as subprocess with config passed via env vars. Uses `CREATE_NO_WINDOW` flag on Windows. 6-minute timeout. REQUIRES_NETWORK = False.

- [ ] **Step 3: Write checkpoint_eval.py**

Lists checkpoints in `autoresearch/checkpoints/`, reads metadata sidecars (.json), ranks by val_bpb, recommends best. REQUIRES_NETWORK = False.

- [ ] **Step 4: Verify imports and run smoke test**

Run: `cd fleet && python -c "from skills.autoresearch_analyze import SKILL_NAME; from skills.autoresearch_trial import SKILL_NAME; from skills.checkpoint_eval import SKILL_NAME; print('OK')"`
Run: `python fleet/smoke_test.py --fast`

- [ ] **Step 5: Commit**

```bash
git add fleet/skills/autoresearch_analyze.py fleet/skills/autoresearch_trial.py fleet/skills/checkpoint_eval.py
git commit -m "feat: ds_research skills — autoresearch analyze, trial, checkpoint eval"
```

---

## Task 6: Agent Registration + Experiment API

**Files:**
- Modify: `fleet/fleet.toml` (add ds_* to disabled_agents)
- Modify: `fleet/supervisor.py` (add to BASE_ROLES at line 58)
- Modify: `fleet/views_blueprint.py` (add experiment API endpoints)

- [ ] **Step 1: Add ds_* agent roles to fleet.toml disabled_agents**

Add `"ds_rag"`, `"ds_fleet"`, `"ds_research"` to the `disabled_agents` list in `[fleet]`.

- [ ] **Step 2: Add ds_* to BASE_ROLES in supervisor.py**

Add `"ds_rag"`, `"ds_fleet"`, `"ds_research"` to the `BASE_ROLES` list at line 58.

- [ ] **Step 3: Add experiment API endpoints to views_blueprint.py**

Add 4 endpoints after existing routes:
- `GET /api/experiments` — list recent experiments (filter by agent, type)
- `GET /api/experiments/pending` — list HITL approval queue
- `POST /api/experiments/<int:exp_id>/approve` — approve pending experiment
- `POST /api/experiments/<int:exp_id>/reject` — reject pending experiment

All use lazy `from experiment import ExperimentFramework` inside handlers.

- [ ] **Step 4: Run smoke test**

Run: `python fleet/smoke_test.py --fast`

- [ ] **Step 5: Commit**

```bash
git add fleet/fleet.toml fleet/supervisor.py fleet/views_blueprint.py
git commit -m "feat: register ds_rag/ds_fleet/ds_research agents + experiment API endpoints"
```

---

## Task 7: Final Verification

- [ ] **Step 1: Full smoke test**

Run: `python fleet/smoke_test.py --fast`
Expected: All pass

- [ ] **Step 2: Verify experiment framework end-to-end**

```bash
cd fleet && python -c "
from experiment import ExperimentFramework
fw = ExperimentFramework()
eid = fw.propose('ds_rag', 'evaluation', 'test', {'key': 'val'})
exp = fw.get(eid)
print(f'Proposed: #{eid}, status={exp[\"status\"]}, auto={exp[\"auto_approved\"]}')
assert exp['status'] == 'APPROVED', 'evaluation type should be auto-approved'
print('Experiment framework OK')
"
```

- [ ] **Step 3: Verify all 11 new skills import with correct contract**

```bash
cd fleet && python -c "
import importlib
skills = ['rag_eval', 'embedding_train', 'reranker_train', 'rag_benchmark',
          'router_analyze', 'router_retrain', 'scaler_train', 'cost_analyze',
          'autoresearch_analyze', 'autoresearch_trial', 'checkpoint_eval']
for s in skills:
    mod = importlib.import_module(f'skills.{s}')
    assert hasattr(mod, 'SKILL_NAME'), f'{s} missing SKILL_NAME'
    assert hasattr(mod, 'DESCRIPTION'), f'{s} missing DESCRIPTION'
    assert hasattr(mod, 'REQUIRES_NETWORK'), f'{s} missing REQUIRES_NETWORK'
    assert hasattr(mod, 'run'), f'{s} missing run()'
    print(f'  {mod.SKILL_NAME}: OK')
print(f'All {len(skills)} skills verified')
"
```

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: ML Data Scientists v0.401.00b — experiment framework + 3 agents + 11 skills"
```
