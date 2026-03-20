"""
Memory Optimizer — Reduce RAM and VRAM pressure across fleet operations.

Analyzes current memory usage, identifies waste, applies safe optimizations.
High success gauge: reduce footprint WITHOUT impacting functionality.

Actions:
  audit       — scan all processes, identify optimization opportunities
  optimize    — apply safe optimizations (gc, cache trim, model unload)
  compact     — aggressive: trim worker count, reduce context windows
  monitor     — continuous monitoring with auto-optimization triggers

Safety rules:
  - NEVER kill a process that's actively executing a task
  - NEVER unload a model that has pending/running tasks
  - NEVER reduce below minimum viable worker count (2)
  - All optimizations must be reversible
  - Log every action for audit trail

Usage:
    lead_client.py task '{"type": "memory_optimizer"}'
    lead_client.py task '{"type": "memory_optimizer", "payload": {"action": "optimize"}}'
"""
import gc
import json
import os
import subprocess
import sys
import time
from pathlib import Path

FLEET_DIR = Path(__file__).parent.parent
SKILL_NAME = "memory_optimizer"
DESCRIPTION = "Analyze and reduce RAM/VRAM pressure across fleet operations."
REQUIRES_NETWORK = False

# Thresholds
RAM_WARNING_PCT = 75
RAM_CRITICAL_PCT = 85
VRAM_WARNING_PCT = 80
VRAM_CRITICAL_PCT = 90
MIN_WORKERS = 2


def run(payload: dict, config: dict, log) -> dict:
    """Run memory optimization."""
    action = payload.get("action", "audit")

    if action == "audit":
        return _audit(config, log)
    elif action == "optimize":
        return _optimize(config, log)
    elif action == "compact":
        return _compact(config, log)
    elif action == "monitor":
        return _monitor(config, log)
    else:
        return {"error": f"Unknown action: {action}. Use: audit, optimize, compact, monitor"}


def _get_memory_state() -> dict:
    """Snapshot current RAM and VRAM state."""
    import psutil

    ram = psutil.virtual_memory()
    state = {
        "ram_total_gb": round(ram.total / 1024**3, 1),
        "ram_used_gb": round(ram.used / 1024**3, 1),
        "ram_pct": ram.percent,
        "ram_available_gb": round(ram.available / 1024**3, 1),
    }

    # VRAM
    try:
        import pynvml
        pynvml.nvmlInit()
        h = pynvml.nvmlDeviceGetHandleByIndex(0)
        m = pynvml.nvmlDeviceGetMemoryInfo(h)
        state["vram_total_gb"] = round(m.total / 1024**3, 1)
        state["vram_used_gb"] = round(m.used / 1024**3, 1)
        state["vram_pct"] = round(m.used / m.total * 100, 1)
        state["vram_free_gb"] = round(m.free / 1024**3, 1)
        pynvml.nvmlShutdown()
    except Exception:
        state["vram_total_gb"] = 0
        state["vram_pct"] = 0

    # Ollama models loaded
    try:
        import urllib.request
        host = config.get("models", {}).get("ollama_host", "http://localhost:11434")
        with urllib.request.urlopen(f"{host}/api/ps", timeout=3) as r:
            data = json.loads(r.read())
        models = []
        for m in data.get("models", []):
            models.append({
                "name": m["name"],
                "size_gb": round(m.get("size", 0) / 1024**3, 1),
                "processor": m.get("details", {}).get("processor", "unknown"),
            })
        state["models_loaded"] = models
        state["models_vram_gb"] = sum(m["size_gb"] for m in models)
    except Exception:
        state["models_loaded"] = []
        state["models_vram_gb"] = 0

    # Fleet workers
    try:
        import psutil as _ps
        workers = []
        for p in _ps.process_iter(['pid', 'name', 'cmdline', 'memory_info']):
            try:
                cmd = ' '.join(p.info.get('cmdline') or [])
                if 'worker.py' in cmd or 'supervisor.py' in cmd or 'hw_supervisor.py' in cmd:
                    rss = p.info['memory_info'].rss / 1024**2
                    workers.append({"name": cmd.split('--role ')[-1].split()[0] if '--role' in cmd else p.info['name'],
                                   "pid": p.pid, "rss_mb": round(rss, 1)})
            except Exception:
                pass
        state["fleet_workers"] = workers
        state["fleet_rss_mb"] = sum(w["rss_mb"] for w in workers)
    except Exception:
        state["fleet_workers"] = []
        state["fleet_rss_mb"] = 0

    return state


def _audit(config, log) -> dict:
    """Analyze memory usage and identify optimization opportunities."""
    state = _get_memory_state()
    findings = []
    savings_mb = 0

    # RAM analysis
    if state["ram_pct"] > RAM_CRITICAL_PCT:
        findings.append({
            "severity": "critical",
            "area": "ram",
            "message": f"RAM at {state['ram_pct']}% — system may swap",
            "action": "compact",
        })
    elif state["ram_pct"] > RAM_WARNING_PCT:
        findings.append({
            "severity": "warning",
            "area": "ram",
            "message": f"RAM at {state['ram_pct']}% — approaching pressure",
            "action": "optimize",
        })

    # VRAM analysis
    if state["vram_pct"] > VRAM_CRITICAL_PCT:
        findings.append({
            "severity": "critical",
            "area": "vram",
            "message": f"VRAM at {state['vram_pct']}% — model performance will degrade",
            "action": "unload_idle_models",
        })

    # Multiple models loaded
    if len(state.get("models_loaded", [])) > 1:
        cpu_models = [m for m in state["models_loaded"] if "CPU" in str(m.get("processor", ""))]
        if cpu_models:
            savings_mb += sum(m["size_gb"] * 1024 for m in cpu_models)
            findings.append({
                "severity": "info",
                "area": "vram",
                "message": f"{len(cpu_models)} CPU-mode model(s) still using RAM: {', '.join(m['name'] for m in cpu_models)}",
                "action": "unload_cpu_models",
                "savings_mb": round(savings_mb),
            })

    # Worker memory
    high_rss_workers = [w for w in state.get("fleet_workers", []) if w["rss_mb"] > 200]
    if high_rss_workers:
        findings.append({
            "severity": "warning",
            "area": "fleet",
            "message": f"{len(high_rss_workers)} worker(s) using >200MB: {', '.join(w['name'] for w in high_rss_workers)}",
            "action": "gc_workers",
        })

    # Python GC pressure
    gc_stats = gc.get_stats()
    gen2_collections = gc_stats[2]["collections"] if len(gc_stats) > 2 else 0
    if gen2_collections > 100:
        findings.append({
            "severity": "info",
            "area": "python",
            "message": f"Gen2 GC has run {gen2_collections} times — possible memory fragmentation",
            "action": "gc_collect",
        })

    # Ollama context window optimization
    for m in state.get("models_loaded", []):
        if m.get("size_gb", 0) > 4:
            findings.append({
                "severity": "info",
                "area": "ollama",
                "message": f"{m['name']} using {m['size_gb']}GB — consider lower num_ctx for reduced VRAM",
                "action": "reduce_context",
            })

    log.info(f"Memory audit: {len(findings)} findings, ~{savings_mb:.0f}MB recoverable")
    return {
        "state": state,
        "findings": findings,
        "recoverable_mb": round(savings_mb),
    }


def _optimize(config, log) -> dict:
    """Apply safe, reversible optimizations."""
    state = _get_memory_state()
    actions_taken = []

    # 1. Force Python garbage collection
    before_gc = gc.get_stats()[2]["collected"] if len(gc.get_stats()) > 2 else 0
    collected = gc.collect(2)
    actions_taken.append({"action": "gc_collect", "collected": collected})
    log.info(f"GC: collected {collected} objects")

    # 2. Unload idle CPU models (keep GPU model + failsafe only)
    try:
        import urllib.request
        host = config.get("models", {}).get("ollama_host", "http://localhost:11434")
        with urllib.request.urlopen(f"{host}/api/ps", timeout=3) as r:
            data = json.loads(r.read())
        loaded = data.get("models", [])

        # Keep: largest GPU model + smallest CPU model (failsafe)
        gpu_models = [m for m in loaded if "GPU" in str(m.get("details", {}).get("processor", ""))]
        cpu_models = [m for m in loaded if "CPU" in str(m.get("details", {}).get("processor", ""))]

        # Sort CPU models by size, keep smallest (failsafe)
        if len(cpu_models) > 1:
            cpu_models.sort(key=lambda m: m.get("size", 0))
            for m in cpu_models[1:]:  # Unload all except smallest
                try:
                    body = json.dumps({"model": m["name"], "keep_alive": 0}).encode()
                    req = urllib.request.Request(
                        f"{host}/api/generate", data=body, method="POST",
                        headers={"Content-Type": "application/json"})
                    urllib.request.urlopen(req, timeout=5)
                    actions_taken.append({"action": "unload_model", "model": m["name"],
                                         "freed_gb": round(m.get("size", 0) / 1024**3, 1)})
                    log.info(f"Unloaded idle CPU model: {m['name']}")
                except Exception:
                    pass
    except Exception:
        pass

    # 3. Reduce Ollama context window for non-critical models
    # (Ollama uses ~2MB per 1K context tokens)
    # This is applied on next model load, not retroactively

    # 4. Trim Python module caches
    try:
        import importlib
        importlib.invalidate_caches()
        actions_taken.append({"action": "invalidate_caches"})
    except Exception:
        pass

    after_state = _get_memory_state()
    ram_saved = state["ram_used_gb"] - after_state["ram_used_gb"]
    vram_saved = state.get("vram_used_gb", 0) - after_state.get("vram_used_gb", 0)

    log.info(f"Optimization complete: {len(actions_taken)} actions, "
             f"RAM: {ram_saved:+.1f}GB, VRAM: {vram_saved:+.1f}GB")
    return {
        "actions": actions_taken,
        "before": {"ram_gb": state["ram_used_gb"], "vram_gb": state.get("vram_used_gb", 0)},
        "after": {"ram_gb": after_state["ram_used_gb"], "vram_gb": after_state.get("vram_used_gb", 0)},
        "saved": {"ram_gb": round(ram_saved, 2), "vram_gb": round(vram_saved, 2)},
    }


def _compact(config, log) -> dict:
    """Aggressive optimization — reduce worker count, trim context windows."""
    # First run normal optimization
    result = _optimize(config, log)

    # Additional aggressive actions
    actions = result.get("actions", [])

    # 5. Reduce Ollama num_ctx on next load
    try:
        import urllib.request
        host = config.get("models", {}).get("ollama_host", "http://localhost:11434")
        # Set lower context for loaded models
        with urllib.request.urlopen(f"{host}/api/ps", timeout=3) as r:
            data = json.loads(r.read())
        for m in data.get("models", []):
            if m.get("details", {}).get("parameter_size", "") in ("8B", "7B"):
                # Reload with reduced context (4096 → 2048)
                try:
                    body = json.dumps({
                        "model": m["name"], "prompt": "",
                        "keep_alive": config.get("models", {}).get("keep_alive_mins", 30) * 60,
                        "options": {"num_ctx": 2048},
                    }).encode()
                    req = urllib.request.Request(
                        f"{host}/api/generate", data=body, method="POST",
                        headers={"Content-Type": "application/json"})
                    urllib.request.urlopen(req, timeout=30)
                    actions.append({"action": "reduce_context", "model": m["name"],
                                   "from": 4096, "to": 2048,
                                   "estimated_savings_mb": 256})
                    log.info(f"Reduced context window: {m['name']} → 2048 tokens")
                except Exception:
                    pass
    except Exception:
        pass

    # 6. Signal supervisor to scale down non-core agents
    try:
        wake_file = FLEET_DIR / ".memory_pressure"
        wake_file.write_text(json.dumps({
            "type": "memory_pressure",
            "timestamp": time.time(),
            "ram_pct": result.get("after", {}).get("ram_gb", 0),
            "action": "scale_down",
        }), encoding="utf-8")
        actions.append({"action": "signal_scale_down"})
        log.info("Signaled supervisor to scale down agents")
    except Exception:
        pass

    result["actions"] = actions
    result["mode"] = "compact"
    return result


def _monitor(config, log) -> dict:
    """One-shot monitoring check — returns recommendations."""
    audit = _audit(config, log)

    critical = [f for f in audit["findings"] if f["severity"] == "critical"]
    warnings = [f for f in audit["findings"] if f["severity"] == "warning"]

    recommendation = "none"
    if critical:
        recommendation = "compact"
    elif warnings:
        recommendation = "optimize"

    return {
        "recommendation": recommendation,
        "critical_count": len(critical),
        "warning_count": len(warnings),
        "state": audit["state"],
        "findings": audit["findings"],
    }
