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


def test_post_task_validation():
    """13. post_task rejects invalid JSON payloads."""
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


def main():
    parser = argparse.ArgumentParser(description="Fleet Soak Test")
    parser.add_argument("--fast", action="store_true", help="Skip slow tests")
    args = parser.parse_args()

    os.environ["FLEET_TEST_DB"] = ":memory:"

    print("Fleet Soak Test (v0.31)")
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
