//! Benchmark: task posting + claiming throughput.
//!
//! This uses a simple `fn main()` harness (no criterion dependency).
//! Run with: `cargo bench`

use std::time::Instant;

fn main() {
    let db = biged_core::db::Db::in_memory().unwrap();
    db.register_agent("bench_worker", "coder").unwrap();

    let n = 1000;

    // ── Benchmark: post N tasks ──────────────────────────────────────────────
    let start = Instant::now();
    for i in 0..n {
        db.post_task("bench_skill", &format!(r#"{{"i":{i}}}"#), 5, None)
            .unwrap();
    }
    let post_elapsed = start.elapsed();

    // ── Benchmark: claim N tasks ─────────────────────────────────────────────
    let start = Instant::now();
    let mut claimed = 0;
    while db.claim_task("coder").unwrap().is_some() {
        claimed += 1;
    }
    let claim_elapsed = start.elapsed();

    println!(
        "Posted {n} tasks in {post_elapsed:?} ({:.0} tasks/s)",
        n as f64 / post_elapsed.as_secs_f64()
    );
    println!(
        "Claimed {claimed} tasks in {claim_elapsed:?} ({:.0} tasks/s)",
        claimed as f64 / claim_elapsed.as_secs_f64()
    );

    // ── Benchmark: complete N tasks ──────────────────────────────────────────
    // Re-post and claim, then measure complete
    for i in 0..n {
        db.post_task("bench_skill", &format!(r#"{{"i":{i}}}"#), 5, None)
            .unwrap();
    }
    let mut task_ids = Vec::new();
    while let Some(task) = db.claim_task("coder").unwrap() {
        task_ids.push(task.id);
    }
    let start = Instant::now();
    for id in &task_ids {
        db.complete_task(*id, r#"{"ok":true}"#).unwrap();
    }
    let complete_elapsed = start.elapsed();

    println!(
        "Completed {} tasks in {complete_elapsed:?} ({:.0} tasks/s)",
        task_ids.len(),
        task_ids.len() as f64 / complete_elapsed.as_secs_f64()
    );
}
