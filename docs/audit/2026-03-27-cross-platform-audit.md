# Cross-Platform Audit -- 2026-03-27

Audited codebase for readiness across three target machines:
1. **Primary:** Windows 11, AMD Ryzen 7 5800X, RTX 3080 Ti 12GB, 32GB RAM
2. **Secondary:** Windows, GTX 1070 8GB VRAM
3. **Linux (Steam Deck OLED):** SteamOS (Arch Linux), AMD APU, 16GB unified memory, no discrete GPU

---

## Critical (will crash on Linux or low-VRAM GPU)

### C1. Dr. Ders exits immediately on non-NVIDIA GPU
**File:** `fleet/hw_supervisor.py:678-681`

The `main()` function returns immediately if `_HAS_GPU` is False. `_HAS_GPU` is set from `detect_gpu()` at module level (line 44). On the Steam Deck with an AMD APU, `detect_gpu()` will try NvidiaBackend (fails), then AmdBackend. AmdBackend explicitly raises on Windows (line 81) but should work on Linux via pyamdgpuinfo or rocm-smi. **However**, the Steam Deck's AMD APU may not have pyamdgpuinfo or rocm-smi installed, and SysfsBackend may not find a GPU hwmon node for the integrated GPU. In that case NullBackend is used and Dr. Ders exits -- no thermal monitoring at all.

**Impact:** No thermal/VRAM management on Steam Deck. Fleet runs blind.
**Also:** The log message says "No NVIDIA GPU" but the check is generic (`not _HAS_GPU`). Misleading for AMD users.

### C2. `os.kill(pid, signal.SIGTERM)` on Windows
**Files:**
- `fleet/process_control.py:133, 249, 440`
- `fleet/health_monitor.py:169`

`os.kill()` with `signal.SIGTERM` behaves differently on Windows -- it calls `TerminateProcess()` which is equivalent to `SIGKILL` (no graceful shutdown). The code at `process_control.py:130-136` sends SIGTERM to all agents, then waits 2 seconds and force-kills survivors. On Windows, the SIGTERM already killed them ungracefully.

**Impact:** Workers cannot run cleanup/flush logic on Windows shutdown. This works but violates the graceful shutdown intent.

### C3. `os.killpg()` / `os.getpgrp()` in worker.py (Windows crash risk)
**File:** `fleet/worker.py:379-383`

`_cleanup_children()` calls `os.killpg(os.getpgrp(), ...)` inside a `hasattr(os, 'killpg')` guard. But `os.getpgrp()` may not exist on Windows. The `try/except` around it catches the error, but the logic is fragile.

**Impact:** Likely safe due to exception handling but the guard should also check `os.getpgrp` existence.

### C4. `autoresearch/train_profile.py` Popen missing `creationflags`
**File:** `autoresearch/train_profile.py:86-91`

The `subprocess.Popen(["ollama", "serve"], ...)` call is missing `creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)`. Will cause console window flash on Windows.

**Impact:** Visual annoyance on Windows, functional issue in headless/GUI contexts.

---

## High (degraded experience, workarounds needed)

### H1. GPU backend: AMD APU (Steam Deck) may get NullBackend
**File:** `fleet/gpu.py:80-100`

AmdBackend requires `pyamdgpuinfo` (not standard) or `rocm-smi` (not on SteamOS by default). SysfsBackend looks for `/sys/class/drm/card*/device/hwmon/*/temp1_input` -- the Steam Deck APU does expose this under `amdgpu` hwmon, so **SysfsBackend should work** for temperature.

However, `get_memory_info()` in SysfsBackend reads `mem_info_vram_total` and `mem_info_vram_used` from sysfs -- on the Steam Deck's unified memory APU, these sysfs entries may not exist or may show the carved-out VRAM (typically 1-4GB). VRAM management logic that expects 8-12GB will behave incorrectly.

**Impact:** VRAM thresholds (vram_emergency=0.92) would trigger immediately if reported VRAM is small.

### H2. VRAM thresholds not configurable per-GPU profile
**File:** `fleet/fleet.toml` + `fleet/hw_supervisor.py:77-88`

The model tier defaults are:
- `tier_default: qwen3:8b` (~6.9GB VRAM)
- `tier_mid: qwen3:8b`

On the GTX 1070 (8GB), loading `qwen3:8b` puts VRAM at ~86%, which triggers the `vram_high` threshold (0.85). Dr. Ders would instantly downgrade to `tier_low`.

**Impact:** GTX 1070 cannot effectively run the default model. Needs per-GPU tier overrides.

### H3. PyWebView backend not set for Linux
**File:** `BigEd/launcher/launcher.py:5-6` and `BigEd/launcher/launcher_webview.py:17-18`

`PYWEBVIEW_GUI` is only set to `qt` on Windows. On SteamOS (KDE Plasma), PyWebView auto-detection may try GTK3/WebKitGTK, which may not be installed. The Qt backend would be better since SteamOS ships with Qt/KDE.

**Impact:** Launcher may fail to open on SteamOS if WebKitGTK is not installed.

### H4. Custom font loading is Windows-only
**File:** `BigEd/launcher/ui/theme.py:19`

`load_custom_fonts()` exits immediately on non-Windows. Custom themed fonts won't render on Linux.

**Impact:** Visual degradation on Linux. Not a crash, but themed appearance is lost.

### H5. Screenshot skill is Windows-only
**File:** `fleet/skills/screenshot.py:118-143`

Window capture uses `ctypes.windll.user32`. No Linux fallback exists. Full-screen capture via `PIL.ImageGrab` also Windows-only (and macOS with extra deps).

**Impact:** Screenshot skill non-functional on Linux. Should add `scrot`/`grim` fallback.

### H6. `pgrep` training detection fallbacks
**Files:**
- `fleet/hw_supervisor.py:671-672`
- `fleet/hw_supervisor.py:946`
- `fleet/marathon.py:52`

These correctly guard with `sys.platform != "win32"` and fall back to psutil first. `pgrep` should be available on SteamOS but is not guaranteed.

**Impact:** Low risk -- psutil is the primary detection path.

### H7. `find_lhm_exe()` lacks platform guard
**File:** `fleet/cpu_temp.py:111-129`

`find_lhm_exe()` searches Windows-specific paths using `%LOCALAPPDATA%` env vars. On Linux these env vars are empty strings. The function is only called from guarded contexts, but the function itself does not guard.

**Impact:** Minimal -- always called from guarded code. Defensive improvement.

---

## Medium (cosmetic or non-blocking)

### M1. `winreg` imports are all properly guarded
**Files checked:** `installer_cross.py`, `installer.py`, `uninstaller.py`
**Status:** All properly guarded with `sys.platform == "win32"`. No issues.

### M2. `ctypes.windll` usage properly guarded
**Files checked:** `cpu_temp.py`, `pid_manager.py`, `process_manager.py`, `installer.py`, `theme.py`, `screenshot.py`
**Status:** All properly guarded. No issues.

### M3. `subprocess.Popen` creationflags coverage
Most Popen calls include `creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)`. Exceptions found:

| File | Line | Has creationflags? | Notes |
|------|------|--------------------|-------|
| `autoresearch/train_profile.py` | 86 | NO | Console flash on Windows |
| `BigEd/launcher/installer.py` | 71, 73 | NO | Linux/macOS `open`/`xdg-open` -- not needed |
| `BigEd/launcher/fleet_bridge.py` | 116 | NO | `bash -c` on non-Windows -- fine |
| `BigEd/launcher/fleet_bridge.py` | 194 | NO | `shell=True` -- may flash on Windows |
| `BigEd/launcher/launcher_tkinter.py` | 4091, 4103, 4111 | NO | GUI relaunches -- some may flash |
| `BigEd/launcher/launcher_tkinter.py` | 4120 | NO | `cmd /c start cmd /k` -- intentionally opens console |
| `BigEd/launcher/modules/mod_manual_mode.py` | 573, 575 | NO | `open`/`xdg-open` on non-Windows -- fine |
| `fleet/skills/service_manager.py` | 312-324 | YES (win32 branch) | Correctly split by platform |
| `BigEd/launcher/ui/comm_tab.py` | 684, 994, 1006, 1025 | Mixed | Some may flash |
| `fleet/manual_mode.py` | 410 | Needs check | VS Code launch |

### M4. Ollama path detection only covers Windows fallback
**File:** `fleet/process_manager.py:54-68`

`_find_ollama()` checks PATH first (cross-platform), then falls back to Windows-specific paths. On Linux, Ollama install script puts it on PATH. No issue.

### M5. `os.kill(pid, signal.SIGTERM)` semantics on Windows
On Windows, `os.kill()` with `signal.SIGTERM` maps to `TerminateProcess()` (immediate kill). The "graceful then force" pattern in `process_control.py` is collapsed to "force then force" on Windows.

**Impact:** Cosmetic -- consider using `psutil.Process.terminate()` for better cross-platform behavior.

### M6. Discovery UDP broadcast may need firewall rules
**File:** `fleet/discovery.py:32-33`

UDP port 5556 for auto-discovery needs firewall exceptions on all platforms.

### M7. `setup.sh` calls `check_thermal` at module level (line 338)
**File:** `scripts/setup.sh:338`

`check_thermal` is invoked before `main()`. Not a bug but runs before `detect_os()` populates variables. Actually, it uses `$OSTYPE` which is a bash built-in, so this works but is ordering-messy.

### M8. Worker CPU affinity on Steam Deck
**File:** `fleet/process_manager.py:222`

CPU affinity is attempted on Linux (only skips macOS). On the Steam Deck's 4-core/8-thread Zen2 APU, pinning workers may conflict with SteamOS power management.

**Impact:** Potential performance degradation. Consider skipping affinity on low-core-count systems.

---

## Low (nice to have)

### L1. Federation mTLS certificate generation
Cross-platform cert generation untested between Windows and Linux peers.

### L2. `setup.sh` does not install pyamdgpuinfo on SteamOS
The setup script installs Python deps from requirements.txt but pyamdgpuinfo is not included. Steam Deck falls to SysfsBackend or NullBackend for GPU monitoring.

### L3. Model tier defaults assume NVIDIA-class VRAM
Default model tiers (8b/8b/1.7b/0.6b) assume dedicated VRAM. Steam Deck's unified memory makes these thresholds less meaningful. Consider a `unified_memory` flag in fleet.toml.

### L4. `_open_path()` helper exists but not used everywhere
`mod_manual_mode.py:570-575` reimplements the cross-platform open logic inline instead of importing `_open_path()` from installer.py. Minor code duplication.

### L5. `pywebview` Qt backend may need `qt5-webengine` on SteamOS
SteamOS ships with Qt but may not include `qt5-webengine` which pywebview needs for its Qt backend.

---

## Per-Machine Readiness

### Windows + RTX 3080 Ti (primary)
| Area | Status | Notes |
|------|--------|-------|
| GPU detection | OK | NvidiaBackend via pynvml |
| VRAM management | OK | 12GB, well within default tiers |
| CPU temp | OK | LHM REST API |
| Subprocess flags | OK | creationflags used consistently |
| Worker scaling | OK | 32GB RAM = "high" tier (20 workers) |
| Dr. Ders | OK | Full NVIDIA telemetry |
| Screenshot skill | OK | ctypes.windll.user32 |
| Federation | OK | Windows Firewall prompt needed |
| Font loading | OK | Windows GDI AddFontResourceExW |
| Signal handling | WARN | SIGTERM = TerminateProcess (no graceful) |
| **Overall** | **READY** | Primary dev target, well-tested |

### Windows + GTX 1070 (secondary)
| Area | Status | Notes |
|------|--------|-------|
| GPU detection | OK | NvidiaBackend via pynvml |
| VRAM management | WARN | 8GB -- qwen3:8b fills 86%, instant downgrade |
| CPU temp | OK | Same as primary |
| Worker scaling | Depends on RAM | Need to verify RAM amount |
| Dr. Ders | WARN | Will immediately downscale default model |
| Model tiers | NEEDS CONFIG | Set tier_default to qwen3:4b or smaller |
| **Overall** | **READY with config changes** | Needs fleet.toml model tier adjustment |

### Steam Deck OLED (Linux)
| Area | Status | Notes |
|------|--------|-------|
| GPU detection | RISK | AMD APU: SysfsBackend probable, VRAM reporting uncertain |
| VRAM management | RISK | Unified memory, sysfs may show 1-4GB carved out |
| CPU temp | OK | psutil/k10temp or psutil/amdgpu should work |
| Dr. Ders | CRITICAL | Exits if _HAS_GPU=False. May get NullBackend |
| Worker scaling | OK | 16GB RAM = "standard" tier (14 workers) |
| PyWebView | RISK | Needs Qt backend set + qt5-webengine installed |
| Font loading | DEGRADED | Custom fonts not loaded on Linux |
| Screenshot | BROKEN | Windows-only ctypes code |
| Subprocess | OK | No creationflags needed on Linux |
| Signal handling | OK | SIGTERM works correctly on Linux |
| Setup script | OK | SteamOS detection works, pacman support present |
| Ollama | OK | Standard Linux install via curl script |
| Federation | WARN | Firewall rules needed, UDP 5556 |
| **Overall** | **NOT READY** | 2-3 critical issues, 3-4 high issues |

---

## Federation Readiness (3-machine test)

| Aspect | Status | Notes |
|--------|--------|-------|
| Discovery (UDP broadcast) | WARN | Each machine needs firewall port 5556 open |
| mTLS certificates | UNTESTED | Certificate generation should be cross-platform |
| Peer heartbeat | OK | Uses urllib HTTP -- cross-platform |
| Task routing | OK | Pure Python + DB -- no platform deps |
| Naming/identity | OK | Uses `platform.node()` -- cross-platform |
| Network topology | WARN | Same subnet needed for broadcast, or manual peers |
| Mixed OS federation | UNTESTED | No known blockers but never tested Win-to-Linux |

### Federation Setup Checklist
1. Open UDP port 5556 on all 3 machines
2. Open TCP port 5555 (dashboard) on all 3 machines
3. Set `[federation] enabled = true` and `discovery_enabled = true` in each fleet.toml
4. Set unique `[naming] device_name` on each machine
5. Optionally configure `[federation] peers = [...]` with static addresses as backup
6. Generate and exchange mTLS certificates (or disable TLS for local network)

---

## Summary

| Severity | Count | Key Issues |
|----------|-------|------------|
| Critical | 4 | Dr. Ders exits on non-NVIDIA, os.kill SIGTERM semantics, killpg guard, missing creationflags |
| High | 7 | AMD APU VRAM detection, model tiers for 8GB GPU, PyWebView Linux backend, fonts, screenshot, pgrep, LHM guard |
| Medium | 8 | Guards verified OK, Popen flags mostly good, discovery firewall, affinity on Steam Deck |
| Low | 5 | mTLS untested, pyamdgpuinfo not in setup, unified memory flag, _open_path duplication, Qt deps |

### Top 5 Fixes for Linux/Steam Deck Readiness
1. **Dr. Ders:** Don't exit on NullBackend -- run in CPU-only thermal monitoring mode (psutil CPU temp still works)
2. **GPU fallback:** Add Steam Deck AMD APU detection path, handle unified memory VRAM reporting
3. **PyWebView:** Set `PYWEBVIEW_GUI=qt` on Linux when KDE/Qt is detected
4. **Model tiers:** Add per-GPU-profile tier overrides (8GB, 4GB, unified memory presets)
5. **Screenshot skill:** Add Linux fallback using `scrot`, `grim` (Wayland), or `xdg-screenshot`
