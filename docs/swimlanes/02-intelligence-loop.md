# Intelligence Loop — Swimlane Diagrams

Workflows 5–8 cover the self-improvement and knowledge systems that run continuously
alongside the task fleet: idle-time skill evolution, quality grading, ML experimentation,
and RAG ingestion/querying.

---

## 5. Idle Evolution

When a worker finds no tasks for `IDLE_THRESHOLD` (3) consecutive polls, it enters the
idle evolution path. Multiple guards (cooldown, cost anomaly, queue depth, per-agent and
cross-worker dedup) prevent runaway evolution. HITL mode gates the final dispatch behind
human approval; in fully-automated mode the skill is dispatched immediately.

```mermaid
sequenceDiagram
    participant Worker
    participant IdleEvolution
    participant DB
    participant SkillLifecycle
    participant Knowledge

    Worker->>Worker: idle_count += 1
    alt idle_count < IDLE_THRESHOLD (3)
        Worker->>Worker: continue polling
    else idle_count >= IDLE_THRESHOLD
        Worker->>IdleEvolution: check_cooldown(now, last_idle_run)
        alt (now - last_idle_run) < 60s
            IdleEvolution-->>Worker: skip — cooldown active
        else cooldown passed
            IdleEvolution->>IdleEvolution: read cost_anomaly_throttle file
            alt cost anomaly detected
                IdleEvolution-->>Worker: skip — cost anomaly throttle
            else no cost anomaly
                IdleEvolution->>DB: SELECT COUNT(*) FROM tasks WHERE status='PENDING'
                DB-->>IdleEvolution: pending_count
                alt pending_count >= 3
                    IdleEvolution-->>Worker: skip — queue too deep
                else pending_count < 3
                    IdleEvolution->>IdleEvolution: pick_skill(role)
                    IdleEvolution->>DB: SELECT skill FROM tasks WHERE status='DONE'
                    DB-->>IdleEvolution: completed_skill_names[]
                    IdleEvolution->>DB: SELECT skill, MAX(created_at) FROM idle_runs GROUP BY skill
                    DB-->>IdleEvolution: last_run_map{}
                    IdleEvolution->>IdleEvolution: sort by staleness (oldest first)
                    IdleEvolution->>IdleEvolution: take bottom 30% (min 3) as candidates
                    IdleEvolution->>DB: SELECT skill, last_run FROM idle_runs WHERE agent=this AND last_run > now-4h
                    DB-->>IdleEvolution: agent_recent[]
                    IdleEvolution->>IdleEvolution: filter: remove per-agent 4h cooldown hits
                    IdleEvolution->>DB: SELECT skill FROM active_evolutions WHERE worker != this
                    DB-->>IdleEvolution: in_progress_elsewhere[]
                    IdleEvolution->>IdleEvolution: filter: cross-worker dedup
                    IdleEvolution->>IdleEvolution: weighted random selection (older = higher weight)
                    IdleEvolution-->>Worker: chosen_skill
                    Note over Worker,IdleEvolution: HITL gate
                    alt HITL enabled
                        Worker->>DB: INSERT task(status=WAITING_HUMAN, hitl_proposal=chosen_skill)
                        DB-->>Worker: task_id
                        Note over Worker,DB: Wait for human approval via dashboard
                    else auto mode
                        Worker->>SkillLifecycle: dispatch(skill_test OR evolution_coordinator, chosen_skill)
                        SkillLifecycle->>Knowledge: log idle_run(skill, agent, timestamp)
                        Knowledge-->>SkillLifecycle: ok
                        SkillLifecycle-->>Worker: dispatched
                    end
                end
            end
        end
    end
```

---

## 6. Quality Flywheel

Every completed task passes through a six-dimension grading pipeline. The blended A–F
grade drives automatic follow-up: high grades need no action; mid-tier grades surface
suggestions; failing grades auto-queue targeted fix tasks. An evidence audit runs in
parallel to flag potential hallucinations.

```mermaid
sequenceDiagram
    participant Worker
    participant FlywheelGrading
    participant DB
    participant GapAnalysis
    participant EvidenceAudit

    Worker->>FlywheelGrading: task_completed(task_id, result)
    FlywheelGrading->>FlywheelGrading: grade_completeness(result)
    Note over FlywheelGrading: Check all required output fields are present
    FlywheelGrading->>DB: SELECT related knowledge entries
    DB-->>FlywheelGrading: knowledge_context
    FlywheelGrading->>FlywheelGrading: grade_consistency(result, knowledge_context)
    Note over FlywheelGrading: Cross-reference with existing knowledge base
    FlywheelGrading->>FlywheelGrading: grade_actionability(result)
    Note over FlywheelGrading: Verify output is concrete, not vague
    FlywheelGrading->>FlywheelGrading: grade_coverage(result, task)
    Note over FlywheelGrading: How much of the task space was addressed
    FlywheelGrading->>FlywheelGrading: grade_freshness(result)
    Note over FlywheelGrading: Are sources/references current?
    FlywheelGrading->>FlywheelGrading: compute_blended_score()
    FlywheelGrading->>FlywheelGrading: score_to_grade() → A/B/C/D/F
    FlywheelGrading->>DB: UPDATE task SET grade=grade, score=score WHERE id=task_id
    DB-->>FlywheelGrading: ok

    FlywheelGrading->>GapAnalysis: analyze_gaps(task_id, dimension_scores)
    GapAnalysis->>GapAnalysis: identify dimensions below threshold
    alt Grade A or B
        GapAnalysis-->>Worker: no action required
    else Grade C
        GapAnalysis->>DB: INSERT suggestion(type=improvement, task_id=task_id)
        DB-->>GapAnalysis: suggestion_id
        GapAnalysis-->>Worker: improvement suggestion queued
    else Grade D or F
        GapAnalysis->>DB: INSERT task(type=skill_evolve OR code_quality, priority=high, ref=task_id)
        DB-->>GapAnalysis: fix_task_id
        GapAnalysis-->>Worker: fix task auto-queued
    end

    FlywheelGrading->>EvidenceAudit: audit(task_id, result)
    EvidenceAudit->>EvidenceAudit: check hallucination markers
    EvidenceAudit->>EvidenceAudit: discover_novel_patterns(result)
    alt hallucination detected
        EvidenceAudit->>DB: UPDATE task SET flag=HALLUCINATION_REVIEW WHERE id=task_id
        DB-->>EvidenceAudit: ok
        EvidenceAudit-->>FlywheelGrading: flagged for review
    else clean
        EvidenceAudit-->>FlywheelGrading: audit passed
    end

    FlywheelGrading->>FlywheelGrading: format_audit_report()
    FlywheelGrading->>DB: write report to knowledge/flywheel/
    DB-->>FlywheelGrading: saved
    FlywheelGrading-->>Worker: grading complete
```

---

## 7. ML Experiment Pipeline

Agents propose experiments (new models, routing changes, prompt strategies). A risk
threshold gate decides whether to auto-approve or hold for human sign-off. After training
and evaluation, results are compared to the current baseline: improvements deploy
automatically; regressions trigger rollback.

```mermaid
sequenceDiagram
    participant Agent
    participant ExperimentFramework
    participant DB
    participant TrainFn
    participant EvalFn
    participant Deployer

    Agent->>ExperimentFramework: fw.propose(agent, type, hypothesis, config)
    ExperimentFramework->>DB: INSERT experiment(status=PROPOSED, agent, type, hypothesis, config)
    DB-->>ExperimentFramework: exp_id

    ExperimentFramework->>ExperimentFramework: assess_risk(config)
    alt risk < auto_approve_below_risk threshold
        ExperimentFramework->>DB: UPDATE experiment SET status=APPROVED WHERE id=exp_id
        DB-->>ExperimentFramework: ok
        Note over ExperimentFramework: Auto-approved — proceed immediately
    else risk >= threshold
        ExperimentFramework-->>Agent: status=PROPOSED — awaiting approval
        Note over Agent,ExperimentFramework: Human or orchestrator reviews and approves
        Agent->>ExperimentFramework: approve(exp_id)
        ExperimentFramework->>DB: UPDATE experiment SET status=APPROVED WHERE id=exp_id
        DB-->>ExperimentFramework: ok
    end

    ExperimentFramework->>ExperimentFramework: fw.run(exp_id, train_fn, eval_fn)
    ExperimentFramework->>DB: UPDATE experiment SET status=RUNNING WHERE id=exp_id
    DB-->>ExperimentFramework: ok

    ExperimentFramework->>TrainFn: train_fn(config)
    TrainFn->>TrainFn: execute training job
    TrainFn-->>ExperimentFramework: model_artifact

    ExperimentFramework->>EvalFn: eval_fn(config, model_artifact)
    EvalFn->>EvalFn: evaluate: accuracy, latency, resource usage
    EvalFn-->>ExperimentFramework: metrics{accuracy, latency, ...}

    ExperimentFramework->>DB: UPDATE experiment SET status=COMPLETED, metrics=metrics WHERE id=exp_id
    DB-->>ExperimentFramework: ok

    ExperimentFramework->>DB: SELECT metrics FROM experiment WHERE status=DEPLOYED ORDER BY deployed_at DESC LIMIT 1
    DB-->>ExperimentFramework: baseline_metrics

    ExperimentFramework->>ExperimentFramework: compare(metrics, baseline_metrics)
    alt improvement > deploy_threshold
        ExperimentFramework->>Deployer: deploy(exp_id, model_artifact)
        Deployer->>DB: UPDATE experiment SET status=DEPLOYED WHERE id=exp_id
        DB-->>Deployer: ok
        Deployer->>Deployer: activate model/artifact in routing layer
        Deployer-->>ExperimentFramework: deployed
        ExperimentFramework->>ExperimentFramework: monitor post-deploy metrics
        Note over ExperimentFramework,Deployer: Track metrics; regression → rollback
    else regression or no improvement
        ExperimentFramework->>DB: UPDATE experiment SET status=REJECTED WHERE id=exp_id
        DB-->>ExperimentFramework: ok
        ExperimentFramework-->>Agent: experiment rejected — baseline retained
    end
```

---

## 8. RAG Pipeline (Ingest → Index → Query → Rerank)

The RAG system has two halves: an incremental indexer that hashes files and only
re-chunks changed content into the FTS5 virtual table, and a query path that supports
BM25 keyword search, optional vector/hybrid search, and optional cross-encoder reranking.

```mermaid
sequenceDiagram
    participant KnowledgeWriter
    participant RAGIndexer
    participant RAGDatabase
    participant QueryExecutor
    participant Reranker

    Note over KnowledgeWriter,RAGDatabase: === Indexing Phase ===

    KnowledgeWriter->>KnowledgeWriter: write .md files → knowledge/
    RAGIndexer->>RAGIndexer: glob **/*.md (project, fleet, knowledge, BigEd, autoresearch)
    RAGIndexer->>RAGDatabase: SELECT path, hash FROM files
    RAGDatabase-->>RAGIndexer: known_files{}

    loop for each discovered .md file
        RAGIndexer->>RAGIndexer: MD5(content) → file_hash
        alt file_hash == known_files[path]
            RAGIndexer->>RAGIndexer: skip — unchanged
        else hash changed or new file
            RAGIndexer->>RAGIndexer: chunk: split on ## ### ####, target 1500 chars, 150 char overlap
            RAGIndexer->>RAGDatabase: DELETE FROM chunks WHERE file_path=path
            RAGDatabase-->>RAGIndexer: ok
            loop for each chunk
                RAGIndexer->>RAGDatabase: INSERT INTO fts_chunks(path, heading, content) [FTS5 porter+unicode61]
                RAGDatabase-->>RAGIndexer: chunk_id
            end
            RAGIndexer->>RAGDatabase: INSERT OR REPLACE INTO files(path, hash, chunk_count, indexed_at)
            RAGDatabase-->>RAGIndexer: ok
        end
    end

    Note over QueryExecutor,Reranker: === Query Phase ===

    QueryExecutor->>QueryExecutor: rag_query skill receives {query: "..."}
    QueryExecutor->>RAGDatabase: FTS5 MATCH query → BM25 ranked top-K chunks
    RAGDatabase-->>QueryExecutor: bm25_results[{chunk, source, heading, score}]

    alt vector model available
        QueryExecutor->>QueryExecutor: embed(query) → query_vector
        QueryExecutor->>RAGDatabase: SELECT chunk_id, vector FROM chunk_vectors
        RAGDatabase-->>QueryExecutor: stored_vectors[]
        QueryExecutor->>QueryExecutor: cosine_similarity(query_vector, stored_vectors) → vector_results
        alt hybrid_search requested
            QueryExecutor->>QueryExecutor: hybrid_search(): blend BM25 + vector scores (weighted sum)
            QueryExecutor->>QueryExecutor: merged_results[]
        else BM25 + vector separate
            QueryExecutor->>QueryExecutor: merged_results = bm25_results ++ vector_results (dedup)
        end
    else no vector model
        QueryExecutor->>QueryExecutor: merged_results = bm25_results
    end

    alt reranker available
        QueryExecutor->>Reranker: rerank(query, merged_results)
        Reranker->>Reranker: cross-encoder score each (query, chunk) pair
        Reranker-->>QueryExecutor: reranked_results[{chunk, source, heading, relevance_score}]
    else no reranker
        QueryExecutor->>QueryExecutor: results = merged_results (BM25/hybrid order)
    end

    alt FTS5 locked (SQLITE_BUSY)
        QueryExecutor->>RAGDatabase: retry with jittered backoff
        Note over QueryExecutor,RAGDatabase: Up to 3 retries before returning partial results
    end

    QueryExecutor-->>KnowledgeWriter: top-K chunks [{source, heading, content, score}]
```
