# BigEd CC — Enterprise Swimlanes

Sequence diagrams covering federation, marketplace, security, and skill lifecycle workflows.

---

## 13. Federation — Cross-Fleet Task Routing

```mermaid
sequenceDiagram
    participant LS as LocalSupervisor
    participant TQ as TaskQueue
    participant FR as FederationRouter
    participant MP as RemotePeer
    participant MD as MeshDiscovery
    participant PR as PeerRegistry

    LS->>FR: Load federation config (enabled, discovery_enabled, peers[])

    alt discovery_enabled
        FR->>MD: Start UDP broadcast + mDNS listener
        MD-->>FR: Discovered peers
    end

    FR->>PR: Build peer registry (discovered + manual peers)
    FR->>MP: POST /api/federation/heartbeat (announce self)
    MP-->>FR: 200 OK

    Note over LS, TQ: Task submission begins

    TQ->>FR: New task arrives
    FR->>LS: Query local capacity (max_workers, active_agents, pending_tasks)
    LS-->>FR: queue_ratio = pending / max_workers

    FR->>PR: GET /api/federation/capacity for each peer
    PR-->>FR: Peer capacities (queue_ratios, status)

    alt queue_ratio >= overflow_threshold AND priority < local_priority_min
        FR->>PR: Select best peer (lowest queue_ratio)
        FR->>MP: POST /api/federation/task {skill, payload, from_fleet, trace_id}
        MP-->>FR: 202 Accepted
        FR->>TQ: Mark task FORWARDED in local DB

        Note over MP: Remote peer executes task

        MP->>LS: POST /api/task/{id}/result (result callback)
        LS->>TQ: Mark task DONE
    else queue_ratio below threshold OR priority is critical
        FR->>LS: Route task locally
        LS->>TQ: Assign to local agent
    end

    Note over FR, PR: Heartbeat loop (every 5 min)

    loop Every 5 minutes
        FR->>PR: Get all peer addresses
        FR->>MP: Broadcast status to all peers
        MP-->>FR: Peer status ACK
    end

    Note over FR: Decision logic — Queue below threshold → local. Priority critical → always local. Best peer available → forward. All peers down → local fallback.
```

---

## 14. Marketplace — Publish → Review → Install

```mermaid
sequenceDiagram
    participant PB as Publisher
    participant MK as Marketplace
    participant RV as Reviewer
    participant TN as Tenant
    participant PS as PackageStorage
    participant DB as DB

    PB->>PB: Create package (manifest.json + skill files)
    PB->>MK: POST /api/marketplace/publish (upload, max 25MB)

    alt Package size > 25MB
        MK-->>PB: 400 Reject — size exceeds limit
    else Size within limit
        MK->>MK: Validate manifest + compute SHA256
        MK->>PS: Store package archive
        PS-->>MK: Storage path confirmed

        alt require_review = true
            MK->>DB: INSERT marketplace_packages (status=pending)
            MK->>RV: Notify reviewers (new pending package)

            RV->>MK: GET package (manifest, code, dependencies)
            RV->>RV: Inspect code safety + dependency audit

            alt Reviewer approves
                RV->>MK: POST /api/marketplace/packages/{id}/approve
                MK->>DB: UPDATE status=published
            else Reviewer rejects
                RV->>MK: POST /api/marketplace/packages/{id}/reject {feedback}
                MK->>DB: UPDATE status=rejected
                MK-->>PB: Rejection notice + feedback
            end
        else allow_unverified_publishers = true
            MK->>DB: INSERT marketplace_packages (status=published)
            Note over MK: Auto-approved — no review required
        end
    end

    Note over TN: Tenant browsing and install

    TN->>MK: GET /api/marketplace/packages
    MK-->>TN: Package catalog (published only)
    TN->>MK: GET /api/marketplace/packages/{id}/reviews
    MK-->>TN: Reviews (1–5 stars, comments)

    TN->>MK: POST /api/marketplace/packages/{id}/install
    MK->>PS: Fetch package archive
    PS-->>MK: Package bytes
    MK->>MK: Verify SHA256 integrity

    alt SHA256 mismatch
        MK-->>TN: 400 Reject — integrity check failed
    else Integrity OK
        MK->>MK: Extract to tenants/{tenant_id}/skills/
        MK->>MK: Run post-install hook (if present)
        MK->>DB: UPDATE download_count + 1
        MK-->>TN: 200 Install success
    end

    opt Tenant rates package
        TN->>MK: POST /api/marketplace/packages/{id}/rate {rating, comment}
        MK->>DB: INSERT marketplace_reviews (rating, comment, tenant_id)
    end

    opt Tenant uninstalls package
        TN->>MK: DELETE /api/marketplace/packages/{id}/install
        MK->>MK: Remove tenants/{tenant_id}/skills/{skill_files}
        MK->>DB: UPDATE install record (uninstalled)
        MK-->>TN: 200 Uninstall success
    end

    Note over MK: Decision logic — require_review → pending. allow_unverified_publishers → auto-approve. SHA256 mismatch → reject. Size > limit → reject.
```

---

## 15. Security Pipeline — Audit → Advisory → Apply

```mermaid
sequenceDiagram
    participant SS as SecurityScanner
    participant CB as Codebase
    participant VD as VulnDB
    participant AW as AdvisoryWriter
    participant OP as Operator
    participant PA as PatchApplier

    SS->>CB: Dispatch security_suite action=audit, target=file_or_dir
    CB-->>SS: Source files

    SS->>SS: Static analysis — regex patterns + SAST
    SS->>SS: Scan for: injection, hardcoded secrets, unsafe subprocess, XSS

    SS->>SS: Classify findings by severity (INFO / LOW / MEDIUM / HIGH / CRITICAL)

    SS->>AW: _create_advisory() — build JSON advisory document
    AW->>AW: Save advisory to knowledge/security/pending/{advisory_id}.json
    AW->>AW: Save advisory to knowledge/security/pending/{advisory_id}.md
    AW->>OP: _notify_lead() — db.post_message() with summary

    Note over VD: Parallel CVE watch

    SS->>VD: Periodic check against OSV database (dependency vulns)
    VD-->>SS: CVE matches (if any)

    alt CVE found
        SS->>AW: Create advisory for CVE finding
    end

    SS->>CB: SQL review — validate queries against fleet DB schema
    alt SQL injection pattern detected
        SS->>AW: Create advisory (CRITICAL)
        AW-->>OP: Immediate block alert
    end

    OP->>OP: Review advisory in dashboard

    alt Severity HIGH or CRITICAL
        Note over OP: Human approval required

        alt Operator approves
            OP->>SS: Dispatch security_suite action=apply, advisory_id=X
            SS->>PA: Read advisory + apply fixes
            PA->>CB: Patch files
            CB-->>PA: Files updated
            PA->>AW: Move advisory: pending/ → applied/
            PA-->>OP: Apply complete

            opt Verification
                PA->>SS: Dispatch code_scan to verify fix
                SS-->>OP: Verification report
            end
        else Operator rejects / defers
            OP->>AW: Mark advisory deferred
        end
    else Severity LOW or INFO
        Note over PA: Auto-apply eligible

        PA->>CB: Apply fix automatically
        PA->>AW: Move advisory: pending/ → applied/
        PA-->>OP: Auto-apply notice
    end

    Note over SS, PA: Decision logic — Severity HIGH+ → require human approval. Auto-apply → only LOW/INFO. CVE found → create advisory. SQL injection pattern → block.
```

---

## 16. Skill Lifecycle — Draft → Test → Promote → Deploy

```mermaid
sequenceDiagram
    participant DV as Developer
    participant DR as Drafter
    participant TR as TestRunner
    participant CR as CodeReviewer
    participant PR as Promoter
    participant DP as Deployer
    participant RG as Registry

    DV->>DR: Trigger skill_lifecycle_suite action=draft (or evolution process)
    DR->>DR: Generate skill file in knowledge/code_drafts/
    DR->>DR: Filename: {skill_name}_draft_{date}_{agent}.py
    DR->>DR: Enforce contract: SKILL_NAME, DESCRIPTION, VERSION, run(payload, config) → dict
    DR-->>DV: Draft ready

    DV->>TR: Dispatch action=test, skill=X
    TR->>DR: Import draft skill
    TR->>TR: Create synthetic test cases
    TR->>TR: Execute test cases, measure success rate + error types + performance
    TR->>RG: Store results in knowledge/skill_tests/
    TR-->>DV: Test report (pass rate, errors)

    DV->>CR: Request code review
    CR->>DR: Inspect draft (safety, style, contract compliance)
    CR->>RG: Store code review report
    CR-->>DV: Review complete

    DV->>PR: Dispatch action=promote, skill=X

    Note over PR: Safety Gate Checks

    PR->>RG: Check: test report exists AND pass_rate >= threshold?
    PR->>RG: Check: code review report exists?
    PR->>RG: Check: no naming collision with fleet/skills/?

    alt Any gate fails AND force=true not set
        PR-->>DV: Promotion blocked — gate failure details
    else All gates pass OR force=true override
        alt force=true override used
            PR->>RG: Log force override to audit trail
        end

        PR->>DR: Read draft from knowledge/code_drafts/
        PR->>DP: Copy draft to fleet/skills/{skill_name}.py
        PR->>RG: Store rollback metadata (previous version snapshot)

        DP->>DP: Deploy verification — import test
        DP->>DP: Run smoke test
        DP-->>PR: Verification result

        alt Verification passes
            DP->>RG: Register skill as active
            RG-->>DV: Skill deployed successfully

            Note over DP, RG: Post-deploy monitoring

            loop Monitor IQ scores
                DP->>RG: Check IQ scores vs baseline
                alt IQ regression detected
                    RG->>DP: Trigger auto-rollback
                    DP->>RG: Restore previous version from rollback metadata
                    DP->>DV: Operator notified — rollback applied
                end
            end
        else Verification fails
            DP-->>PR: Deploy failed — verification error
            PR-->>DV: Promotion rolled back
        end
    end

    Note over PR, RG: Decision logic — Test pass rate < 80% → block promote. Gates fail → require force=true override. IQ regression → auto-rollback. Force override → log to audit.
```
