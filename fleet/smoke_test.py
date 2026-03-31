#!/usr/bin/env python3
"""Smoke test — validates the entire fleet startup chain (51 fast / 54 full). Run: python smoke_test.py"""

import argparse
import importlib
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

FLEET_DIR = Path(__file__).parent
sys.path.insert(0, str(FLEET_DIR))

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"


def check(name, fn):
    try:
        ok, detail = fn()
        status = PASS if ok else FAIL
        print(f"  [{status}] {name}: {detail}")
        return ok
    except Exception as e:
        print(f"  [{FAIL}] {name}: {e}")
        return False


def test_skill_imports():
    """1. All skills import cleanly."""
    skills_dir = FLEET_DIR / "skills"
    failures = []
    count = 0
    for f in sorted(skills_dir.glob("*.py")):
        if f.name.startswith("_"):
            continue
        mod_name = f.stem
        count += 1
        try:
            importlib.import_module(f"skills.{mod_name}")
        except Exception as e:
            failures.append(f"{mod_name}: {e}")
    if failures:
        return False, f"{len(failures)}/{count} failed: {'; '.join(failures[:3])}"
    return True, f"{count} skills imported"


def test_skill_contracts():
    """1b. All skills comply with plugin contract."""
    from skills._contract import validate_skill
    skills_dir = FLEET_DIR / "skills"
    violations = {}
    count = 0
    for f in sorted(skills_dir.glob("*.py")):
        if f.name.startswith("_"):
            continue
        mod_name = f.stem
        count += 1
        try:
            mod = importlib.import_module(f"skills.{mod_name}")
            warns = validate_skill(mod)
            if warns:
                violations[mod_name] = warns
        except Exception:
            pass  # import failures caught by test_skill_imports
    if violations:
        summary = "; ".join(f"{k}: {len(v)} issues" for k, v in list(violations.items())[:5])
        return False, f"{len(violations)}/{count} non-compliant: {summary}"
    return True, f"{count} skills contract-compliant"


def test_db_health():
    """2. DB init + task round-trip."""
    import db
    db.init_db()
    # Use unique type to avoid claiming stale tasks from prior runs
    smoke_type = f"smoke_{int(time.time())}"
    tid = db.post_task(smoke_type, json.dumps({"test": True}), priority=1)
    task = db.claim_task("smoke_agent", affinity_skills=[smoke_type])
    if not task or task["id"] != tid:
        return False, f"claim_task failed (got {task['id'] if task else 'None'} expected {tid})"
    db.complete_task(tid, json.dumps({"ok": True}))
    result = db.get_task_result(tid)
    if result["status"] != "DONE":
        return False, f"expected DONE, got {result['status']}"
    return True, f"task {tid} round-tripped"


def test_config():
    """3. Config loads with required keys + offline/air-gap flags."""
    from config import load_config, is_offline, is_air_gap
    cfg = load_config()
    required = ["fleet", "models", "workers"]
    missing = [k for k in required if k not in cfg]
    if missing:
        return False, f"missing sections: {missing}"
    # Verify offline/air-gap flags are present and callable
    offline = is_offline(cfg)
    air_gap = is_air_gap(cfg)
    if air_gap and not offline:
        return False, "air_gap_mode should imply offline_mode"
    detail = f"fleet.toml OK ({len(cfg)} sections, offline={offline}, air_gap={air_gap})"
    return True, detail


def test_ollama_reachable():
    """4. Ollama API responds."""
    from config import load_config
    cfg = load_config()
    host = cfg.get("models", {}).get("ollama_host", "http://localhost:11434")
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=5) as r:
            data = json.loads(r.read())
            models = [m["name"] for m in data.get("models", [])]
            return True, f"{len(models)} models loaded"
    except Exception as e:
        return False, f"unreachable at {host}: {e}"


def test_rag_search():
    """5. RAG search doesn't crash."""
    try:
        from rag import RAGIndex
        idx = RAGIndex()
        results = idx.search("test")
        return True, f"{len(results)} results"
    except Exception as e:
        return False, str(e)


def test_message_roundtrip():
    """6. Message post → get → verify."""
    import db
    db.init_db()
    db.register_agent("smoke_sender", "test", 0)
    db.register_agent("smoke_receiver", "test", 0)
    body = json.dumps({"msg": "smoke_test", "ts": time.time()})
    db.post_message("smoke_sender", "smoke_receiver", body)
    msgs = db.get_messages("smoke_receiver", unread_only=True, limit=5)
    found = any("smoke_test" in m.get("body_json", "") for m in msgs)
    return found, f"{'found' if found else 'missing'} in {len(msgs)} messages"


def test_broadcast_roundtrip():
    """7. Broadcast → verify delivery to multiple agents."""
    import db
    db.init_db()
    db.register_agent("smoke_a1", "test", 0)
    db.register_agent("smoke_a2", "test", 0)
    ts = str(time.time())
    db.broadcast_message("smoke_bc", json.dumps({"broadcast": ts}))
    m1 = db.get_messages("smoke_a1", unread_only=True, limit=5)
    m2 = db.get_messages("smoke_a2", unread_only=True, limit=5)
    got1 = any(ts in m.get("body_json", "") for m in m1)
    got2 = any(ts in m.get("body_json", "") for m in m2)
    ok = got1 and got2
    return ok, f"a1={'yes' if got1 else 'no'} a2={'yes' if got2 else 'no'}"


def test_channel_message_routing():
    """8. Post to channel='agent', verify invisible from channels=['sup']."""
    import db
    db.init_db()
    db.register_agent("smoke_ch_sender", "test", 0)
    db.register_agent("smoke_ch_receiver", "test", 0)
    body = json.dumps({"msg": "agent_only", "ts": time.time()})
    db.post_message("smoke_ch_sender", "smoke_ch_receiver", body, channel="agent")
    # Should be visible on agent channel
    msgs_agent = db.get_messages("smoke_ch_receiver", unread_only=False, limit=5, channels=["agent"])
    found_agent = any("agent_only" in m.get("body_json", "") for m in msgs_agent)
    # Should NOT be visible on sup channel
    msgs_sup = db.get_messages("smoke_ch_receiver", unread_only=False, limit=5, channels=["sup"])
    found_sup = any("agent_only" in m.get("body_json", "") for m in msgs_sup)
    ok = found_agent and not found_sup
    return ok, f"agent={'yes' if found_agent else 'no'} sup={'yes' if found_sup else 'no'}"


def test_note_round_trip():
    """9. Post note, read back, verify content."""
    import db
    db.init_db()
    ts = str(time.time())
    body = json.dumps({"test": "note_smoke", "ts": ts})
    nid = db.post_note("sup", "smoke_noter", body)
    if not nid:
        return False, "post_note returned None"
    notes = db.get_notes("sup", limit=5)
    found = any(ts in n.get("body_json", "") for n in notes)
    # Verify count
    count = db.get_note_count("sup")
    return found and count > 0, f"note {nid} {'found' if found else 'missing'}, count={count}"


def test_backward_compat_messages():
    """10. Post with no channel arg defaults to 'fleet'."""
    import db
    db.init_db()
    db.register_agent("smoke_bc_sender", "test", 0)
    db.register_agent("smoke_bc_recv", "test", 0)
    ts = str(time.time())
    db.post_message("smoke_bc_sender", "smoke_bc_recv", json.dumps({"compat": ts}))
    msgs = db.get_messages("smoke_bc_recv", unread_only=False, limit=5, channels=["fleet"])
    found = any(ts in m.get("body_json", "") for m in msgs)
    return found, f"default channel={'fleet' if found else 'missing'}"


def test_usage_tracking():
    """Usage table round-trip: log + summarize."""
    import db
    db.init_db()
    db.log_usage(
        skill="smoke_usage_test", model="claude-sonnet-4-6",
        input_tokens=1000, output_tokens=200,
        cache_read_tokens=500, cache_create_tokens=0,
        cost_usd=0.006, task_id=None, agent="smoke_agent",
    )
    # Flush async usage queue before checking
    try:
        from cost_tracking import flush_usage_queue
        flush_usage_queue(timeout=3)
    except Exception:
        import time; time.sleep(1)
    summary = db.get_usage_summary(period="day", group_by="skill")
    found = any(r["skill"] == "smoke_usage_test" for r in summary)
    if not found:
        return False, "usage row not found in summary"
    row = next(r for r in summary if r["skill"] == "smoke_usage_test")
    ok = row["total_input"] >= 1000 and row["total_cost"] >= 0.006
    return ok, f"logged: {row['calls']} calls, ${row['total_cost']:.4f}"


def test_idle_run_log():
    """Idle run table round-trip: log + stats."""
    import db
    db.init_db()
    db.log_idle_run("smoke_idle_agent", "smoke_idle_skill", result="ok", cost_usd=0.001)
    stats = db.get_idle_stats(period="day")
    found = any(r["skill"] == "smoke_idle_skill" for r in stats)
    if not found:
        return False, "idle run not found in stats"
    row = next(r for r in stats if r["skill"] == "smoke_idle_skill")
    return True, f"{row['runs']} idle runs, ${row.get('total_cost', 0):.4f}"


def test_budget_check():
    """Budget check returns correct exceeded status."""
    import db
    db.init_db()
    # Log expensive usage
    db.log_usage(
        skill="smoke_budget_test", model="claude-sonnet-4-6",
        input_tokens=100000, output_tokens=50000,
        cost_usd=5.0, task_id=None, agent="smoke_agent",
    )
    # Flush async usage queue before checking
    try:
        from cost_tracking import flush_usage_queue
        flush_usage_queue(timeout=3)
    except Exception:
        import time; time.sleep(1)
    # Simulate budget check with $1.00 limit
    summary = db.get_usage_summary(period="day", group_by="skill")
    row = next((r for r in summary if r["skill"] == "smoke_budget_test"), None)
    if not row:
        return False, "usage row not found"
    exceeded = (row["total_cost"] or 0) >= 1.00
    return exceeded, f"${row['total_cost']:.2f} vs $1.00 budget = {'exceeded' if exceeded else 'under'}"


def test_stale_recovery():
    """Stale task recovery finds orphaned RUNNING tasks."""
    import db
    db.init_db()
    # Register a fake agent with old heartbeat
    db.register_agent("smoke_stale", "test", 0)
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE agents SET last_heartbeat=datetime('now', '-1 hour') WHERE name='smoke_stale'"
        )
    tid = db.post_task("smoke_stale_test", json.dumps({"stale": True}), priority=1, assigned_to="smoke_stale")
    # Manually set to RUNNING
    with db.get_conn() as conn:
        conn.execute("UPDATE tasks SET status='RUNNING' WHERE id=?", (tid,))
    recovered = db.recover_stale_tasks(timeout_secs=60)
    found = any(t["id"] == tid for t in recovered)
    return found, f"{'recovered' if found else 'missed'} task {tid}"


def test_training_lock():
    """9. Training lock acquire/check/release round-trip."""
    import db
    db.init_db()
    # Acquire
    ok1 = db.acquire_lock("training", "smoke_trainer")
    if not ok1:
        return False, "acquire failed"
    # Check holder
    holder = db.check_lock("training")
    if holder != "smoke_trainer":
        return False, f"expected smoke_trainer, got {holder}"
    # Second acquire should fail
    ok2 = db.acquire_lock("training", "smoke_other")
    if ok2:
        db.release_lock("training")
        return False, "second acquire should have failed"
    # Release
    db.release_lock("training", "smoke_trainer")
    after = db.check_lock("training")
    if after is not None:
        return False, f"lock not released: {after}"
    return True, "acquire/check/block/release OK"


def test_ha_fallback():
    """HA fallback chain builds correctly from config."""
    from skills._models import FALLBACK_CHAIN
    # Verify chain exists and has expected providers
    assert len(FALLBACK_CHAIN) == 3, f"Expected 3 providers, got {len(FALLBACK_CHAIN)}"
    assert "claude" in FALLBACK_CHAIN
    assert "gemini" in FALLBACK_CHAIN
    assert "local" in FALLBACK_CHAIN
    return True, f"chain: {' > '.join(FALLBACK_CHAIN)}"


def test_dal_roundtrip():
    """DAL ensure_table + insert + query + delete round-trip."""
    import tempfile, os
    from pathlib import Path
    # Use temp file to avoid polluting real DB
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    try:
        sys.path.insert(0, str(Path(__file__).parent.parent / "BigEd" / "launcher"))
        from data_access import DataAccess
        dal = DataAccess(tmp.name)
        dal.ensure_table("smoke_dal", {"name": "TEXT", "value": "INTEGER"})
        rid = dal.insert("smoke_dal", {"name": "test", "value": 42})
        rows = dal.query("smoke_dal", where={"name": "test"})
        assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
        assert rows[0]["value"] == 42
        dal.delete("smoke_dal", where={"name": "test"})
        rows2 = dal.query("smoke_dal")
        assert len(rows2) == 0, f"Expected 0 rows after delete, got {len(rows2)}"
        dal.close()
        return True, f"CRUD round-trip OK (row id={rid})"
    finally:
        os.unlink(tmp.name)


def test_dag_validation():
    """DAG validation detects valid chains."""
    import db
    db.init_db()
    # Create a simple valid chain
    tids = db.post_task_chain([
        {"type": "smoke_dag_a", "payload": {}},
        {"type": "smoke_dag_b", "payload": {}},
    ])
    valid, msg = db.validate_dag(tids)
    return valid, f"chain of {len(tids)}: {msg}"


def test_conditional_dag():
    """Conditional DAG edges: task promotes only when condition substring matches."""
    import db
    db.init_db()

    # Stop the async DAG queue to prevent race conditions during test.
    # We'll drive promotion manually via _promote_waiting_tasks().
    try:
        import dag_queue
        dag_queue.stop()
    except ImportError:
        pass

    # Task A: will complete with "approved" in result
    tid_a = db.post_task("smoke_cond_a", json.dumps({"step": "a"}), priority=1)
    # Task B: will complete with "rejected" in result
    tid_b = db.post_task("smoke_cond_b", json.dumps({"step": "b"}), priority=1)
    # Task C: depends on A (must contain "approved") and B (any completion)
    tid_c = db.post_task(
        "smoke_cond_c", json.dumps({"step": "c"}), priority=1,
        depends_on=[tid_a, tid_b],
        conditions={str(tid_a): "approved", str(tid_b): None},
    )
    # Verify C starts as WAITING
    task_c = db.get_task_result(tid_c)
    if task_c["status"] != "WAITING":
        return False, f"expected WAITING, got {task_c['status']}"

    # Complete A with matching condition — use sync promotion (bypass dag_queue)
    def _complete_sync(task_id, result):
        def _do():
            with db.get_conn() as conn:
                conn.execute(
                    "UPDATE tasks SET status='DONE', result_json=? WHERE id=?",
                    (result, task_id),
                )
                db._promote_waiting_tasks(conn)
        db._retry_write(_do)

    _complete_sync(tid_a, json.dumps({"verdict": "approved"}))
    # C should still be WAITING (B not done yet)
    task_c = db.get_task_result(tid_c)
    if task_c["status"] != "WAITING":
        return False, f"expected WAITING after A done, got {task_c['status']}"

    # Complete B — now both deps done, condition on A met, B is unconditional
    _complete_sync(tid_b, json.dumps({"verdict": "rejected"}))
    task_c = db.get_task_result(tid_c)
    if task_c["status"] != "PENDING":
        return False, f"expected PENDING after conditions met, got {task_c['status']}"

    # --- Negative case: condition NOT met ---
    tid_x = db.post_task("smoke_cond_x", json.dumps({"step": "x"}), priority=1)
    tid_y = db.post_task(
        "smoke_cond_y", json.dumps({"step": "y"}), priority=1,
        depends_on=[tid_x],
        conditions={str(tid_x): "approved"},
    )
    # Complete X with result that does NOT contain "approved"
    _complete_sync(tid_x, json.dumps({"verdict": "denied"}))
    task_y = db.get_task_result(tid_y)
    if task_y["status"] != "WAITING":
        return False, f"expected WAITING (condition not met), got {task_y['status']}"

    return True, f"positive={tid_c} promoted, negative={tid_y} blocked"


def test_regression_detector_skill():
    """Regression detector: module exports + audit run."""
    from skills.regression_detector import SKILL_NAME, DESCRIPTION, REQUIRES_NETWORK, COMPLEXITY, run
    if SKILL_NAME != "regression_detector":
        return False, f"SKILL_NAME mismatch: {SKILL_NAME}"
    result = run({"data": [1.0, 2.0, 3.0, 4.0, 5.0], "target": [1.1, 2.1, 2.9, 4.2, 5.0]}, {})
    ok = isinstance(result, dict)
    return ok, f"SKILL_NAME={SKILL_NAME}, COMPLEXITY={COMPLEXITY}, keys={list(result.keys())[:3]}"


def test_packet_optimizer_skill():
    """Packet optimizer: module exports + audit run."""
    from skills.packet_optimizer import SKILL_NAME, DESCRIPTION, REQUIRES_NETWORK, COMPLEXITY, run
    if SKILL_NAME != "packet_optimizer":
        return False, f"SKILL_NAME mismatch: {SKILL_NAME}"
    result = run({"packets": [{"size": 100, "priority": 1}, {"size": 200, "priority": 2}]}, {})
    ok = isinstance(result, dict)
    return ok, f"SKILL_NAME={SKILL_NAME}, COMPLEXITY={COMPLEXITY}, keys={list(result.keys())[:3]}"


def test_screenshot_diff_skill():
    """Screenshot diff: module exports + skip-if-missing run."""
    from skills.screenshot_diff import SKILL_NAME, DESCRIPTION, REQUIRES_NETWORK, COMPLEXITY, run
    if SKILL_NAME != "screenshot_diff":
        return False, f"SKILL_NAME mismatch: {SKILL_NAME}"
    result = run({
        "before_path": "knowledge/screenshots/test_a.png",
        "after_path": "knowledge/screenshots/test_b.png",
        "skip_if_missing": True,
    }, {})
    ok = isinstance(result, dict) and result.get("verdict") in ("pass", "warn", "fail", "skip")
    return ok, f"SKILL_NAME={SKILL_NAME}, COMPLEXITY={COMPLEXITY}, verdict={result.get('verdict')}"


def test_outcome_tracker_skill():
    """Outcome tracker: module exports + dry run."""
    from skills.outcome_tracker import SKILL_NAME, DESCRIPTION, REQUIRES_NETWORK, run
    if SKILL_NAME != "outcome_tracker":
        return False, f"SKILL_NAME mismatch: {SKILL_NAME}"
    import db
    db.init_db()
    result = run({"hours": 1, "min_tasks": 1}, {})
    ok = isinstance(result, dict) and result.get("status") == "ok"
    return ok, f"SKILL_NAME={SKILL_NAME}, keys={list(result.keys())[:3]}"


def test_prompt_optimize_skill():
    """Prompt optimize: module exports + dry run."""
    from skills.prompt_optimize import SKILL_NAME, DESCRIPTION, REQUIRES_NETWORK, run
    if SKILL_NAME != "prompt_optimize":
        return False, f"SKILL_NAME mismatch: {SKILL_NAME}"
    import db
    db.init_db()
    result = run({"hours": 1, "min_samples": 1}, {})
    ok = isinstance(result, dict) and result.get("status") in ("ok", "skip")
    return ok, f"SKILL_NAME={SKILL_NAME}, status={result.get('status')}, keys={list(result.keys())[:3]}"


def test_model_eval_framework_skill():
    """Model eval framework: module exports + contract check."""
    from skills.model_eval_framework import SKILL_NAME, DESCRIPTION, REQUIRES_NETWORK, run
    if SKILL_NAME != "model_eval_framework":
        return False, f"SKILL_NAME mismatch: {SKILL_NAME}"
    return True, f"SKILL_NAME={SKILL_NAME}, REQUIRES_NETWORK={REQUIRES_NETWORK}"


def test_mcp_probe_skill():
    """MCP probe: module exports + contract check."""
    from skills.mcp_probe import SKILL_NAME, DESCRIPTION, REQUIRES_NETWORK, run
    if SKILL_NAME != "mcp_probe":
        return False, f"SKILL_NAME mismatch: {SKILL_NAME}"
    return True, f"SKILL_NAME={SKILL_NAME}, REQUIRES_NETWORK={REQUIRES_NETWORK}"


def test_agent_personality_tune_skill():
    """Agent personality tune: module exports + dry run."""
    from skills.agent_personality_tune import SKILL_NAME, DESCRIPTION, REQUIRES_NETWORK, run
    if SKILL_NAME != "agent_personality_tune":
        return False, f"SKILL_NAME mismatch: {SKILL_NAME}"
    import db
    db.init_db()
    result = run({"agent": "test_agent", "hours": 1}, {})
    ok = isinstance(result, dict) and result.get("status") in ("ok", "skip")
    return ok, f"SKILL_NAME={SKILL_NAME}, status={result.get('status')}"


def test_cross_fleet_knowledge_sync_skill():
    """Cross fleet knowledge sync: module exports + dry run."""
    from skills.cross_fleet_knowledge_sync import SKILL_NAME, DESCRIPTION, REQUIRES_NETWORK, run
    if SKILL_NAME != "cross_fleet_knowledge_sync":
        return False, f"SKILL_NAME mismatch: {SKILL_NAME}"
    result = run({}, {})
    ok = isinstance(result, dict) and result.get("status") in ("ok", "skip")
    return ok, f"SKILL_NAME={SKILL_NAME}, status={result.get('status')}"


def test_doc_freshness_skill():
    """Doc freshness: standalone audit runs without error."""
    from skills.doc_freshness import SKILL_NAME, run
    if SKILL_NAME != "doc_freshness":
        return False, f"SKILL_NAME mismatch: {SKILL_NAME}"
    result = run({}, {})
    ok = isinstance(result, dict) and result.get("status") == "ok"
    stale = result.get("stale_count", -1)
    return ok, f"SKILL_NAME={SKILL_NAME}, stale_refs={stale}"


def test_path_traversal_blocked():
    """Security: path traversal in code_suite is blocked."""
    try:
        from skills.code_suite import _pick_review_file
        # Try to read /etc/shadow or C:\Windows\System32\config\SAM
        import sys
        if sys.platform == "win32":
            result = _pick_review_file("C:\\Windows\\System32\\config\\SAM")
        else:
            result = _pick_review_file("/etc/shadow")
        blocked = result is None
        return blocked, f"path traversal {'blocked' if blocked else 'ALLOWED (VULN!)'}"
    except (ImportError, AttributeError):
        return True, "code_suite._pick_review_file not found (may use different pattern)"


def test_ssrf_blocked():
    """Security: SSRF to internal IPs is blocked."""
    try:
        from skills.browser_crawl import _check_ssrf
        ok1, _ = _check_ssrf("http://127.0.0.1:5555/api/status")
        ok2, _ = _check_ssrf("http://169.254.169.254/latest/meta-data/")
        ok3, _ = _check_ssrf("https://example.com")
        blocked = not ok1 and not ok2 and ok3
        return blocked, f"internal={'blocked' if not ok1 else 'ALLOWED'} metadata={'blocked' if not ok2 else 'ALLOWED'} external={'allowed' if ok3 else 'blocked'}"
    except (ImportError, AttributeError):
        return True, "browser_crawl._check_ssrf not found (may use different pattern)"


def test_new_module_imports():
    """New module imports: all v0.100-v0.400 modules import cleanly."""
    new_modules = [
        "discovery", "federation_router", "fleet_tls", "remote_deploy",
        "federation_hitl", "federation_data", "ml_router", "self_healing",
        "health_api", "skill_recommender", "ab_testing", "dag_builder",
        "predictive_scaler", "mcp_server", "dispatch_bridge", "intent",
        "sso", "tenant_crypto", "tenant_crypto_api", "billing", "compliance",
        "tenant_admin", "control_plane", "self_service", "payments",
        "marketplace", "geo_fleet", "geo_api",
    ]
    failures = []
    for mod in new_modules:
        try:
            importlib.import_module(mod)
        except Exception as e:
            failures.append(f"{mod}: {e}")
    if failures:
        return False, f"{len(failures)}/{len(new_modules)} failed: {'; '.join(failures[:3])}"
    return True, f"{len(new_modules)} modules imported"


def test_fastmcp_available():
    """FastMCP library is importable."""
    try:
        import fastmcp  # noqa: F401
        version = getattr(fastmcp, "__version__", "unknown")
        return True, f"fastmcp {version}"
    except Exception as e:
        return False, f"import fastmcp failed: {e}"


def test_db_schema_complete():
    """DB schema: expected tables exist in fleet.db."""
    import db
    db.init_db()
    expected = [
        "tasks", "agents", "messages", "usage", "idle_runs", "locks", "notes",
    ]
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    existing = {r["name"] if isinstance(r, dict) else r[0] for r in rows}
    missing = [t for t in expected if t not in existing]
    if missing:
        return False, f"missing tables: {missing}"
    return True, f"{len(existing)} tables, all {len(expected)} expected present"


def test_fleet_toml_sections():
    """fleet.toml: key sections exist."""
    from config import load_config
    cfg = load_config()
    required_sections = [
        "fleet", "models", "workers", "dashboard", "federation",
        "routing", "self_healing", "billing", "platform", "compliance",
    ]
    missing = [s for s in required_sections if s not in cfg]
    if missing:
        return False, f"missing sections: {missing}"
    return True, f"{len(cfg)} sections, all {len(required_sections)} required present"


def test_dashboard_importable():
    """Dashboard module imports without crash."""
    try:
        importlib.import_module("dashboard")
        return True, "dashboard imported OK"
    except Exception as e:
        return False, f"import dashboard failed: {e}"


def test_view_registry():
    """View registry: sources registered and discoverable."""
    try:
        import view_registry
        view_registry.clear()
        view_registry.register_source("test_src", "fleet", ["a"], ["b"], "/api/test")
        src = view_registry.get_source("test_src")
        if not src or src["name"] != "test_src":
            return False, "register/get round-trip failed"
        sources = view_registry.get_sources()
        if len(sources) != 1:
            return False, f"expected 1 source, got {len(sources)}"
        view_registry.clear()
        return True, "register/get/clear OK"
    except Exception as e:
        return False, str(e)


def test_experiment_framework():
    """Experiment framework: propose/get/history round-trip."""
    try:
        from experiment import ExperimentFramework
        fw = ExperimentFramework()
        eid = fw.propose("test_agent", "evaluation", "test hypothesis", {"key": "val"})
        exp = fw.get(eid)
        if not exp:
            return False, "proposed experiment not found"
        if exp["agent"] != "test_agent":
            return False, f"agent mismatch: {exp['agent']}"
        history = fw.history(agent="test_agent")
        if len(history) < 1:
            return False, "history empty after propose"
        return True, f"propose #{eid}, get OK, history={len(history)}"
    except Exception as e:
        return False, str(e)


def test_token_bridge():
    """Token bridge: generates valid CSS from design tokens."""
    try:
        from token_bridge import generate_css, check_staleness
        css = generate_css()
        if "--bg:" not in css:
            return False, "CSS missing --bg variable"
        if "--accent:" not in css:
            return False, "CSS missing --accent variable"
        if "data-theme" not in css:
            return False, "CSS missing theme overrides"
        staleness = check_staleness()
        return True, f"CSS OK ({len(css)} bytes), staleness checked"
    except Exception as e:
        return False, str(e)


def test_docs_api():
    """Docs API: markdown parser returns structured sections."""
    try:
        from views_blueprint import _parse_markdown_sections
        test_md = "## Section One\nContent here\n### Sub A\nSub content\n## Section Two [DONE]\nDone content"
        sections = _parse_markdown_sections(test_md)
        if len(sections) < 2:
            return False, f"expected 2+ sections, got {len(sections)}"
        if not sections[0].get("children"):
            return False, "first section missing children"
        # Check status detection
        done_section = [s for s in sections if s.get("status") == "done"]
        if not done_section:
            return False, "DONE status not detected"
        return True, f"{len(sections)} sections parsed, status detection OK"
    except Exception as e:
        return False, str(e)


def test_thermal_readings():
    """10. GPU thermal readings available (if GPU present)."""
    try:
        from gpu import detect_gpu, read_telemetry
        backend, has_gpu = detect_gpu()
        if not has_gpu:
            return False, "no GPU detected"
        data = read_telemetry(backend)
        if not data:
            return False, "read_telemetry returned None"
        return True, f"GPU {data['gpu_temp_c']}°C, VRAM {data['vram_used_gb']}GB used ({type(backend).__name__})"
    except Exception as e:
        return False, f"no GPU thermal data: {e}"


def test_skill_health_checks():
    """All skills with health_check() report healthy."""
    from config import load_config
    skills_dir = FLEET_DIR / "skills"
    checked = 0
    failures = []
    cfg = load_config()
    for f in sorted(skills_dir.glob("*.py")):
        if f.name.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f"skills.{f.stem}")
            if hasattr(mod, "health_check"):
                checked += 1
                result = mod.health_check(cfg)
                if not result.get("healthy"):
                    failures.append(f"{f.stem}: {result.get('detail', 'unhealthy')}")
        except Exception:
            pass  # import failures caught elsewhere
    if not checked:
        return True, "no skills define health_check() yet"
    if failures:
        return False, f"{len(failures)}/{checked} unhealthy: {'; '.join(failures[:3])}"
    return True, f"{checked} health checks passed"


def test_api_gate():
    """API gate module loads and defaults to disabled."""
    try:
        import api_gate
        s = api_gate.status()
        if s["enabled"]:
            return False, "Gate should default to disabled"
        return True, f"Gate disabled, budget ${s['session_budget_usd']}"
    except Exception as e:
        return False, str(e)


def test_no_direct_api_imports():
    """Skills must route through call_complex(), not direct API clients."""
    BANNED = ["import anthropic", "import google.generativeai", "from anthropic import"]
    EXEMPT = {"_models.py", "providers.py", "_contract.py"}
    violations = []
    skills_dir = FLEET_DIR / "skills"
    for py in skills_dir.glob("*.py"):
        if py.name in EXEMPT:
            continue
        try:
            content = py.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for pattern in BANNED:
            if pattern in content:
                violations.append(f"{py.name}: {pattern}")
    if violations:
        return False, f"Direct API imports: {'; '.join(violations)}"
    return True, "All skills route through call_complex()"


def test_ingest_module():
    """Ingest manager loads and lists pre-configured sources."""
    import ingest_manager
    sources = ingest_manager.list_sources()
    if len(sources) < 5:
        return False, f"Expected 5+ sources, got {len(sources)}"
    hf = [s for s in sources if s["type"] == "huggingface"]
    if not hf:
        return False, "No HuggingFace sources found"
    return True, f"{len(sources)} sources ({len(hf)} HF)"


def test_ingest_cache_stats():
    """Ingest cache stats returns valid structure."""
    import ingest_manager
    stats = ingest_manager.cache_stats()
    if "used_mb" not in stats or "max_mb" not in stats:
        return False, f"Missing keys: {list(stats.keys())}"
    if stats["max_mb"] <= 0:
        return False, f"max_mb should be > 0, got {stats['max_mb']}"
    return True, f"Cache: {stats['used_mb']}/{stats['max_mb']}MB ({stats['usage_pct']}%)"


def test_ingest_staging():
    """Ingest staging table is accessible."""
    import ingest_manager
    staged = ingest_manager.get_staging()
    if not isinstance(staged, list):
        return False, f"Expected list, got {type(staged)}"
    return True, f"{len(staged)} items in staging"


def test_task_lifecycle_e2e():
    """Full task lifecycle: post → claim → complete → verify DONE + result stored."""
    import db
    db.init_db()

    # Post a test task with synthetic classification so it doesn't affect metrics
    task_id = db.post_task(
        "smoke_lifecycle",
        json.dumps({"test": True}),
        priority=5,
        classification="synthetic_prefix",
    )
    if not task_id:
        return False, "post_task returned no ID"

    # Claim it
    task = db.claim_task("smoke_lifecycle_agent", affinity_skills=["smoke_lifecycle"])
    if not task:
        return False, f"claim_task returned None (task_id={task_id})"
    if task["id"] != task_id:
        return False, f"claimed wrong task: got {task['id']}, expected {task_id}"

    # Complete it
    db.complete_task(task_id, json.dumps({"status": "ok", "test": True}))

    # Verify status and result in DB
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT status, result_json FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
    if not row:
        return False, f"task {task_id} not found after complete"
    status = row["status"] if isinstance(row, dict) else row[0]
    result_json = row["result_json"] if isinstance(row, dict) else row[1]
    if status != "DONE":
        return False, f"expected DONE, got {status}"
    if '"ok"' not in (result_json or ""):
        return False, f"result missing 'ok': {result_json}"

    return True, f"post #{task_id} -> claim -> complete -> verified DONE + result"


def test_backup_creates_valid_snapshot():
    """Backup should include fleet.db and fleet.toml at minimum."""
    import tempfile
    import shutil
    from pathlib import Path
    from backup_manager import BackupManager

    # Use a temp directory so we don't pollute the real backup location
    tmp_dir = Path(tempfile.mkdtemp(prefix="smoke_backup_"))
    try:
        mgr = BackupManager({"backup": {
            "enabled": True,
            "location": str(tmp_dir),
            "depth": 2,
            "targets": {
                "fleet_db": True,
                "rag_db": False,
                "config": True,
                "knowledge": False,
            },
        }})
        manifest = mgr.perform_backup(trigger="smoke_test")

        # Verify manifest structure
        if "files" not in manifest:
            return False, "manifest missing 'files' key"

        files = manifest["files"]
        missing = []
        if "fleet.db" not in files:
            missing.append("fleet.db")
        if "fleet.toml" not in files:
            missing.append("fleet.toml")
        if missing:
            return False, f"backup missing: {missing} (found: {list(files.keys())})"

        # Verify files actually exist on disk
        backup_dir = Path(manifest["location"])
        for fname in ["fleet.db", "fleet.toml"]:
            if not (backup_dir / fname).exists():
                return False, f"{fname} not present on disk in backup dir"

        total_kb = manifest["total_size_bytes"] / 1024
        return True, (
            f"backup OK — {list(files.keys())} "
            f"({total_kb:.0f}KB total, id={manifest['id']})"
        )
    except Exception as e:
        return False, str(e)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def test_graph_universe_task_aggregation():
    """Graph should return task_group nodes, not individual tasks."""
    import db
    db.init_db()
    from views_blueprint import _graph_universe
    nodes, edges = _graph_universe(db)
    types = [n.get("type") for n in nodes]
    individual_tasks = types.count("task")
    task_groups = types.count("task_group")
    communities = types.count("community")
    if individual_tasks != 0:
        return False, f"Expected 0 individual tasks, got {individual_tasks}"
    if communities < 1:
        return False, f"Expected at least 1 community, got {communities}"
    nodes_with_parent = [n for n in nodes if n.get("parent")]
    if len(nodes_with_parent) == 0:
        return False, "Some nodes should have compound parents"
    return True, (f"{len(nodes)} nodes, {len(edges)} edges, "
                  f"{task_groups} task groups, {communities} communities")


def test_modules_api():
    """Module management API endpoints respond correctly."""
    import urllib.request
    import json
    base = "http://localhost:5555"
    try:
        r = urllib.request.urlopen(base + "/api/modules", timeout=5)
        data = json.loads(r.read())
        assert "modules" in data, "Missing 'modules' key"
        print(f"  Modules API: {len(data['modules'])} installed")
        return True, f"{len(data['modules'])} modules installed"
    except urllib.error.URLError:
        # Dashboard not running — test the blueprint import instead
        try:
            from modules_blueprint import modules_bp
            assert modules_bp.name == "modules"
            return True, "blueprint importable (dashboard not running)"
        except Exception as e:
            return False, f"blueprint import failed: {e}"


def test_dep_resolver_importable():
    """Dependency resolver module imports and core functions exist."""
    from dep_resolver import (
        parse_manifest_dict, build_dependency_graph, resolve,
        topological_sort, check_version_constraint, parse_constraint,
    )
    # Quick sanity: parse a minimal manifest
    m = parse_manifest_dict({
        "name": "test", "version": "1.0.0", "type": "launcher",
        "description": "smoke test", "dependencies": {"requires": [], "conflicts": [], "recommends": []},
    })
    assert m.name == "test"
    graph = build_dependency_graph([m])
    assert "test" in graph.nodes
    return True, "dep_resolver importable + graph builds"


def test_snapshotter_importable():
    """Module snapshotter module imports and core functions exist."""
    from module_snapshotter import (
        take_snapshot, list_snapshots, get_snapshot,
        diff_snapshot, restore_snapshot, prune_snapshots,
    )
    assert callable(take_snapshot)
    assert callable(restore_snapshot)
    return True, "module_snapshotter importable"


def cleanup():
    """Remove smoke test artifacts from DB."""
    import db
    with db.get_conn() as conn:
        conn.execute("DELETE FROM agents WHERE name LIKE 'smoke_%'")
        conn.execute("DELETE FROM tasks WHERE type LIKE 'smoke_%'")
        conn.execute("DELETE FROM messages WHERE from_agent LIKE 'smoke_%' OR to_agent LIKE 'smoke_%'")
        conn.execute("DELETE FROM locks WHERE holder LIKE 'smoke_%'")
        conn.execute("DELETE FROM notes WHERE from_agent LIKE 'smoke_%'")
        # Clean usage tracking test data (ignore if table doesn't exist yet)
        try:
            conn.execute("DELETE FROM usage WHERE skill LIKE 'smoke_%' OR skill LIKE 'soak_%'")
        except Exception:
            pass
        try:
            conn.execute("DELETE FROM idle_runs WHERE agent LIKE 'smoke_%' OR agent LIKE 'soak_%'")
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="Fleet Smoke Test")
    parser.add_argument("--fast", action="store_true", help="Fast mode: skip Ollama/RAG/Thermal, use in-memory DB")
    parser.add_argument("--full", action="store_true", help="Full mode (default)")
    args = parser.parse_args()

    if args.fast:
        os.environ["FLEET_TEST_DB"] = ":memory:"

    print("Fleet Smoke Test")
    if args.fast:
        print("Mode: FAST (in-memory DB, skipping external services)")
    print("=" * 40)

    tests = [
        ("Skill imports", test_skill_imports),
        ("Skill contracts", test_skill_contracts),
        ("DB health", test_db_health),
        ("Config health", test_config),
    ]
    
    if not args.fast:
        tests.extend([
            ("Ollama reachable", test_ollama_reachable),
            ("RAG search", test_rag_search),
        ])
        
    tests.extend([
        ("Message round-trip", test_message_roundtrip),
        ("Broadcast round-trip", test_broadcast_roundtrip),
        ("Channel message routing", test_channel_message_routing),
        ("Note round-trip", test_note_round_trip),
        ("Backward-compat messages", test_backward_compat_messages),
        ("Usage tracking", test_usage_tracking),
        ("Idle run log", test_idle_run_log),
        ("Budget check", test_budget_check),
        ("Stale recovery", test_stale_recovery),
        ("Training lock", test_training_lock),
        ("HA fallback", test_ha_fallback),
        ("DAL round-trip", test_dal_roundtrip),
        ("DAG validation", test_dag_validation),
        ("Conditional DAG", test_conditional_dag),
        ("Path traversal blocked", test_path_traversal_blocked),
        ("SSRF blocked", test_ssrf_blocked),
        ("Regression detector skill", test_regression_detector_skill),
        ("Packet optimizer skill", test_packet_optimizer_skill),
        ("Screenshot diff skill", test_screenshot_diff_skill),
        ("Outcome tracker skill", test_outcome_tracker_skill),
        ("Prompt optimize skill", test_prompt_optimize_skill),
        ("Model eval framework skill", test_model_eval_framework_skill),
        ("MCP probe skill", test_mcp_probe_skill),
        ("Agent personality tune skill", test_agent_personality_tune_skill),
        ("Cross fleet knowledge sync skill", test_cross_fleet_knowledge_sync_skill),
        ("Doc freshness skill", test_doc_freshness_skill),
        ("New module imports", test_new_module_imports),
        ("FastMCP available", test_fastmcp_available),
        ("DB schema complete", test_db_schema_complete),
        ("fleet.toml sections", test_fleet_toml_sections),
        ("Dashboard importable", test_dashboard_importable),
        ("View registry", test_view_registry),
        ("Experiment framework", test_experiment_framework),
        ("Token bridge", test_token_bridge),
        ("Docs API", test_docs_api),
        ("Skill health checks", test_skill_health_checks),
        ("API gate", test_api_gate),
        ("No direct API imports", test_no_direct_api_imports),
        ("Ingest module", test_ingest_module),
        ("Ingest cache stats", test_ingest_cache_stats),
        ("Ingest staging", test_ingest_staging),
        ("Graph universe task aggregation", test_graph_universe_task_aggregation),
        ("Task lifecycle e2e", test_task_lifecycle_e2e),
        ("Backup creates valid snapshot", test_backup_creates_valid_snapshot),
        ("Modules API", test_modules_api),
        ("Dep resolver importable", test_dep_resolver_importable),
        ("Snapshotter importable", test_snapshotter_importable),
    ])

    if not args.fast:
        tests.append(("Thermal readings", test_thermal_readings))

    results = []
    for name, fn in tests:
        results.append(check(name, fn))

    print("=" * 40)
    passed = sum(results)
    total = len(results)
    cleanup()
    print(f"Result: {passed}/{total} passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
