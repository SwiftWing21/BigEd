"""
BigEd Load Test -- concurrent task processing stress test.

Verifies that N agents claiming tasks simultaneously do not corrupt data,
double-claim the same task, or leave tasks stuck in RUNNING state.

Usage:
    python fleet/load_test.py                     # default: 10 workers, 100 tasks
    python fleet/load_test.py --workers 20 --tasks 500
    python fleet/load_test.py --duration 60        # run for 60 seconds
    python fleet/load_test.py --workers 5 --tasks 20 --verbose
"""
import argparse
import threading
import time
import sys
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

log = logging.getLogger("load_test")

# Sentinel classification used for all synthetic tasks so they can be
# cleaned up reliably without touching real fleet work.
_CLASSIFICATION = "load_test_synthetic"
_SKILL_PREFIX = "load_test_skill_"


def _post_tasks(db, num_tasks: int) -> list:
    """Post num_tasks synthetic tasks and return their IDs."""
    task_ids = []
    for i in range(num_tasks):
        skill = f"{_SKILL_PREFIX}{i % 5}"
        tid = db.post_task(
            skill,
            json.dumps({"test_index": i}),
            priority=3,
            classification=_CLASSIFICATION,
        )
        task_ids.append(tid)
    return task_ids


def _cleanup(db) -> int:
    """Delete all synthetic load-test tasks. Returns count removed."""
    with db.get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM tasks WHERE classification=?", (_CLASSIFICATION,)
        )
        return cur.rowcount


def _count_by_status(db, status: str) -> int:
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE classification=? AND status=?",
            (_CLASSIFICATION, status),
        ).fetchone()
        return row["n"] if row else 0


def run_load_test(num_workers: int = 10, num_tasks: int = 100,
                  duration: int = None, verbose: bool = False) -> bool:
    """
    Execute the load test.

    Returns True if PASS (no double claims, no leaks, no errors).
    """
    import db

    db.init_db()

    results = {
        "claimed": 0,
        "completed": 0,
        "errors": 0,
        "double_claims": 0,
    }
    claimed_ids: set = set()
    lock = threading.Lock()
    stop_event = threading.Event()

    # ------------------------------------------------------------------ #
    # Post synthetic tasks
    # ------------------------------------------------------------------ #
    print(f"Posting {num_tasks} synthetic tasks...")
    task_ids = _post_tasks(db, num_tasks)
    print(f"Posted {len(task_ids)} tasks (IDs {task_ids[0]}..{task_ids[-1]})")

    # ------------------------------------------------------------------ #
    # Worker thread
    # ------------------------------------------------------------------ #
    def worker(worker_id: int):
        agent_name = f"load_worker_{worker_id}"
        # Each worker is "proficient" in 2 of the 5 skill types so that
        # affinity matching is exercised, but workers can also fall back to
        # any available task.
        affinity = [
            f"{_SKILL_PREFIX}{worker_id % 5}",
            f"{_SKILL_PREFIX}{(worker_id + 1) % 5}",
        ]

        while not stop_event.is_set():
            try:
                task = db.claim_task(agent_name, affinity_skills=affinity)
                if task is None:
                    time.sleep(0.01)
                    continue

                tid = task["id"]

                # Check for double-claim
                with lock:
                    if tid in claimed_ids:
                        results["double_claims"] += 1
                        log.warning("Double-claim detected! task_id=%s worker=%s", tid, worker_id)
                    else:
                        claimed_ids.add(tid)
                    results["claimed"] += 1

                if verbose:
                    log.debug("worker=%s claimed task=%s", worker_id, tid)

                # Simulate lightweight work
                time.sleep(0.005)

                db.complete_task(tid, json.dumps({"worker": worker_id, "ok": True}))

                with lock:
                    results["completed"] += 1

            except Exception:
                with lock:
                    results["errors"] += 1
                log.warning("Worker %s error:", worker_id, exc_info=True)

    # ------------------------------------------------------------------ #
    # Start workers
    # ------------------------------------------------------------------ #
    threads = []
    start = time.monotonic()
    for i in range(num_workers):
        t = threading.Thread(target=worker, args=(i,), daemon=True, name=f"lw-{i}")
        t.start()
        threads.append(t)

    # ------------------------------------------------------------------ #
    # Wait for completion or timeout / duration
    # ------------------------------------------------------------------ #
    HARD_TIMEOUT = 60  # seconds — never hang forever

    if duration:
        stop_event.wait(timeout=duration)
        stop_event.set()
    else:
        deadline = start + HARD_TIMEOUT
        while time.monotonic() < deadline:
            pending = _count_by_status(db, "PENDING")
            running = _count_by_status(db, "RUNNING")
            if pending == 0 and running == 0:
                break
            time.sleep(0.1)
        stop_event.set()

    for t in threads:
        t.join(timeout=5)

    elapsed = time.monotonic() - start

    # ------------------------------------------------------------------ #
    # Verify final DB state
    # ------------------------------------------------------------------ #
    done_count = _count_by_status(db, "DONE")
    leaked_count = _count_by_status(db, "RUNNING")   # should be 0
    pending_remaining = _count_by_status(db, "PENDING")

    # ------------------------------------------------------------------ #
    # Cleanup synthetic tasks
    # ------------------------------------------------------------------ #
    removed = _cleanup(db)

    # ------------------------------------------------------------------ #
    # Report
    # ------------------------------------------------------------------ #
    print(f"\n{'='*52}")
    print(f"  BigEd Load Test Results")
    print(f"  Workers: {num_workers}  |  Tasks posted: {num_tasks}")
    print(f"{'='*52}")
    print(f"  Duration:          {elapsed:.2f}s")
    print(f"  Claimed (in-mem):  {results['claimed']}")
    print(f"  Completed (DB):    {done_count} / {num_tasks}")
    print(f"  Errors:            {results['errors']}")
    print(f"  Double-claims:     {results['double_claims']}")
    print(f"  Leaked RUNNING:    {leaked_count}")
    print(f"  Pending remaining: {pending_remaining}")
    if elapsed > 0:
        tps = results["completed"] / elapsed
        print(f"  Throughput:        {tps:.1f} tasks/sec")
    print(f"  Cleaned up:        {removed} rows")
    print(f"{'='*52}")

    ok = (
        results["double_claims"] == 0
        and leaked_count == 0
        and results["errors"] == 0
    )
    label = "PASS" if ok else "FAIL"
    print(f"  Result: {label}")
    print(f"{'='*52}\n")

    if not ok:
        if results["double_claims"]:
            log.error("FAIL: %d double-claim(s) detected — atomicity broken",
                      results["double_claims"])
        if leaked_count:
            log.error("FAIL: %d task(s) stuck in RUNNING — possible deadlock or crash",
                      leaked_count)
        if results["errors"]:
            log.error("FAIL: %d uncaught exception(s) in workers", results["errors"])

    return ok


# --------------------------------------------------------------------------- #
# CLI entry point
# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="BigEd concurrent task processing load/stress test"
    )
    parser.add_argument(
        "--workers", type=int, default=10,
        help="Number of concurrent worker threads (default: 10)",
    )
    parser.add_argument(
        "--tasks", type=int, default=100,
        help="Number of synthetic tasks to post (default: 100)",
    )
    parser.add_argument(
        "--duration", type=int, default=None,
        help="Run for this many seconds instead of until all tasks complete",
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-7s  %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    ok = run_load_test(
        num_workers=args.workers,
        num_tasks=args.tasks,
        duration=args.duration,
        verbose=args.verbose,
    )
    sys.exit(0 if ok else 1)
