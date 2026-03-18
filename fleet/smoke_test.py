#!/usr/bin/env python3
"""Smoke test — validates the entire fleet startup chain. Run: uv run python smoke_test.py"""

import importlib
import json
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
    tid = db.post_task("smoke_test", json.dumps({"test": True}), priority=1)
    task = db.claim_task("smoke_agent")
    if not task or task["id"] != tid:
        return False, "claim_task failed"
    db.complete_task(tid, json.dumps({"ok": True}))
    result = db.get_task_result(tid)
    if result["status"] != "DONE":
        return False, f"expected DONE, got {result['status']}"
    return True, f"task {tid} round-tripped"


def test_config():
    """3. Config loads with required keys."""
    from config import load_config
    cfg = load_config()
    required = ["fleet", "models", "workers"]
    missing = [k for k in required if k not in cfg]
    if missing:
        return False, f"missing sections: {missing}"
    return True, f"fleet.toml OK ({len(cfg)} sections)"


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


def test_stale_recovery():
    """8. Stale task recovery finds orphaned RUNNING tasks."""
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


def cleanup():
    """Remove smoke test artifacts from DB."""
    import db
    with db.get_conn() as conn:
        conn.execute("DELETE FROM agents WHERE name LIKE 'smoke_%'")
        conn.execute("DELETE FROM tasks WHERE type LIKE 'smoke_%'")
        conn.execute("DELETE FROM messages WHERE from_agent LIKE 'smoke_%' OR to_agent LIKE 'smoke_%'")


def main():
    print("Fleet Smoke Test")
    print("=" * 40)

    tests = [
        ("Skill imports", test_skill_imports),
        ("DB health", test_db_health),
        ("Config health", test_config),
        ("Ollama reachable", test_ollama_reachable),
        ("RAG search", test_rag_search),
        ("Message round-trip", test_message_roundtrip),
        ("Broadcast round-trip", test_broadcast_roundtrip),
        ("Stale recovery", test_stale_recovery),
    ]

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
