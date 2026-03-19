"""0.08.00: Async DAG recalculation queue — prevents SQLite WAL thundering herd."""
import json
import threading
import time
import queue
import logging

log = logging.getLogger("dag_queue")

_dag_queue = queue.Queue()
_running = False
_thread = None


def enqueue_promotion(task_id: int):
    """Enqueue a task completion for async DAG promotion."""
    if not _running:
        start()  # auto-start on first use
    _dag_queue.put(("promote", task_id))


def enqueue_cascade_fail(task_id: int, error: str):
    """Enqueue a task failure for async cascade processing."""
    if not _running:
        start()
    _dag_queue.put(("cascade_fail", task_id, error))


def start():
    """Start the async DAG processor thread."""
    global _running, _thread
    if _running:
        return
    _running = True
    _thread = threading.Thread(target=_process_loop, daemon=True)
    _thread.start()
    log.info("DAG queue processor started")


def stop():
    """Stop the async DAG processor."""
    global _running
    _running = False


def _process_loop():
    """Main loop: batch-process DAG events with coalescing."""
    while _running:
        events = []
        try:
            # Wait for first event
            event = _dag_queue.get(timeout=1)
            events.append(event)
            # Drain any queued events (batch coalescing)
            while not _dag_queue.empty():
                try:
                    events.append(_dag_queue.get_nowait())
                except queue.Empty:
                    break
        except queue.Empty:
            continue

        if not events:
            continue

        # Process batch
        try:
            import db
            with db.get_conn() as conn:
                for event in events:
                    if event[0] == "promote":
                        db._promote_waiting_tasks(conn)
                    elif event[0] == "cascade_fail":
                        _, task_id, error = event
                        db._cascade_fail_dependents(conn, task_id, error)
            log.debug(f"DAG queue processed {len(events)} events")
        except Exception as e:
            log.warning(f"DAG queue error: {e}")

        # Small delay to allow coalescing
        time.sleep(0.1)
