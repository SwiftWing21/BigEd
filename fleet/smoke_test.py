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
        ("Stale recovery", test_stale_recovery),
        ("Training lock", test_training_lock),
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
