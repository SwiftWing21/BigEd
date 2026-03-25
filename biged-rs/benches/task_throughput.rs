//! Comprehensive performance benchmarks for BigEd Rust rewrite.
//!
//! Measures startup, task throughput, config parsing, memory footprint,
//! and event bus throughput.  No external framework required.
//!
//! Run with: `cargo bench --bench task_throughput`

use std::mem::size_of;
use std::time::Instant;

use biged_core::config::FleetConfig;
use biged_core::db::Db;
use biged_core::types::{Agent, Task};
use biged_supervisor::events::{create_event_bus, FleetEvent};

// ── Helpers ─────────────────────────────────────────────────────────────────

/// Run a closure `n` times and return the total elapsed time.
fn bench<F: FnMut()>(mut f: F, n: usize) -> std::time::Duration {
    let start = Instant::now();
    for _ in 0..n {
        f();
    }
    start.elapsed()
}

/// Format a rate (ops/s) with thousands separators.
fn rate(count: usize, elapsed: std::time::Duration) -> String {
    let per_sec = count as f64 / elapsed.as_secs_f64();
    if per_sec >= 1_000_000.0 {
        format!("{:.2}M", per_sec / 1_000_000.0)
    } else if per_sec >= 1_000.0 {
        format!("{:.0}", per_sec)
    } else {
        format!("{:.1}", per_sec)
    }
}

// ── Minimal fleet.toml for config parse benchmark ───────────────────────────

const SAMPLE_TOML: &str = r#"
[fleet]
offline_mode = false
max_workers = 10
idle_timeout_secs = 10

[models]
local = "qwen3:8b"
complex = "qwen3:8b"
ollama_host = "http://localhost:11434"

[dashboard]
enabled = true
port = 5555
theme = "figma"

[workers]
max_workers = 6
memory_limit_mb = 384

[thermal]
gpu_target_c = 75
poll_interval_secs = 5

[budgets]
period = "day"
enforcement = "warn"

[backup]
enabled = true
interval_secs = 1200
depth = 10
"#;

fn main() {
    let n = 1000;
    let event_n = 10_000;

    println!();
    println!("=== BigEd Rust Performance Benchmarks ===");
    println!();

    // ── 1. Startup time: create DB + register 6 agents ──────────────────────
    let start = Instant::now();
    let db = Db::in_memory().unwrap();
    let agent_roles = [
        ("coder_1", "coder"),
        ("coder_2", "coder"),
        ("coder_3", "coder"),
        ("researcher_1", "researcher"),
        ("reviewer_1", "reviewer"),
        ("conductor_1", "conductor"),
    ];
    for (name, role) in &agent_roles {
        db.register_agent(name, role).unwrap();
    }
    let startup_elapsed = start.elapsed();
    println!(
        "Startup (DB + 6 agents):     {:.2}ms",
        startup_elapsed.as_secs_f64() * 1000.0
    );

    // ── 2a. Post N tasks ────────────────────────────────────────────────────
    let start = Instant::now();
    for i in 0..n {
        db.post_task("bench_skill", &format!(r#"{{"i":{i}}}"#), 5, None)
            .unwrap();
    }
    let post_elapsed = start.elapsed();
    println!(
        "Post {} tasks:             {:.2}ms ({} tasks/s)",
        n,
        post_elapsed.as_secs_f64() * 1000.0,
        rate(n, post_elapsed)
    );

    // ── 2b. Claim N tasks ───────────────────────────────────────────────────
    let start = Instant::now();
    let mut claimed = 0;
    while db.claim_task("coder").unwrap().is_some() {
        claimed += 1;
    }
    let claim_elapsed = start.elapsed();
    println!(
        "Claim {} tasks:             {:.2}ms ({} tasks/s)",
        claimed,
        claim_elapsed.as_secs_f64() * 1000.0,
        rate(claimed, claim_elapsed)
    );

    // ── 2c. Complete N tasks ────────────────────────────────────────────────
    // Re-post and claim, then time completion
    for i in 0..n {
        db.post_task("bench_skill", &format!(r#"{{"i":{i}}}"#), 5, None)
            .unwrap();
    }
    let mut task_ids = Vec::new();
    while let Some(task) = db.claim_task("coder").unwrap() {
        task_ids.push(task.id);
    }
    let complete_count = task_ids.len();
    let start = Instant::now();
    for id in &task_ids {
        db.complete_task(*id, r#"{"ok":true}"#).unwrap();
    }
    let complete_elapsed = start.elapsed();
    println!(
        "Complete {} tasks:          {:.2}ms ({} tasks/s)",
        complete_count,
        complete_elapsed.as_secs_f64() * 1000.0,
        rate(complete_count, complete_elapsed)
    );

    // ── 3. Config parse time ────────────────────────────────────────────────
    let config_elapsed = bench(
        || {
            let _cfg: FleetConfig = FleetConfig::parse(SAMPLE_TOML).unwrap();
            std::hint::black_box(&_cfg);
        },
        n,
    );
    println!(
        "Config parse ({}x):        {:.2}ms ({:.3}ms/parse)",
        n,
        config_elapsed.as_secs_f64() * 1000.0,
        config_elapsed.as_secs_f64() * 1000.0 / n as f64
    );

    // ── 4. Event bus throughput ─────────────────────────────────────────────
    let (tx, _rx) = create_event_bus(event_n + 128);
    let start = Instant::now();
    for i in 0..event_n {
        tx.send(FleetEvent::TaskCompleted {
            id: i as i64,
            skill: "bench".into(),
            agent: "worker_1".into(),
        })
        .unwrap();
    }
    let event_elapsed = start.elapsed();
    println!(
        "Event bus ({} events):    {:.2}ms ({} events/s)",
        event_n,
        event_elapsed.as_secs_f64() * 1000.0,
        rate(event_n, event_elapsed)
    );

    // ── 5. Memory footprint ─────────────────────────────────────────────────
    println!();
    println!("=== Memory Footprint ===");
    println!("Task struct:       {} bytes", size_of::<Task>());
    println!("Agent struct:      {} bytes", size_of::<Agent>());
    println!("FleetConfig:       {} bytes", size_of::<FleetConfig>());
    println!();

    // ── Summary ─────────────────────────────────────────────────────────────
    println!("=== Comparison Targets (from spec) ===");
    println!("Task throughput:   10x Python baseline (~1,500 tasks/s → ~15,000+ tasks/s)");
    println!("Startup time:      50x Python baseline (~5,000ms → ~100ms)");
    println!("Memory per agent:  5x reduction (~50MB Python → ~10MB Rust)");
    println!();
}
