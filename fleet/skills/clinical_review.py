"""
Clinical Review Pipeline — 5-agent DITL (Doctor in the Loop) review system.

Implements a structured clinical document review with mandatory HITL sign-off:
  1. Intake Agent     — extract claims, detect PHI, de-identify
  2. Analysis Agent   — evaluate clinical accuracy against guidelines
  3. Recommendation   — structured approve/modify/reject decision
  4. Peer Review      — independent second opinion, flag disagreements
  5. Final Sign-off   — operator confirms with hex code (HITL gate)

Actions:
  review   — run full pipeline on a document/recommendation
  status   — check pipeline status by pipeline_id
  history  — list recent pipeline runs

All PHI is de-identified before stage outputs are stored.
Each stage logs to the phi_audit table for HIPAA compliance tracking.
"""
import json
import secrets
from datetime import datetime
from pathlib import Path

SKILL_NAME = "clinical_review"
DESCRIPTION = "5-agent clinical review pipeline with HITL sign-off"
COMPLEXITY = "complex"
REQUIRES_NETWORK = False

FLEET_DIR = Path(__file__).parent.parent
DITL_DIR = FLEET_DIR / "knowledge" / "ditl"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_pipeline_id():
    """Generate a unique pipeline identifier: cr_YYYYMMDD_<hex6>."""
    date_str = datetime.now().strftime("%Y%m%d")
    hex_part = secrets.token_hex(3)  # 6 hex chars
    return f"cr_{date_str}_{hex_part}"


def _log_phi_audit(action, data_scope, phi_detected=False, deidentified=False, model_used="local"):
    """Insert a row into the phi_audit table and return the inserted row id."""
    import db as _db
    try:
        with _db.get_conn() as conn:
            cursor = conn.execute(
                "INSERT INTO phi_audit (user_id, action, data_scope, model_used, phi_detected, deidentified) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("clinical_review", action, data_scope, model_used, int(phi_detected), int(deidentified)),
            )
            return cursor.lastrowid
    except Exception:
        return None


def _deidentify_text(text):
    """Run PHI de-identification on text. Returns (cleaned_text, phi_was_found)."""
    from phi_deidentify import deidentify, contains_phi
    had_phi = contains_phi(text)
    if had_phi:
        result = deidentify(text, log_stripped=False)
        return result["text"], True
    return text, False


def _deidentify_dict(d):
    """Recursively de-identify all string values in a dict. Returns (cleaned, phi_found)."""
    phi_found = False
    cleaned = {}
    for k, v in d.items():
        if isinstance(v, str):
            clean, found = _deidentify_text(v)
            cleaned[k] = clean
            phi_found = phi_found or found
        elif isinstance(v, dict):
            clean, found = _deidentify_dict(v)
            cleaned[k] = clean
            phi_found = phi_found or found
        elif isinstance(v, list):
            clean_list = []
            for item in v:
                if isinstance(item, str):
                    clean, found = _deidentify_text(item)
                    clean_list.append(clean)
                    phi_found = phi_found or found
                elif isinstance(item, dict):
                    clean, found = _deidentify_dict(item)
                    clean_list.append(clean)
                    phi_found = phi_found or found
                else:
                    clean_list.append(item)
            cleaned[k] = clean_list
        else:
            cleaned[k] = v
    return cleaned, phi_found


def _call_local_model(system_prompt, user_prompt, config):
    """Call the local model via the shared routing layer."""
    from skills._models import call_complex
    return call_complex(
        system_prompt, user_prompt, config,
        max_tokens=2048,
        skill_name=SKILL_NAME,
    )


def _save_pipeline_record(pipeline_id, record):
    """Persist the pipeline record as JSON in knowledge/ditl/."""
    DITL_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DITL_DIR / f"{pipeline_id}.json"
    out_path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    return str(out_path)


def _load_pipeline_record(pipeline_id):
    """Load a pipeline record from disk. Returns dict or None."""
    path = DITL_DIR / f"{pipeline_id}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# Stage 1: Intake Agent
# ---------------------------------------------------------------------------

def _stage_intake(document_text, config):
    """Extract key claims, identify PHI, de-identify the document."""
    # De-identify the raw document first
    clean_text, phi_detected = _deidentify_text(document_text)

    # Ask local model to extract claims from the de-identified text
    system = (
        "You are a clinical intake analyst. Your job is to extract key clinical "
        "claims from a document. Respond ONLY with valid JSON — no markdown, "
        "no explanation, no code fences."
    )
    user = f"""Extract the key clinical claims from this document.

DOCUMENT:
{clean_text[:4000]}

Respond with a JSON object containing:
- "claims": list of strings, each a factual clinical claim from the document
- "document_type": string describing what kind of document this is (e.g. "treatment recommendation", "lab report", "clinical note")
- "urgency": "routine" | "urgent" | "critical"
- "summary": one-sentence summary of the document"""

    raw_response = _call_local_model(system, user, config)
    try:
        result = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError):
        result = {
            "claims": [raw_response[:500] if raw_response else "Unable to parse claims"],
            "document_type": "unknown",
            "urgency": "routine",
            "summary": raw_response[:200] if raw_response else "Parsing failed",
        }

    # De-identify the model output as well
    result, output_phi = _deidentify_dict(result)
    phi_detected = phi_detected or output_phi

    result["clean_document"] = clean_text
    return result, phi_detected


# ---------------------------------------------------------------------------
# Stage 2: Analysis Agent
# ---------------------------------------------------------------------------

def _stage_analysis(intake_result, config):
    """Evaluate clinical accuracy and check against known guidelines."""
    claims = intake_result.get("claims", [])
    doc_type = intake_result.get("document_type", "unknown")
    summary = intake_result.get("summary", "")

    system = (
        "You are a clinical analysis agent. Evaluate the accuracy of clinical claims "
        "against standard medical guidelines and evidence-based practice. "
        "Respond ONLY with valid JSON — no markdown, no explanation, no code fences."
    )
    claims_text = "\n".join(f"- {c}" for c in claims[:20])
    user = f"""Analyze these clinical claims for accuracy and guideline compliance.

DOCUMENT TYPE: {doc_type}
SUMMARY: {summary}

CLAIMS:
{claims_text}

Respond with a JSON object containing:
- "accuracy_score": float 0.0-1.0 (overall clinical accuracy estimate)
- "guideline_alignment": "aligned" | "partially_aligned" | "misaligned" | "insufficient_data"
- "concerns": list of strings — specific clinical concerns or inaccuracies found
- "strengths": list of strings — aspects that are well-supported
- "evidence_gaps": list of strings — claims that need additional evidence
- "risk_level": "low" | "medium" | "high"
"""

    raw_response = _call_local_model(system, user, config)
    try:
        result = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError):
        result = {
            "accuracy_score": 0.5,
            "guideline_alignment": "insufficient_data",
            "concerns": [raw_response[:300] if raw_response else "Unable to analyze"],
            "strengths": [],
            "evidence_gaps": ["Full analysis could not be completed"],
            "risk_level": "medium",
        }

    result, _ = _deidentify_dict(result)
    return result


# ---------------------------------------------------------------------------
# Stage 3: Recommendation Agent
# ---------------------------------------------------------------------------

def _stage_recommendation(intake_result, analysis_result, config):
    """Generate a structured recommendation: approve, modify, or reject."""
    system = (
        "You are a clinical recommendation agent. Based on intake findings and analysis, "
        "you produce a structured recommendation to approve, modify, or reject the document. "
        "Respond ONLY with valid JSON — no markdown, no explanation, no code fences."
    )

    user = f"""Generate a clinical recommendation based on the following:

DOCUMENT TYPE: {intake_result.get("document_type", "unknown")}
SUMMARY: {intake_result.get("summary", "")}
ACCURACY SCORE: {analysis_result.get("accuracy_score", "N/A")}
GUIDELINE ALIGNMENT: {analysis_result.get("guideline_alignment", "unknown")}
RISK LEVEL: {analysis_result.get("risk_level", "unknown")}

CONCERNS:
{json.dumps(analysis_result.get("concerns", []), indent=2)}

EVIDENCE GAPS:
{json.dumps(analysis_result.get("evidence_gaps", []), indent=2)}

Respond with a JSON object containing:
- "decision": "approve" | "modify" | "reject"
- "confidence": float 0.0-1.0
- "rationale": string explaining the decision
- "modifications_required": list of strings (empty if approving)
- "conditions": list of strings — conditions that must be met for approval
- "follow_up_required": boolean
"""

    raw_response = _call_local_model(system, user, config)
    try:
        result = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError):
        result = {
            "decision": "modify",
            "confidence": 0.3,
            "rationale": raw_response[:500] if raw_response else "Unable to generate recommendation",
            "modifications_required": ["Manual review required — automated recommendation failed"],
            "conditions": [],
            "follow_up_required": True,
        }

    result, _ = _deidentify_dict(result)
    return result


# ---------------------------------------------------------------------------
# Stage 4: Peer Review Agent
# ---------------------------------------------------------------------------

def _stage_peer_review(intake_result, analysis_result, recommendation_result, config):
    """Independent review of the recommendation — flag disagreements."""
    system = (
        "You are an independent clinical peer reviewer. You review recommendations "
        "made by another agent and flag any disagreements or concerns. You must be "
        "objective and thorough. "
        "Respond ONLY with valid JSON — no markdown, no explanation, no code fences."
    )

    user = f"""Review this clinical recommendation independently.

DOCUMENT SUMMARY: {intake_result.get("summary", "")}
DOCUMENT TYPE: {intake_result.get("document_type", "unknown")}

ANALYSIS FINDINGS:
- Accuracy: {analysis_result.get("accuracy_score", "N/A")}
- Alignment: {analysis_result.get("guideline_alignment", "unknown")}
- Risk: {analysis_result.get("risk_level", "unknown")}
- Concerns: {json.dumps(analysis_result.get("concerns", []))}

RECOMMENDATION:
- Decision: {recommendation_result.get("decision", "unknown")}
- Confidence: {recommendation_result.get("confidence", "N/A")}
- Rationale: {recommendation_result.get("rationale", "")}
- Required Modifications: {json.dumps(recommendation_result.get("modifications_required", []))}

Respond with a JSON object containing:
- "agrees": boolean — whether you agree with the recommendation decision
- "agreement_level": "full" | "partial" | "disagree"
- "concerns": list of strings — your specific concerns about the recommendation
- "missed_issues": list of strings — issues the recommendation agent missed
- "alternative_decision": null | "approve" | "modify" | "reject" — only if you disagree
- "alternative_rationale": string — explanation if you propose an alternative (empty string if agrees)
- "peer_confidence": float 0.0-1.0 — your confidence in your own assessment
"""

    raw_response = _call_local_model(system, user, config)
    try:
        result = json.loads(raw_response)
    except (json.JSONDecodeError, TypeError):
        result = {
            "agrees": False,
            "agreement_level": "partial",
            "concerns": [raw_response[:300] if raw_response else "Unable to complete peer review"],
            "missed_issues": [],
            "alternative_decision": None,
            "alternative_rationale": "",
            "peer_confidence": 0.3,
        }

    result, _ = _deidentify_dict(result)
    return result


# ---------------------------------------------------------------------------
# Stage 5: Final Sign-off (HITL gate)
# ---------------------------------------------------------------------------

def _stage_create_signoff(pipeline_id, stages, config):
    """Create a WAITING_HUMAN task with the confirmation hex."""
    confirmation_hex = secrets.token_hex(4)  # 8-char hex

    # Build a summary for the operator
    intake = stages[0]["result"]
    analysis = stages[1]["result"]
    recommendation = stages[2]["result"]
    peer_review = stages[3]["result"]

    agrees = peer_review.get("agrees", False)
    agreement_level = peer_review.get("agreement_level", "unknown")

    summary_lines = [
        f"Pipeline: {pipeline_id}",
        f"Document Type: {intake.get('document_type', 'unknown')}",
        f"Summary: {intake.get('summary', 'N/A')}",
        f"Urgency: {intake.get('urgency', 'routine')}",
        "",
        f"Analysis — Accuracy: {analysis.get('accuracy_score', 'N/A')}, "
        f"Risk: {analysis.get('risk_level', 'N/A')}, "
        f"Alignment: {analysis.get('guideline_alignment', 'N/A')}",
        "",
        f"Recommendation: {recommendation.get('decision', 'N/A').upper()} "
        f"(confidence: {recommendation.get('confidence', 'N/A')})",
        f"Rationale: {recommendation.get('rationale', 'N/A')}",
        "",
        f"Peer Review: {'AGREES' if agrees else 'DISAGREES'} ({agreement_level})",
    ]

    if not agrees:
        alt = peer_review.get("alternative_decision")
        if alt:
            summary_lines.append(f"  Alternative: {alt.upper()}")
        alt_rationale = peer_review.get("alternative_rationale", "")
        if alt_rationale:
            summary_lines.append(f"  Reason: {alt_rationale}")

    concerns = peer_review.get("concerns", [])
    if concerns:
        summary_lines.append(f"  Peer Concerns: {'; '.join(concerns[:5])}")

    summary_text = "\n".join(summary_lines)

    # Post a WAITING_HUMAN task so the operator can confirm
    import db as _db
    hitl_payload = {
        "type": "clinical_review_signoff",
        "pipeline_id": pipeline_id,
        "summary": summary_text,
        "recommendation_decision": recommendation.get("decision", "unknown"),
        "peer_agrees": agrees,
        "agreement_level": agreement_level,
        "confirmation_hex": confirmation_hex,
        "instruction": (
            f"To APPROVE this clinical review, reply with the confirmation code: {confirmation_hex}\n"
            f"To REJECT, reply with: REJECT\n"
            f"To request modifications, reply with: MODIFY <your instructions>"
        ),
    }
    task_id = _db.post_task(
        "clinical_review",
        json.dumps(hitl_payload),
        priority=9,  # high priority — operator attention needed
    )
    # Mark it as waiting for human
    with _db.get_conn() as conn:
        conn.execute("UPDATE tasks SET status='WAITING_HUMAN' WHERE id=?", (task_id,))

    # Also send a fleet message to the operator
    _db.post_message(
        from_agent="clinical_review",
        to_agent="operator",
        body_json=json.dumps({
            "type": "clinical_signoff_required",
            "pipeline_id": pipeline_id,
            "task_id": task_id,
            "confirmation_hex": confirmation_hex,
            "recommendation": recommendation.get("decision", "unknown"),
            "peer_agrees": agrees,
            "message": f"Clinical review {pipeline_id} requires sign-off. Code: {confirmation_hex}",
        }),
        channel="fleet",
    )

    return {
        "status": "pending_human",
        "confirmation_hex": confirmation_hex,
        "hitl_task_id": task_id,
        "summary": summary_text,
    }


def _stage_validate_signoff(pipeline_id, operator_response, expected_hex):
    """Validate the operator's hex confirmation. Returns approved/rejected status."""
    response = (operator_response or "").strip()

    if response.upper() == expected_hex.upper():
        return {"status": "approved", "operator_action": "approved"}
    elif response.upper() == "REJECT":
        return {"status": "rejected", "operator_action": "rejected"}
    elif response.upper().startswith("MODIFY"):
        instructions = response[6:].strip()
        return {
            "status": "rejected",
            "operator_action": "modify_requested",
            "modification_instructions": instructions,
        }
    else:
        return {
            "status": "rejected",
            "operator_action": "invalid_code",
            "expected": expected_hex,
            "received": response[:20],
        }


# ---------------------------------------------------------------------------
# Main pipeline orchestration
# ---------------------------------------------------------------------------

def _run_review_pipeline(payload, config, log=None):
    """Execute the full 5-stage clinical review pipeline."""
    document = payload.get("document", payload.get("text", ""))
    if not document:
        return {"error": "No document provided. Include 'document' or 'text' in payload."}

    pipeline_id = _generate_pipeline_id()
    phi_audit_ids = []
    stages = []

    if log:
        log.info(f"Clinical review pipeline {pipeline_id} — starting intake")

    # ── Stage 1: Intake ──────────────────────────────────────────────────
    intake_result, phi_detected = _stage_intake(document, config)
    audit_id = _log_phi_audit(
        action="intake_scan",
        data_scope=f"pipeline:{pipeline_id}:intake",
        phi_detected=phi_detected,
        deidentified=phi_detected,
    )
    if audit_id:
        phi_audit_ids.append(audit_id)

    stages.append({
        "stage": "intake",
        "agent": "intake",
        "result": intake_result,
        "phi_detected": phi_detected,
    })

    if log:
        log.info(f"  Stage 1 (intake) complete — PHI detected: {phi_detected}")

    # ── Stage 2: Analysis ────────────────────────────────────────────────
    analysis_result = _stage_analysis(intake_result, config)
    audit_id = _log_phi_audit(
        action="analysis",
        data_scope=f"pipeline:{pipeline_id}:analysis",
        phi_detected=False,
        deidentified=False,
    )
    if audit_id:
        phi_audit_ids.append(audit_id)

    stages.append({
        "stage": "analysis",
        "agent": "analysis",
        "result": analysis_result,
    })

    if log:
        log.info(f"  Stage 2 (analysis) complete — risk: {analysis_result.get('risk_level', '?')}")

    # ── Stage 3: Recommendation ──────────────────────────────────────────
    recommendation_result = _stage_recommendation(intake_result, analysis_result, config)
    audit_id = _log_phi_audit(
        action="recommendation",
        data_scope=f"pipeline:{pipeline_id}:recommendation",
        phi_detected=False,
        deidentified=False,
    )
    if audit_id:
        phi_audit_ids.append(audit_id)

    stages.append({
        "stage": "recommendation",
        "agent": "recommendation",
        "result": recommendation_result,
    })

    if log:
        log.info(f"  Stage 3 (recommendation) complete — decision: {recommendation_result.get('decision', '?')}")

    # ── Stage 4: Peer Review ─────────────────────────────────────────────
    peer_result = _stage_peer_review(intake_result, analysis_result, recommendation_result, config)
    audit_id = _log_phi_audit(
        action="peer_review",
        data_scope=f"pipeline:{pipeline_id}:peer_review",
        phi_detected=False,
        deidentified=False,
    )
    if audit_id:
        phi_audit_ids.append(audit_id)

    stages.append({
        "stage": "peer_review",
        "agent": "peer_review",
        "result": peer_result,
    })

    if log:
        log.info(f"  Stage 4 (peer review) complete — agrees: {peer_result.get('agrees', '?')}")

    # ── Stage 5: Create HITL sign-off gate ───────────────────────────────
    signoff_result = _stage_create_signoff(pipeline_id, stages, config)
    audit_id = _log_phi_audit(
        action="signoff_created",
        data_scope=f"pipeline:{pipeline_id}:signoff",
        phi_detected=False,
        deidentified=False,
    )
    if audit_id:
        phi_audit_ids.append(audit_id)

    stages.append({
        "stage": "sign_off",
        "status": signoff_result["status"],
        "confirmation_hex": signoff_result["confirmation_hex"],
    })

    if log:
        log.info(f"  Stage 5 (sign-off) — WAITING_HUMAN, code: {signoff_result['confirmation_hex']}")

    # ── Assemble final record ────────────────────────────────────────────
    record = {
        "pipeline_id": pipeline_id,
        "created_at": datetime.now().isoformat(),
        "stages": stages,
        "final_status": "pending_human",
        "phi_audit_ids": phi_audit_ids,
        "hitl_task_id": signoff_result.get("hitl_task_id"),
        "confirmation_hex": signoff_result["confirmation_hex"],
    }

    _save_pipeline_record(pipeline_id, record)

    if log:
        log.info(f"Clinical review pipeline {pipeline_id} complete — awaiting operator sign-off")

    return record


def _handle_signoff_response(payload, config, log=None):
    """Handle an operator's response to a sign-off request."""
    pipeline_id = payload.get("pipeline_id")
    operator_response = payload.get("_human_response", payload.get("response", ""))
    expected_hex = payload.get("confirmation_hex", "")

    if not pipeline_id:
        return {"error": "Missing pipeline_id in sign-off response"}

    record = _load_pipeline_record(pipeline_id)
    if not record:
        return {"error": f"Pipeline record not found: {pipeline_id}"}

    # Use stored hex if not in payload
    if not expected_hex:
        expected_hex = record.get("confirmation_hex", "")

    result = _stage_validate_signoff(pipeline_id, operator_response, expected_hex)

    # Log the sign-off decision to phi_audit
    _log_phi_audit(
        action=f"signoff_{result['status']}",
        data_scope=f"pipeline:{pipeline_id}:signoff_response",
        phi_detected=False,
        deidentified=False,
    )

    # Update the stored record
    record["final_status"] = result["status"]
    if len(record.get("stages", [])) >= 5:
        record["stages"][-1]["status"] = result["status"]
        record["stages"][-1]["operator_action"] = result.get("operator_action", "unknown")
    _save_pipeline_record(pipeline_id, record)

    if log:
        log.info(f"Clinical review {pipeline_id} sign-off: {result['status']}")

    return {
        "pipeline_id": pipeline_id,
        "final_status": result["status"],
        "operator_action": result.get("operator_action", "unknown"),
        "detail": result,
    }


def _get_pipeline_status(payload, config, log=None):
    """Check the status of a pipeline by ID."""
    pipeline_id = payload.get("pipeline_id")
    if not pipeline_id:
        return {"error": "Missing pipeline_id"}

    record = _load_pipeline_record(pipeline_id)
    if not record:
        return {"error": f"Pipeline not found: {pipeline_id}"}

    return {
        "pipeline_id": pipeline_id,
        "final_status": record.get("final_status", "unknown"),
        "created_at": record.get("created_at"),
        "stage_count": len(record.get("stages", [])),
        "phi_audit_ids": record.get("phi_audit_ids", []),
        "stages_summary": [
            {
                "stage": s.get("stage"),
                "agent": s.get("agent", s.get("stage")),
                "status": s.get("status", "complete"),
            }
            for s in record.get("stages", [])
        ],
    }


def _get_pipeline_history(payload, config, log=None):
    """List recent pipeline runs from the ditl directory."""
    DITL_DIR.mkdir(parents=True, exist_ok=True)
    limit = payload.get("limit", 20)

    records = []
    for path in sorted(DITL_DIR.glob("cr_*.json"), reverse=True)[:limit]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            records.append({
                "pipeline_id": data.get("pipeline_id"),
                "created_at": data.get("created_at"),
                "final_status": data.get("final_status"),
                "recommendation": (
                    data.get("stages", [{}])[2].get("result", {}).get("decision", "unknown")
                    if len(data.get("stages", [])) > 2 else "unknown"
                ),
            })
        except Exception:
            continue

    return {
        "count": len(records),
        "pipelines": records,
    }


# ---------------------------------------------------------------------------
# Skill entry point
# ---------------------------------------------------------------------------

def run(payload: dict, config: dict, log=None) -> dict:
    """Main entry point — dispatched by worker.py.

    Actions:
        review    — run the full 5-stage pipeline
        status    — check pipeline status by pipeline_id
        history   — list recent pipeline runs
        signoff   — process an operator sign-off response
    """
    action = payload.get("action", "review")

    # If this is a sign-off callback (has _human_response or is type clinical_review_signoff)
    if payload.get("_human_response") or payload.get("type") == "clinical_review_signoff":
        return _handle_signoff_response(payload, config, log)

    if action == "review":
        return _run_review_pipeline(payload, config, log)
    elif action == "status":
        return _get_pipeline_status(payload, config, log)
    elif action == "history":
        return _get_pipeline_history(payload, config, log)
    elif action == "signoff":
        return _handle_signoff_response(payload, config, log)
    else:
        return {"error": f"Unknown action: {action}", "valid_actions": ["review", "status", "history", "signoff"]}
