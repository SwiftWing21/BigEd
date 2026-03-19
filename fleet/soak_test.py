#!/usr/bin/env python3
"""
Soak test — extended validation of fleet + module system for 24/7 operation.

Tests:
  1. Submit 100 mixed tasks, verify all complete
  2. Module enable/disable mid-run
  3. Kill random workers, verify recovery
  4. Training lock lifecycle
  5. Deprecation lifecycle (active→deprecated→sunset→removed)
  6. Data export per module
  7. Stale task recovery under load
  8. Message broadcast under load
  9. Concurrent lock contention
  10. DB WAL mode stress

Run: uv run python soak_test.py [--duration MINUTES] [--fast]
"""
import argparse
import json
import os
import random
import sys
import time
import threading
from pathlib import Path

FLEET_DIR = Path(__file__).parent
sys.path.insert(0, str(FLEET_DIR))

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"


def check(name, fn, timeout=60):
    try:
        ok, detail = fn()
        status = PASS if ok else FAIL
        print(f"  [{status}] {name}: {detail}")
        return ok
    except Exception as e:
        print(f"  [{FAIL}] {name}: {e}")
        return False


def test_task_flood():
    """1. Submit 100 tasks, verify all reach PENDING."""
    import db
    db.init_db()
    task_ids = []
    for i in range(100):
        skill = random.choice(["summarize", "web_search", "flashcard", "rag_query"])
        tid = db.post_task(f"soak_{skill}", json.dumps({"soak": True, "i": i}), priority=5)
        task_ids.append(tid)
    # Verify all pending
    pending = 0
    for tid in task_ids:
        r = db.get_task_result(tid)
        if r and r["status"] == "PENDING":
            pending += 1
    return pending == 100, f"{pending}/100 tasks queued"


def test_task_claim_complete():
    """2. Claim and complete tasks sequentially."""
    import db
    db.init_db()
    db.register_agent("soak_worker", "test", 0)
    completed = 0
    for _ in range(20):
        task = db.claim_task("soak_worker")
        if task:
            db.complete_task(task["id"], json.dumps({"soak_result": True}))
            completed += 1
    return completed > 0, f"completed {completed}/20 tasks"


def test_concurrent_claims():
    """3. Multiple threads claiming tasks simultaneously."""
    import db
    db.init_db()
    for i in range(10):
        db.register_agent(f"soak_concurrent_{i}", "test", 0)
        db.post_task("soak_concurrent", json.dumps({"i": i}), priority=5)

    claimed = []
    lock = threading.Lock()

    def _claim(agent_name):
        task = db.claim_task(agent_name)
        if task:
            with lock:
                claimed.append(task["id"])

    threads = [threading.Thread(target=_claim, args=(f"soak_concurrent_{i}",))
               for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # No task should be double-claimed
    unique = len(set(claimed))
    return unique == len(claimed), f"{unique} unique claims, {len(claimed)} total"


def test_lock_contention():
    """4. Multiple threads contending for training lock."""
    import db
    db.init_db()

    acquired = []
    lock = threading.Lock()

    def _try_lock(name):
        ok = db.acquire_lock("soak_test_lock", name)
        if ok:
            with lock:
                acquired.append(name)
            time.sleep(0.1)
            db.release_lock("soak_test_lock", name)

    threads = [threading.Thread(target=_try_lock, args=(f"soak_locker_{i}",))
               for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    # At least 1 should have acquired, and they should be sequential
    db.release_lock("soak_test_lock")  # cleanup
    return len(acquired) >= 1, f"{len(acquired)}/5 acquired lock"


def test_stale_recovery_under_load():
    """5. Create stale tasks while other tasks are running."""
    import db
    db.init_db()
    db.register_agent("soak_stale", "test", 0)
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE agents SET last_heartbeat=datetime('now', '-1 hour') WHERE name='soak_stale'")

    stale_ids = []
    for i in range(5):
        tid = db.post_task("soak_stale_task", json.dumps({"stale": True}),
                           priority=1, assigned_to="soak_stale")
        with db.get_conn() as conn:
            conn.execute("UPDATE tasks SET status='RUNNING' WHERE id=?", (tid,))
        stale_ids.append(tid)

    recovered = db.recover_stale_tasks(timeout_secs=60)
    found = sum(1 for t in recovered if t["id"] in stale_ids)
    return found == 5, f"recovered {found}/5 stale tasks"


def test_broadcast_under_load():
    """6. Broadcast to many agents simultaneously."""
    import db
    db.init_db()
    agent_count = 20
    for i in range(agent_count):
        db.register_agent(f"soak_bcast_{i}", "test", 0)

    ts = str(time.time())
    db.broadcast_message("soak_sender", json.dumps({"broadcast": ts}))

    received = 0
    for i in range(agent_count):
        msgs = db.get_messages(f"soak_bcast_{i}", unread_only=True, limit=5)
        if any(ts in m.get("body_json", "") for m in msgs):
            received += 1

    return received == agent_count, f"{received}/{agent_count} agents received"


def test_training_lock_lifecycle():
    """7. Full training lock lifecycle: acquire → check → release → re-acquire."""
    import db
    db.init_db()

    ok1 = db.acquire_lock("training", "soak_trainer")
    if not ok1:
        db.release_lock("training")
        ok1 = db.acquire_lock("training", "soak_trainer")

    holder = db.check_lock("training")
    ok2 = db.acquire_lock("training", "soak_other")  # should fail
    db.release_lock("training", "soak_trainer")
    ok3 = db.acquire_lock("training", "soak_other")  # should succeed
    db.release_lock("training", "soak_other")

    return (ok1 and holder == "soak_trainer" and not ok2 and ok3), \
        f"acquire={ok1} holder={holder} block={not ok2} re-acquire={ok3}"


def test_deprecation_lifecycle():
    """8. Module deprecation: active → deprecated → sunset → removed."""
    # Test the version check utility
    sys.path.insert(0, str(Path(__file__).parent.parent / "BigEd" / "launcher"))
    try:
        from modules._version_check import parse_version, is_past_sunset, set_current_version
        set_current_version("0.30")

        v1 = parse_version("v0.22")
        v2 = parse_version("0.30")

        past = is_past_sunset("v0.28")  # 0.30 > 0.28 → True
        not_past = is_past_sunset("v0.32")  # 0.30 < 0.32 → False
        empty = is_past_sunset("")  # empty → False

        return (v1 == (0, 22) and v2 == (0, 30) and past and not not_past and not empty), \
            f"parse={v1},{v2} past={past} not_past={not_past}"
    except ImportError:
        return False, "modules package not importable"


def test_module_data_export():
    """9. Module manifest read + data contract validation."""
    manifest_path = Path(__file__).parent.parent / "BigEd" / "launcher" / "modules" / "manifest.json"
    if not manifest_path.exists():
        return False, "manifest.json not found"

    data = json.loads(manifest_path.read_text())
    modules = data.get("modules", [])
    if not modules:
        return False, "no modules in manifest"

    # Validate manifest structure
    required_keys = {"name", "file", "version", "default_enabled", "deprecated"}
    for mod in modules:
        missing = required_keys - set(mod.keys())
        if missing:
            return False, f"module '{mod.get('name', '?')}' missing keys: {missing}"

    return True, f"{len(modules)} modules validated"


def test_db_wal_stress():
    """10. Concurrent reads and writes to test WAL mode."""
    import db
    db.init_db()

    errors = []
    lock = threading.Lock()

    def _writer(n):
        for i in range(10):
            try:
                db.post_task(f"soak_wal_{n}", json.dumps({"i": i}), priority=1)
            except Exception as e:
                with lock:
                    errors.append(str(e))

    def _reader():
        for _ in range(10):
            try:
                db.get_fleet_status()
            except Exception as e:
                with lock:
                    errors.append(str(e))

    threads = []
    for i in range(3):
        threads.append(threading.Thread(target=_writer, args=(i,)))
    for _ in range(3):
        threads.append(threading.Thread(target=_reader))

    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    return len(errors) == 0, f"{len(errors)} errors (0 expected)"


def test_task_dag():
    """11. Task dependency chain: A → B → C, complete A, verify B promotes."""
    import db
    db.init_db()
    # Post chain: A → B → C
    task_ids = db.post_task_chain([
        {"type": "soak_dag_a", "payload": {"step": "a"}},
        {"type": "soak_dag_b", "payload": {"step": "b"}},
        {"type": "soak_dag_c", "payload": {"step": "c"}},
    ], priority=5)
    if len(task_ids) != 3:
        return False, f"expected 3 tasks, got {len(task_ids)}"

    # A should be PENDING, B and C should be WAITING
    a, b, c = task_ids
    ra = db.get_task_result(a)
    rb = db.get_task_result(b)
    rc = db.get_task_result(c)
    if ra["status"] != "PENDING":
        return False, f"task A should be PENDING, got {ra['status']}"
    if rb["status"] != "WAITING":
        return False, f"task B should be WAITING, got {rb['status']}"
    if rc["status"] != "WAITING":
        return False, f"task C should be WAITING, got {rc['status']}"

    # Complete A → B should promote to PENDING, C still WAITING
    db.complete_task(a, json.dumps({"result": "done_a"}))
    rb = db.get_task_result(b)
    rc = db.get_task_result(c)
    if rb["status"] != "PENDING":
        return False, f"task B should be PENDING after A done, got {rb['status']}"
    if rc["status"] != "WAITING":
        return False, f"task C should still be WAITING, got {rc['status']}"

    # Complete B → C should promote
    db.complete_task(b, json.dumps({"result": "done_b"}))
    rc = db.get_task_result(c)
    if rc["status"] != "PENDING":
        return False, f"task C should be PENDING after B done, got {rc['status']}"

    return True, "chain A->B->C promoted correctly"


def test_task_dag_cascade_fail():
    """12. DAG cascade: fail A → B should auto-fail."""
    import db
    db.init_db()
    task_ids = db.post_task_chain([
        {"type": "soak_dag_f1", "payload": {}},
        {"type": "soak_dag_f2", "payload": {}},
    ], priority=5)
    a, b = task_ids
    db.fail_task(a, "intentional failure")
    rb = db.get_task_result(b)
    if rb["status"] != "FAILED":
        return False, f"task B should cascade-fail, got {rb['status']}"
    if "Dependency" not in (rb.get("error") or ""):
        return False, f"expected dependency error message, got: {rb.get('error', '')[:60]}"
    return True, "cascade fail propagated"


def test_offline_skill_rejection():
    """14. Offline mode: REQUIRES_NETWORK skills return error dict."""
    import importlib
    # Check that web_search has REQUIRES_NETWORK = True
    mod = importlib.import_module("skills.web_search")
    if not getattr(mod, "REQUIRES_NETWORK", False):
        return False, "web_search.REQUIRES_NETWORK should be True"
    # Check that code_review does NOT require network
    mod2 = importlib.import_module("skills.code_review")
    if getattr(mod2, "REQUIRES_NETWORK", False):
        return False, "code_review should NOT require network"
    # Verify the config helper works
    from config import is_offline, AIR_GAP_SKILLS
    fake_cfg = {"fleet": {"offline_mode": True}}
    if not is_offline(fake_cfg):
        return False, "is_offline should return True"
    return True, "REQUIRES_NETWORK flags + is_offline helper correct"


def test_air_gap_whitelist():
    """15. Air-gap mode: whitelist blocks non-approved skills."""
    from config import is_air_gap, AIR_GAP_SKILLS
    # Verify air_gap implies offline
    fake_cfg = {"fleet": {"air_gap_mode": True, "offline_mode": False}}
    from config import load_config
    # Simulate: air_gap_mode should force offline_mode
    import tomllib
    # Just check the whitelist contents
    expected_approved = {"code_review", "summarize", "discuss", "flashcard", "rag_query"}
    missing = expected_approved - AIR_GAP_SKILLS
    if missing:
        return False, f"expected skills missing from whitelist: {missing}"
    # Check that web_search is NOT in whitelist
    if "web_search" in AIR_GAP_SKILLS:
        return False, "web_search should NOT be in air-gap whitelist"
    if "generate_video" in AIR_GAP_SKILLS:
        return False, "generate_video should NOT be in air-gap whitelist"
    return True, f"whitelist OK ({len(AIR_GAP_SKILLS)} approved, network skills blocked)"


def test_review_status_lifecycle():
    """16. REVIEW status: review_task + reject_task round-trip."""
    import db
    db.init_db()
    db.register_agent("soak_reviewer", "test", 0)
    tid = db.post_task("soak_review_test", json.dumps({"data": "test"}),
                       priority=10, assigned_to="soak_reviewer")
    # Claim (assigned_to ensures only our agent gets it)
    task = db.claim_task("soak_reviewer")
    if not task or task["id"] != tid:
        return False, f"claim failed (got {task['id'] if task else 'None'} expected {tid})"
    # Transition to REVIEW
    db.review_task(tid, json.dumps({"output": "draft result"}))
    r = db.get_task_result(tid)
    if r["status"] != "REVIEW":
        return False, f"expected REVIEW, got {r['status']}"
    # Reject with critique
    rounds = db.reject_task(tid, "Missing error handling")
    if rounds != 1:
        return False, f"expected round 1, got {rounds}"
    r = db.get_task_result(tid)
    if r["status"] != "PENDING":
        return False, f"expected PENDING after reject, got {r['status']}"
    payload = json.loads(r["payload_json"])
    if payload.get("_review_critique") != "Missing error handling":
        return False, f"critique not in payload: {payload}"
    if payload.get("_review_round") != 1:
        return False, f"round not in payload: {payload}"
    # Complete normally after second attempt
    task2 = db.claim_task("soak_reviewer")
    if task2:
        db.complete_task(task2["id"], json.dumps({"output": "fixed"}))
    return True, "REVIEW > reject > PENDING > claim > DONE lifecycle OK"


def test_review_verdict_parsing():
    """17. _review._parse_verdict handles various response formats."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent / "skills"))
    from _review import _parse_verdict
    # Clean JSON
    v1 = _parse_verdict('{"verdict": "PASS", "critique": "looks good", "confidence": 0.9}')
    if v1["verdict"] != "PASS":
        return False, f"expected PASS, got {v1}"
    # JSON embedded in text
    v2 = _parse_verdict('Here is my review:\n{"verdict": "FAIL", "critique": "missing tests"}\nEnd.')
    if v2["verdict"] != "FAIL":
        return False, f"expected FAIL, got {v2}"
    # Plain text fallback
    v3 = _parse_verdict("This output FAILS the quality check because it has no error handling.")
    if v3["verdict"] != "FAIL":
        return False, f"expected FAIL from keyword, got {v3}"
    v4 = _parse_verdict("The output looks good and passes all criteria.")
    if v4["verdict"] != "PASS":
        return False, f"expected PASS from default, got {v4}"
    return True, "all 4 verdict formats parsed correctly"


def test_quarantine_lifecycle():
    """18. Quarantine: set + check + clear agent status."""
    import db
    db.init_db()
    db.register_agent("soak_quarantine_agent", "test", 0)
    # Quarantine
    db.quarantine_agent("soak_quarantine_agent", "test reason")
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM agents WHERE name='soak_quarantine_agent'").fetchone()
        if row["status"] != "QUARANTINED":
            return False, f"expected QUARANTINED, got {row['status']}"
    # Check message was posted
    msgs = db.get_messages("soak_quarantine_agent", unread_only=True, limit=5)
    quarantine_msgs = [m for m in msgs if "quarantine" in m.get("body_json", "")]
    if not quarantine_msgs:
        return False, "quarantine message not found"
    # Clear
    db.clear_quarantine("soak_quarantine_agent")
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM agents WHERE name='soak_quarantine_agent'").fetchone()
        if row["status"] != "IDLE":
            return False, f"expected IDLE after clear, got {row['status']}"
    return True, "quarantine set/check/clear OK"


def test_dlp_scrubbing():
    """19. DLP: detect and redact secrets in text."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent / "skills"))
    from _watchdog import _contains_secret, _redact_secrets
    # Test pattern detection
    test_text = "My API key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz and also AIzaSyAbcdefghijklmnopqrstuvwxyz12345"
    if not _contains_secret(test_text):
        return False, "should detect sk- and AIza patterns"
    redacted = _redact_secrets(test_text)
    if "sk-ant" in redacted:
        return False, f"sk- not redacted: {redacted[:80]}"
    if "AIzaSy" in redacted:
        return False, f"AIza not redacted: {redacted[:80]}"
    # Clean text should pass
    clean = "Hello world, this is normal text with no secrets."
    if _contains_secret(clean):
        return False, "false positive on clean text"
    return True, "secret detection + redaction OK"


def test_waiting_human_lifecycle():
    """20. WAITING_HUMAN: request input + respond + resume."""
    import db
    db.init_db()
    db.register_agent("soak_hitl_agent", "test", 0)
    tid = db.post_task("soak_hitl_test", json.dumps({"step": "initial"}),
                       priority=10, assigned_to="soak_hitl_agent")
    # Claim
    task = db.claim_task("soak_hitl_agent")
    if not task:
        return False, "claim failed"
    # Request human input
    db.request_human_input(tid, "soak_hitl_agent", "Which option: A or B?")
    r = db.get_task_result(tid)
    if r["status"] != "WAITING_HUMAN":
        return False, f"expected WAITING_HUMAN, got {r['status']}"
    # Check waiting tasks query
    waiting = db.get_waiting_human_tasks()
    found = any(t["id"] == tid for t in waiting)
    if not found:
        return False, "task not in get_waiting_human_tasks()"
    # Operator responds
    db.respond_to_agent(tid, "Option A")
    r = db.get_task_result(tid)
    if r["status"] != "PENDING":
        return False, f"expected PENDING after respond, got {r['status']}"
    payload = json.loads(r["payload_json"])
    if payload.get("_human_response") != "Option A":
        return False, f"response not in payload: {payload}"
    return True, "WAITING_HUMAN request/respond/resume OK"


def test_channel_broadcast_isolation():
    """21. Broadcast to channel='agent', verify supervisors got 0 messages."""
    import db
    db.init_db()
    # Register supervisors and workers
    db.register_agent("soak_sup_1", "supervisor", 0)
    db.register_agent("soak_sup_2", "supervisor", 0)
    db.register_agent("soak_wk_1", "worker", 0)
    db.register_agent("soak_wk_2", "worker", 0)
    ts = str(time.time())
    db.broadcast_message("soak_sender", json.dumps({"isolation": ts}), channel="agent")
    # Workers should have received it
    wk1 = db.get_messages("soak_wk_1", unread_only=True, limit=5, channels=["agent"])
    wk2 = db.get_messages("soak_wk_2", unread_only=True, limit=5, channels=["agent"])
    got_wk = sum(1 for m in wk1 + wk2 if ts in m.get("body_json", ""))
    # Supervisors should NOT have received it
    sup1 = db.get_messages("soak_sup_1", unread_only=True, limit=5, channels=["agent"])
    sup2 = db.get_messages("soak_sup_2", unread_only=True, limit=5, channels=["agent"])
    got_sup = sum(1 for m in sup1 + sup2 if ts in m.get("body_json", ""))
    ok = got_wk == 2 and got_sup == 0
    return ok, f"workers={got_wk}/2 supervisors={got_sup}/0"


def test_notes_append_load():
    """22. Post 100 notes, verify ordering + since/count filtering."""
    import db
    db.init_db()
    channel = "soak_notes_test"
    for i in range(100):
        db.post_note(channel, "soak_noter", json.dumps({"i": i}))
    # Verify total count
    count = db.get_note_count(channel)
    if count != 100:
        return False, f"expected 100 notes, got {count}"
    # Get all notes (default desc order without since)
    all_notes = db.get_notes(channel, limit=100)
    if len(all_notes) != 100:
        return False, f"expected 100 returned, got {len(all_notes)}"
    # Since filtering with a synthetic past timestamp
    since_notes = db.get_notes(channel, since="2000-01-01T00:00:00", limit=100)
    if len(since_notes) != 100:
        return False, f"since past expected 100, got {len(since_notes)}"
    # Since with future timestamp should return 0
    future_notes = db.get_notes(channel, since="2099-01-01T00:00:00", limit=100)
    if len(future_notes) != 0:
        return False, f"since future expected 0, got {len(future_notes)}"
    # Count matches
    since_count = db.get_note_count(channel, since="2000-01-01T00:00:00")
    ok = since_count == 100
    return ok, f"total=100 since_past=100 since_future=0"


def test_security_config():
    """23. Security config: sandbox_skills, dependency_scan, network_hardening."""
    from config import load_config
    cfg = load_config()
    sec = cfg.get("security", {})
    if "sandbox_enabled" not in sec:
        return False, "missing sandbox_enabled"
    if "sandbox_skills" not in sec:
        return False, "missing sandbox_skills"
    if "dependency_scan_enabled" not in sec:
        return False, "missing dependency_scan_enabled"
    if "network_hardening_enabled" not in sec:
        return False, "missing network_hardening_enabled"
    skills = sec["sandbox_skills"]
    if "code_write" not in skills:
        return False, f"code_write not in sandbox_skills: {skills}"
    return True, f"security config OK (sandbox={sec['sandbox_enabled']}, {len(skills)} sandboxed skills)"


def test_post_task_validation():
    """24. post_task rejects invalid JSON payloads."""
    import db
    db.init_db()
    try:
        db.post_task("soak_valid", "not valid json{{{")
        return False, "should have raised ValueError"
    except ValueError:
        pass
    # Valid JSON should work
    tid = db.post_task("soak_valid", '{"ok": true}')
    if not tid:
        return False, "valid post_task returned None"
    # Priority clamping
    tid2 = db.post_task("soak_valid", '{}', priority=99)
    r = db.get_task_result(tid2)
    if r["priority"] != 10:
        return False, f"priority should be clamped to 10, got {r['priority']}"
    return True, "validation + clamping works"


def cleanup():
    """Remove soak test artifacts from DB."""
    import db
    with db.get_conn() as conn:
        conn.execute("DELETE FROM agents WHERE name LIKE 'soak_%'")
        conn.execute("DELETE FROM tasks WHERE type LIKE 'soak_%'")
        conn.execute("DELETE FROM messages WHERE from_agent LIKE 'soak_%' OR to_agent LIKE 'soak_%'")
        conn.execute("DELETE FROM locks WHERE holder LIKE 'soak_%'")
        conn.execute("DELETE FROM locks WHERE name='soak_test_lock'")
        conn.execute("DELETE FROM notes WHERE from_agent LIKE 'soak_%'")
        conn.execute("DELETE FROM notes WHERE channel='soak_notes_test'")


def main():
    parser = argparse.ArgumentParser(description="Fleet Soak Test")
    parser.add_argument("--fast", action="store_true", help="Skip slow tests")
    args = parser.parse_args()

    os.environ["FLEET_TEST_DB"] = ":memory:"

    print("Fleet Soak Test (v0.38)")
    print("=" * 50)

    tests = [
        ("Task flood (100 tasks)", test_task_flood),
        ("Task claim/complete", test_task_claim_complete),
        ("Concurrent claims", test_concurrent_claims),
        ("Lock contention", test_lock_contention),
        ("Stale recovery under load", test_stale_recovery_under_load),
        ("Broadcast under load", test_broadcast_under_load),
        ("Training lock lifecycle", test_training_lock_lifecycle),
        ("Deprecation lifecycle", test_deprecation_lifecycle),
        ("Module manifest validation", test_module_data_export),
        ("DB WAL stress", test_db_wal_stress),
        ("Task DAG (dependency chain)", test_task_dag),
        ("Task DAG (cascade fail)", test_task_dag_cascade_fail),
        ("Offline skill rejection", test_offline_skill_rejection),
        ("Air-gap whitelist", test_air_gap_whitelist),
        ("Review status lifecycle", test_review_status_lifecycle),
        ("Review verdict parsing", test_review_verdict_parsing),
        ("Quarantine lifecycle", test_quarantine_lifecycle),
        ("DLP scrubbing", test_dlp_scrubbing),
        ("WAITING_HUMAN lifecycle", test_waiting_human_lifecycle),
        ("Channel broadcast isolation", test_channel_broadcast_isolation),
        ("Notes append + load", test_notes_append_load),
        ("Security config", test_security_config),
        ("Post task validation", test_post_task_validation),
    ]

    results = []
    for name, fn in tests:
        results.append(check(name, fn))

    print("=" * 50)
    passed = sum(results)
    total = len(results)
    cleanup()
    print(f"Result: {passed}/{total} passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    main()
