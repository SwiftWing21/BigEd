"""
BigEd CC — Ollama management mixin.
Extracted from launcher.py to reduce god-object complexity (TECH_DEBT 4.2).

Provides an OllamaManagerMixin that is mixed into BigEdCC:

Status:
  _poll_ollama, _is_ollama_running, _apply_ollama_status, _ollama_status

Control:
  _run_ollama_start, _start_ollama, _stop_ollama

Models:
  _populate_model_dropdown, _quick_model_switch, _ollama_script

Health:
  _on_ollama_recovered, _recover_offline_agents, _schedule_ollama_watch,
  _send_keepalive

Strategy:
  _apply_strategy, _get_complex_provider, _toggle_claude_research,
  _is_eco_mode, _is_training_active
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import psutil

from ui.theme import GREEN, ORANGE, RED, DIM

# ─── Lazy imports from launcher ──────────────────────────────────────────────

def _launcher():
    """Return the launcher module (import once, cache)."""
    import launcher as _mod
    return _mod


class OllamaManagerMixin:
    """Mixin providing Ollama status, control, model switching, health
    watchdog, and fleet strategy presets for BigEdCC."""

    # ── Ollama status + watchdog ─────────────────────────────────────────

    def _poll_ollama(self) -> tuple:
        """
        Check Ollama API. Returns (up, detail, model_loaded).
        detail format: "model GPU(queued) VRAM | conductor" or similar.
        Reads hw_state.json for conductor status when available.
        """
        L = _launcher()
        if not L.ollama_is_running():
            return False, "not reachable", False

        # Get queued task count from fleet.db
        from data_access import FleetDB
        queued = FleetDB.queued_task_count(L.FLEET_DIR / "fleet.db")
        queue_str = f"({queued})" if queued else ""

        # Determine CPU/GPU mode
        eco = self._is_eco_mode()
        mode_str = "CPU" if eco else "GPU"

        # Read conductor status from hw_state.json (written by hw_supervisor)
        conductor_suffix = ""
        try:
            if L.HW_STATE_JSON.exists():
                hw = json.loads(L.HW_STATE_JSON.read_text(encoding="utf-8"))
                cs = hw.get("conductor", "")
                if cs == "loaded":
                    conductor_suffix = " +chat"
                elif cs == "unloaded":
                    conductor_suffix = " -chat"
        except Exception:
            pass

        # Server is up — check if a model is currently loaded in VRAM
        ps_data = L.ollama_ps()
        if ps_data is not None:
            models = ps_data.get("models", [])
            if models:
                names = [m["name"].split(":")[0] for m in models]
                vram_str = ""
                if L._ensure_gpu() and not eco:
                    try:
                        mem = L._pynvml.nvmlDeviceGetMemoryInfo(L._GPU_HANDLE)
                        vram_str = f" {mem.used/1e9:.1f}GB"
                    except Exception:
                        pass
                model_list = "+".join(names) if len(names) <= 2 else f"{names[0]}+{len(names)-1}"
                return True, f"{model_list} {mode_str}{queue_str}{vram_str}{conductor_suffix}", True
            else:
                return True, f"idle {mode_str}{queue_str} — unloaded{conductor_suffix}", False
        return True, f"up {mode_str}{queue_str}{conductor_suffix}", False

    def _is_ollama_running(self) -> bool:
        """Check if Ollama is running via HTTP API (cross-platform)."""
        L = _launcher()
        return L.ollama_is_running()

    def _apply_ollama_status(self, up: bool, detail: str, loaded: bool = True):
        if up and loaded:
            self._ollama_dot.configure(text="\u25cf", text_color=GREEN)
            self._ollama_lbl.configure(text=detail, text_color=DIM)
            self._ollama_restart_count = 0
        elif up:
            self._ollama_dot.configure(text="\u25cf", text_color=ORANGE)
            self._ollama_lbl.configure(text=detail, text_color=ORANGE)
            self._ollama_restart_count = 0
        else:
            self._ollama_dot.configure(text="\u25cf", text_color=RED)
            self._ollama_lbl.configure(text="offline", text_color=RED)

        # Watchdog: was up, now down -> auto-relaunch + recover workers (max 3)
        # Suppressed when the user deliberately stopped the system.
        if self._ollama_up is True and not up and not self._system_intentional_stop:
            if self._ollama_restart_count >= 3:
                self._log_output("Ollama offline \u2014 restart cap reached (3). Restart manually.")
                self._ollama_lbl.configure(text="offline (restart cap)", text_color=RED)
            else:
                self._ollama_restart_count += 1
                self._log_output(
                    f"Ollama went offline \u2014 relaunching (attempt {self._ollama_restart_count}/3)..."
                )
                self._ollama_lbl.configure(text="relaunching...", text_color=ORANGE)
                self._ollama_dot.configure(text_color=ORANGE)
                self._run_ollama_start(
                    lambda o, e: self._on_ollama_recovered(o, e)
                )

        self._ollama_up = up

    def _ollama_status(self):
        def _bg():
            L = _launcher()
            data = L.ollama_tags()
            if data:
                models = [m["name"] for m in data.get("models", [])]
                msg = f"Ollama running\nModels: {', '.join(models)}"
            else:
                msg = "Ollama not running"
            self._safe_after(0, lambda: self._log_output(msg))
        threading.Thread(target=_bg, daemon=True).start()

    # ── Ollama control ───────────────────────────────────────────────────

    def _run_ollama_start(self, callback=None):
        """Start Ollama natively on Windows, poll until responsive."""
        import shutil
        def _run():
            try:
                # Already running?
                if self._is_ollama_running():
                    if callback:
                        callback("Ollama already running", "")
                    return
                # Find ollama executable
                ollama_exe = shutil.which("ollama")
                if not ollama_exe and sys.platform == "win32":
                    for _p in [
                        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
                        Path(os.environ.get("LOCALAPPDATA", "")) / "Ollama" / "ollama.exe",
                        Path(os.environ.get("PROGRAMFILES", "")) / "Ollama" / "ollama.exe",
                    ]:
                        if _p.exists():
                            ollama_exe = str(_p)
                            break
                if not ollama_exe:
                    if callback:
                        callback("", "ollama not found \u2014 install from https://ollama.com")
                    return
                # Set eco mode env if needed
                env = os.environ.copy()
                if self._is_eco_mode():
                    env["CUDA_VISIBLE_DEVICES"] = "-1"
                # Launch ollama serve as background process
                subprocess.Popen(
                    [ollama_exe, "serve"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=env,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
                )
                # Poll until responsive (30s)
                for _ in range(15):
                    if self._is_ollama_running():
                        if callback:
                            callback("Ollama started OK", "")
                        return
                    time.sleep(2)
                if callback:
                    callback("", "Ollama start timed out")
            except Exception as e:
                if callback:
                    callback("", str(e))
        threading.Thread(target=_run, daemon=True).start()

    def _start_ollama(self):
        self._log_output("Starting Ollama...")
        self._run_ollama_start(
            lambda o, e: self._safe_after(0, lambda: self._log_output(o or e or "Ollama start attempted"))
        )

    def _stop_ollama(self):
        self._log_output("Stopping Ollama...")
        from ui.boot import _kill_ollama
        def _bg():
            result = _kill_ollama()
            msg = "Ollama stopped" if result else "Ollama not running"
            self._safe_after(0, lambda: self._log_output(msg))
        threading.Thread(target=_bg, daemon=True).start()

    # ── Model management ─────────────────────────────────────────────────

    def _populate_model_dropdown(self):
        """Fetch installed Ollama models and update the dropdown values."""
        def _fetch():
            try:
                L = _launcher()
                host = L.load_model_cfg().get("ollama_host", "http://localhost:11434")
                with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as r:
                    data = json.loads(r.read())
                models = sorted(set(m["name"] for m in data.get("models", [])))
                if models:
                    self._safe_after(0, lambda: self._model_switch.configure(values=models))
            except Exception:
                pass
        threading.Thread(target=_fetch, daemon=True).start()

    def _quick_model_switch(self, model_name):
        """Switch the active Ollama model from the Command Center dropdown."""
        self._log_output(f"Switching to {model_name}...")

        def _switch():
            try:
                L = _launcher()
                host = L.load_model_cfg().get("ollama_host", "http://localhost:11434")
                # Unload current model
                try:
                    with urllib.request.urlopen(f"{host}/api/ps", timeout=3) as r:
                        data = json.loads(r.read())
                    for m in data.get("models", []):
                        body = json.dumps({"model": m["name"], "keep_alive": 0}).encode()
                        req = urllib.request.Request(f"{host}/api/generate", data=body,
                              method="POST", headers={"Content-Type": "application/json"})
                        urllib.request.urlopen(req, timeout=5)
                except Exception:
                    pass
                # Load new model on GPU
                body = json.dumps({"model": model_name, "prompt": "", "keep_alive": "30m",
                                   "options": {"num_gpu": 99}}).encode()
                req = urllib.request.Request(f"{host}/api/generate", data=body,
                      method="POST", headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=60)
                # Update fleet.toml
                toml_path = L.FLEET_TOML
                text = toml_path.read_text(encoding="utf-8")
                text = re.sub(r'^(\s*local\s*=\s*).*$', rf'\1"{model_name}"', text, flags=re.MULTILINE)
                toml_path.write_text(text, encoding="utf-8")
                self._safe_after(0, lambda: self._log_output(f"Switched to {model_name} (GPU)"))
            except Exception as e:
                self._safe_after(0, lambda: self._log_output(f"Switch failed: {e}"))
        threading.Thread(target=_switch, daemon=True).start()

    def _ollama_script(self) -> str:
        """Build the ollama-start bash script content (WSL fallback only)."""
        prefix = "CUDA_VISIBLE_DEVICES=-1 " if self._is_eco_mode() else ""
        return (
            "#!/bin/bash\n"
            "curl -sf http://localhost:11434/api/tags > /dev/null"
            " && echo 'Ollama already running' && exit 0\n"
            f"nohup {prefix}ollama serve >> /tmp/ollama.log 2>&1 &\n"
            "disown\n"
            "for i in $(seq 1 15); do\n"
            "    curl -sf http://localhost:11434/api/tags > /dev/null"
            " && echo 'Ollama started OK' && exit 0\n"
            "    sleep 2\n"
            "done\n"
            "echo 'Ollama start timed out - check /tmp/ollama.log'\n"
        )

    # ── Health / watchdog ────────────────────────────────────────────────

    def _on_ollama_recovered(self, out: str, err: str):
        self._log_output(f"Ollama restarted: {out or err}")
        # Give workers time to detect the new Ollama instance, then recover offline ones
        self._safe_after(4000, self._recover_offline_agents)

    def _recover_offline_agents(self):
        """Restart any agents currently showing as OFFLINE."""
        L = _launcher()
        status = L.parse_status()
        seen = {a["name"] for a in status.get("agents", [])}
        all_roles = ["researcher", "coder", "archivist", "analyst",
                     "sales", "onboarding", "implementation", "security"]
        offline = [r for r in all_roles if r not in seen]
        if offline:
            self._log_output(f"Auto-recovering offline agents: {', '.join(offline)}")
            for role in offline:
                self._recover_agent(role)

    def _schedule_ollama_watch(self):
        try:
            def _check():
                try:
                    up, detail, loaded = self._poll_ollama()
                    # Keepalive: GPU mode, no training, model loaded, every 4 min
                    if up and loaded and not self._is_eco_mode():
                        now = time.time()
                        if now - self._last_keepalive >= 240:
                            model = detail.split()[0] if detail else ""
                            if model and model != "up":
                                self._send_keepalive(model)
                                self._last_keepalive = now
                    self._safe_after(0, lambda: self._apply_ollama_status(up, detail, loaded))
                except Exception:
                    pass
            threading.Thread(target=_check, daemon=True).start()
        except Exception as e:
            self._log_output(f"Ollama watch error: {e}")
        finally:
            self._safe_after(8000, self._schedule_ollama_watch)

    def _send_keepalive(self, model: str):
        """Ping Ollama with keep_alive=-1 to prevent model unload."""
        L = _launcher()
        L.ollama_keepalive(model)

    # ── Strategy presets ─────────────────────────────────────────────────

    def _apply_strategy(self, strategy):
        """Apply a fleet strategy preset."""
        L = _launcher()
        STRATEGIES = {
            "performance": {
                "model": "qwen3:8b", "max_workers": 10, "eco_mode": False,
                "idle_enabled": True, "desc": "Max speed \u2014 8b on GPU, full fleet",
            },
            "balanced": {
                "model": "qwen3:8b", "max_workers": 6, "eco_mode": False,
                "idle_enabled": True, "desc": "Standard \u2014 8b on GPU, balanced workers",
            },
            "training": {
                "model": "qwen3:4b", "max_workers": 4, "eco_mode": False,
                "idle_enabled": False, "desc": "Training mode \u2014 4b on GPU, VRAM for autoresearch",
            },
            "eco": {
                "model": "qwen3:0.6b", "max_workers": 2, "eco_mode": True,
                "idle_enabled": False, "desc": "Eco mode \u2014 minimal power, smallest model",
            },
        }
        preset = STRATEGIES.get(strategy)
        if not preset:
            return
        self._log_output(f"Strategy: {strategy} \u2014 {preset['desc']}")
        # Switch model
        self._model_switch_var.set(preset["model"])
        self._quick_model_switch(preset["model"])
        # Update fleet.toml settings
        try:
            text = L.FLEET_TOML.read_text(encoding="utf-8")
            text = re.sub(r'^(\s*max_workers\s*=\s*).*$', rf"\1{preset['max_workers']}", text, flags=re.MULTILINE)
            text = re.sub(r'^(\s*eco_mode\s*=\s*).*$', rf"\1{'true' if preset['eco_mode'] else 'false'}", text, flags=re.MULTILINE)
            text = re.sub(r'^(\s*idle_enabled\s*=\s*).*$', rf"\1{'true' if preset['idle_enabled'] else 'false'}", text, flags=re.MULTILINE)
            L.FLEET_TOML.write_text(text, encoding="utf-8")
        except Exception:
            pass

    def _get_complex_provider(self) -> str:
        try:
            L = _launcher()
            text = L.FLEET_TOML.read_text(encoding="utf-8")
            m = re.search(r'^complex_provider\s*=\s*["\']([^"\']+)["\']', text, re.M)
            return m.group(1) if m else "local"
        except Exception:
            return "local"

    def _toggle_claude_research(self):
        L = _launcher()
        use_claude = self._claude_research_var.get()
        try:
            import tomlkit
            doc = tomlkit.parse(L.FLEET_TOML.read_text(encoding="utf-8"))
            models = doc.setdefault("models", {})
            if use_claude:
                # Read the configured Claude model (last saved via Claude console or model selector)
                claude_model = models.get("claude_model", "claude-sonnet-4-6")
                provider  = "claude"
                complex_v = claude_model
            else:
                local_model = models.get("local", "qwen3:8b")
                provider  = "local"
                complex_v = local_model
            models["complex_provider"] = provider
            models["complex"] = complex_v
            L.FLEET_TOML.write_text(tomlkit.dumps(doc), encoding="utf-8")
            state = f"Claude ({complex_v})" if use_claude else f"local ({complex_v})"
            self._log_output(f"Research decisions \u2192 {state}  (fleet picks up on next task)")
        except Exception as e:
            self._log_output(f"Could not update fleet.toml: {e}")
            self._claude_research_var.set(not use_claude)  # revert checkbox on failure

    def _is_eco_mode(self) -> bool:
        try:
            L = _launcher()
            text = L.FLEET_TOML.read_text(encoding="utf-8")
            m = re.search(r'^eco_mode\s*=\s*(true|false)', text, re.M | re.I)
            return m.group(1).lower() == "true" if m else False
        except Exception:
            return False

    def _is_training_active(self) -> bool:
        try:
            return any('train.py' in ' '.join(p.info.get('cmdline') or [])
                       for p in psutil.process_iter(['cmdline']))
        except Exception:
            return False
