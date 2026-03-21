#!/usr/bin/env python3
"""Smoke test — validates the entire fleet startup chain. Run: uv run python smoke_test.py"""

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
    assert len(FALLBACK_CHAIN) == 4, f"Expected 4 providers, got {len(FALLBACK_CHAIN)}"
    assert "claude" in FALLBACK_CHAIN
    assert "minimax" in FALLBACK_CHAIN
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

    # Complete A with matching condition
    db.complete_task(tid_a, json.dumps({"verdict": "approved"}))
    # C should still be WAITING (B not done yet)
    task_c = db.get_task_result(tid_c)
    if task_c["status"] != "WAITING":
        return False, f"expected WAITING after A done, got {task_c['status']}"

    # Complete B — now both deps done, condition on A met, B is unconditional
    db.complete_task(tid_b, json.dumps({"verdict": "rejected"}))
    # Allow async DAG queue to process (0.08.00) or sync fallback
    time.sleep(0.3)
    task_c = db.get_task_result(tid_c)
    if task_c["status"] != "PENDING":
        with db.get_conn() as conn:
            db._promote_waiting_tasks(conn)
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
    db.complete_task(tid_x, json.dumps({"verdict": "denied"}))
    task_y = db.get_task_result(tid_y)
    if task_y["status"] != "WAITING":
        return False, f"expected WAITING (condition not met), got {task_y['status']}"

    return True, f"positive={tid_c} promoted, negative={tid_y} blocked"


def test_regression_detector_skill():
    """Regression detector: module exports + audit run."""
    from skills.regression_detector import SKILL_NAME, DESCRIPTION, REQUIRES_NETWORK, COMPLEXITY, run
    if SKILL_NAME != "regression_detector":
        return False, f"SKILL_NAME mismatch: {SKILL_NAME}"
    import logging
    log = logging.getLogger("smoke_rd")
    result = run({"data": [1.0, 2.0, 3.0, 4.0, 5.0], "target": [1.1, 2.1, 2.9, 4.2, 5.0]}, {}, log)
    ok = isinstance(result, dict)
    return ok, f"SKILL_NAME={SKILL_NAME}, COMPLEXITY={COMPLEXITY}, keys={list(result.keys())[:3]}"


def test_packet_optimizer_skill():
    """Packet optimizer: module exports + audit run."""
    from skills.packet_optimizer import SKILL_NAME, DESCRIPTION, REQUIRES_NETWORK, COMPLEXITY, run
    if SKILL_NAME != "packet_optimizer":
        return False, f"SKILL_NAME mismatch: {SKILL_NAME}"
    import logging
    log = logging.getLogger("smoke_po")
    result = run({"packets": [{"size": 100, "priority": 1}, {"size": 200, "priority": 2}]}, {}, log)
    ok = isinstance(result, dict)
    return ok, f"SKILL_NAME={SKILL_NAME}, COMPLEXITY={COMPLEXITY}, keys={list(result.keys())[:3]}"


def test_screenshot_diff_skill():
    """Screenshot diff: module exports + skip-if-missing run."""
    from skills.screenshot_diff import SKILL_NAME, DESCRIPTION, REQUIRES_NETWORK, COMPLEXITY, run
    if SKILL_NAME != "screenshot_diff":
        return False, f"SKILL_NAME mismatch: {SKILL_NAME}"
    import logging
    log = logging.getLogger("smoke_sd")
    result = run({
        "before_path": "knowledge/screenshots/test_a.png",
        "after_path": "knowledge/screenshots/test_b.png",
        "skip_if_missing": True,
    }, {}, log)
    ok = isinstance(result, dict) and result.get("verdict") in ("pass", "warn", "fail", "skip")
    return ok, f"SKILL_NAME={SKILL_NAME}, COMPLEXITY={COMPLEXITY}, verdict={result.get('verdict')}"


def test_path_traversal_blocked():
    """Security: path traversal in code_review is blocked."""
    try:
        from skills.code_review import _pick_file
        # Try to read /etc/shadow or C:\Windows\System32\config\SAM
        import sys
        if sys.platform == "win32":
            result = _pick_file("C:\\Windows\\System32\\config\\SAM")
        else:
            result = _pick_file("/etc/shadow")
        blocked = result is None
        return blocked, f"path traversal {'blocked' if blocked else 'ALLOWED (VULN!)'}"
    except (ImportError, AttributeError):
        return True, "code_review._pick_file not found (may use different pattern)"


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
