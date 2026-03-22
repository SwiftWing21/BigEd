"""BigEd CC — First-run walkthrough dialog and system detection.

Extracted from launcher.py (TECH_DEBT 4.2).
"""
from datetime import datetime

import customtkinter as ctk
import psutil
import tomlkit

from ui.theme import (
    BG, BG2, BG3, ACCENT, ACCENT_H, GOLD, TEXT, DIM,
    GREEN, ORANGE, RED, FONT,
)

# Late-bound: set by launcher.py after path resolution
HERE = None
FLEET_TOML = None
_GPU_OK = False
_GPU_HANDLE = None
pynvml = None
KeyManagerDialog = None  # reference to ui.settings.keys.KeyManagerDialog


def _init_walkthrough_refs(here, fleet_toml, gpu_ok, gpu_handle, _pynvml,
                           key_manager_cls=None):
    """Called once from launcher.py to inject refs without circular imports."""
    global HERE, FLEET_TOML, _GPU_OK, _GPU_HANDLE, pynvml, KeyManagerDialog
    HERE = here
    FLEET_TOML = fleet_toml
    _GPU_OK = gpu_ok
    _GPU_HANDLE = gpu_handle
    pynvml = _pynvml
    KeyManagerDialog = key_manager_cls


# ── System detection & profile logic ──────────────────────────────────────────

def _detect_system_profile():
    """Probe hardware and return a profile dict with recommended settings."""
    ram_gb = round(psutil.virtual_memory().total / (1024 ** 3), 1)
    cpu_cores = psutil.cpu_count(logical=False) or psutil.cpu_count() or 2

    gpu_name = None
    vram_gb = 0.0
    if _GPU_OK:
        try:
            gpu_name = pynvml.nvmlDeviceGetName(_GPU_HANDLE)
            if isinstance(gpu_name, bytes):
                gpu_name = gpu_name.decode()
            mem = pynvml.nvmlDeviceGetMemoryInfo(_GPU_HANDLE)
            vram_gb = round(mem.total / (1024 ** 3), 1)
        except Exception:
            pass

    has_gpu = vram_gb >= 2.0
    if has_gpu and vram_gb >= 8.0 and ram_gb >= 16:
        tier = "full"
    elif has_gpu and vram_gb >= 4.0 and ram_gb >= 12:
        tier = "standard"
    elif ram_gb >= 8:
        tier = "light"
    else:
        tier = "minimal"

    profiles = {
        "minimal": {
            "eco_mode": True,
            "max_workers": min(3, max(2, cpu_cores // 2)),
            "model": "qwen3:0.6b",
            "conductor_model": "qwen3:0.6b",
            "model_tiers": {"default": "qwen3:0.6b", "mid": "qwen3:0.6b",
                            "low": "qwen3:0.6b", "critical": "qwen3:0.6b"},
        },
        "light": {
            "eco_mode": not has_gpu,
            "max_workers": min(5, max(3, cpu_cores // 2)),
            "model": "qwen3:1.7b" if not has_gpu else "qwen3:4b",
            "conductor_model": "qwen3:0.6b",
            "model_tiers": {"default": "qwen3:1.7b" if not has_gpu else "qwen3:4b",
                            "mid": "qwen3:1.7b", "low": "qwen3:0.6b",
                            "critical": "qwen3:0.6b"},
        },
        "standard": {
            "eco_mode": False,
            "max_workers": min(7, max(4, cpu_cores - 2)),
            "model": "qwen3:4b" if vram_gb < 6 else "qwen3:8b",
            "conductor_model": "qwen3:0.6b",
            "model_tiers": {"default": "qwen3:4b" if vram_gb < 6 else "qwen3:8b",
                            "mid": "qwen3:4b", "low": "qwen3:1.7b",
                            "critical": "qwen3:0.6b"},
        },
        "full": {
            "eco_mode": False,
            "max_workers": min(10, max(5, cpu_cores - 2)),
            "model": "qwen3:8b",
            "conductor_model": "qwen3:4b",
            "model_tiers": {"default": "qwen3:8b", "mid": "qwen3:8b",
                            "low": "qwen3:1.7b", "critical": "qwen3:0.6b"},
        },
    }

    rec = profiles[tier]
    return {
        "ram_gb": ram_gb, "cpu_cores": cpu_cores,
        "gpu_name": gpu_name, "vram_gb": vram_gb,
        "tier": tier, **rec,
    }


def _apply_system_profile(profile: dict):
    """Write auto-detected profile recommendations to fleet.toml."""
    try:
        doc = tomlkit.parse(FLEET_TOML.read_text(encoding="utf-8"))
        fleet = doc.setdefault("fleet", {})
        fleet["eco_mode"] = profile["eco_mode"]
        fleet["max_workers"] = profile["max_workers"]

        models = doc.setdefault("models", {})
        models["local"] = profile["model"]
        models["complex"] = profile["model"]
        models["conductor_model"] = profile["conductor_model"]

        tiers = models.setdefault("tiers", {})
        for k, v in profile["model_tiers"].items():
            tiers[k] = v

        gpu = doc.setdefault("gpu", {})
        gpu["mode"] = "full" if not profile["eco_mode"] else "eco"

        detected = doc.setdefault("system_detected", {})
        detected["ram_gb"] = profile["ram_gb"]
        detected["cpu_cores"] = profile["cpu_cores"]
        detected["gpu_name"] = profile.get("gpu_name") or "none"
        detected["vram_gb"] = profile["vram_gb"]
        detected["tier"] = profile["tier"]
        detected["detected_at"] = datetime.now().isoformat(timespec="seconds")

        FLEET_TOML.write_text(tomlkit.dumps(doc), encoding="utf-8")
        return True
    except Exception:
        return False


def _should_show_walkthrough() -> bool:
    """Check if walkthrough should be shown on launch."""
    try:
        import tomllib
        with open(FLEET_TOML, "rb") as f:
            data = tomllib.load(f)
        return not data.get("walkthrough", {}).get("completed", False)
    except Exception:
        return True


# ── Walkthrough dialog ────────────────────────────────────────────────────────

class WalkthroughDialog(ctk.CTkToplevel):
    """First-run walkthrough — 7-step guided setup with skip/skip-all."""

    STEPS = [
        {
            "title": "Welcome to BigEd CC",
            "desc": (
                "BigEd CC is an autonomous AI agent fleet that runs entirely on your machine.\n\n"
                "Your fleet has 74 skills across 10+ specialized worker roles — researchers, "
                "coders, analysts, security auditors, and more — coordinated by a dual-supervisor "
                "system. Workers share a task queue, communicate via messages, and build "
                "a local knowledge base over time.\n\n"
                "Everything stays private. Nothing leaves your machine unless you enable cloud AI.\n\n"
                "This walkthrough will help you get set up. You can skip any step."
            ),
        },
        {
            "title": "System Detection",
            "desc": (
                "BigEd CC can detect your hardware and auto-adjust settings for the best "
                "experience on your system.\n\n"
                "This checks your RAM, CPU cores, and GPU/VRAM to set the right model size, "
                "worker count, and eco mode. It is especially important for systems with less "
                "than 8 GB RAM or no dedicated GPU.\n\n"
                "Click 'Detect & Adjust' to scan your hardware now, or skip to use defaults."
            ),
            "action_label": "Detect & Adjust",
            "has_auto_detect": True,
        },
        {
            "title": "API Keys (Optional)",
            "desc": (
                "BigEd CC works fully offline with local models. Cloud AI is optional.\n\n"
                "If you add API keys, the fleet gains access to Claude, Gemini, and web search "
                "(Brave, Tavily). The system uses intelligent fallback: Claude > Gemini > Local, "
                "so if one provider is down, tasks route to the next automatically.\n\n"
                "Keys are stored in ~/.secrets (never committed to git).\n"
                "You can manage them anytime from Settings > Key Manager."
            ),
            "action_label": "Open Key Manager",
        },
        {
            "title": "Fleet Profile",
            "desc": (
                "Choose a deployment profile that matches your use case:\n\n"
                "  minimal    — Ingestion + Outputs (lightweight, personal use)\n"
                "  research   — Same + RAG pipeline and research workflows\n"
                "  consulting — CRM, Onboarding, Customers, Accounts + research\n"
                "  full       — All modules enabled\n\n"
                "You can change your profile anytime in Settings. Modules can also be "
                "toggled individually from the sidebar."
            ),
        },
        {
            "title": "Ollama Setup",
            "desc": (
                "Ollama is the local AI engine that powers your fleet workers.\n\n"
                "The default model is qwen3:8b (~6.9 GB VRAM). Dr. Ders, the hardware "
                "supervisor, monitors your GPU in real-time and automatically scales between "
                "model tiers if needed:\n\n"
                "  Default  — qwen3:8b (best quality, needs GPU)\n"
                "  Low      — qwen3:1.7b (lighter, less VRAM)\n"
                "  Critical — qwen3:0.6b (CPU-only fallback)\n\n"
                "No GPU? Enable Eco Mode in fleet.toml to run everything on CPU."
            ),
        },
        {
            "title": "Dispatch Your First Task",
            "desc": (
                "Use the task bar at the bottom of the main window to dispatch work.\n\n"
                "Try one of these:\n"
                '  summarize — "Summarize the key ideas in transformer architecture"\n'
                '  web_search — "Find recent open-source local AI projects"\n'
                '  flashcard — "Create flashcards on Python async patterns"\n\n'
                "The fleet automatically routes each task to the best-matching worker "
                "based on skill affinity. Results appear in the Knowledge tab."
            ),
        },
        {
            "title": "Consoles & Fleet Comm",
            "desc": (
                "BigEd CC has 3 interactive consoles in the sidebar:\n\n"
                "  Claude Console — Cloud AI (Anthropic API key required)\n"
                "  Gemini Console — Cloud AI (Google API key required)\n"
                "  Local Console  — Free, powered by Ollama\n\n"
                "Each console can dispatch fleet tasks mid-conversation.\n\n"
                "Fleet Comm (bottom panel) shows real-time worker activity, task results, "
                "and system events. You're all set — close this to start using BigEd CC."
            ),
        },
    ]

    def __init__(self, parent):
        super().__init__(parent)
        self.title("BigEd CC — Setup Walkthrough")
        self.geometry("560x480")
        self.resizable(False, False)
        self.configure(fg_color=BG2)
        self.grab_set()
        self.lift()
        self._parent = parent
        self._step = 0
        self._skipped = []
        self._detected_profile = None

        if HERE:
            ico = HERE / "brick.ico"
            if ico.exists():
                try: self.iconbitmap(str(ico))
                except Exception: pass

        self._build_ui()
        self._show_step()

    def _build_ui(self):
        self._progress = ctk.CTkProgressBar(self, width=520, height=6,
                                             fg_color=BG3, progress_color=ACCENT)
        self._progress.pack(padx=20, pady=(16, 0))

        self._step_label = ctk.CTkLabel(self, text="", font=("RuneScape Plain 11", 10),
                                         text_color=DIM)
        self._step_label.pack(pady=(4, 0))

        self._title = ctk.CTkLabel(self, text="", font=("RuneScape Bold 12", 16, "bold"),
                                    text_color=GOLD)
        self._title.pack(padx=20, pady=(12, 0), anchor="w")

        self._desc = ctk.CTkLabel(self, text="", font=FONT, text_color=TEXT,
                                   wraplength=520, justify="left", anchor="nw")
        self._desc.pack(padx=20, pady=(8, 0), fill="both", expand=True, anchor="nw")

        self._detect_result = ctk.CTkLabel(self, text="", font=("Consolas", 10),
                                            text_color=DIM, wraplength=520,
                                            justify="left", anchor="nw")

        self._action_btn = ctk.CTkButton(self, text="", height=30,
                                          fg_color=ACCENT, hover_color=ACCENT_H,
                                          command=self._on_action)

        bottom = ctk.CTkFrame(self, fg_color=BG3, height=54, corner_radius=0)
        bottom.pack(side="bottom", fill="x")
        bottom.pack_propagate(False)

        ctk.CTkButton(bottom, text="Skip All", width=80, height=30,
                      fg_color=BG, hover_color=BG2, text_color=DIM,
                      command=self._skip_all).pack(side="left", padx=12, pady=12)

        self._no_show_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(bottom, text="Don't show again", variable=self._no_show_var,
                        font=("RuneScape Plain 11", 9), text_color=DIM,
                        fg_color=ACCENT, checkmark_color=TEXT,
                        checkbox_width=16, checkbox_height=16,
                        ).pack(side="left", padx=(8, 0), pady=12)

        self._next_btn = ctk.CTkButton(bottom, text="Next →", width=90, height=30,
                                        fg_color=ACCENT, hover_color=ACCENT_H,
                                        command=self._next)
        self._next_btn.pack(side="right", padx=12, pady=12)

        self._skip_btn = ctk.CTkButton(bottom, text="Skip", width=70, height=30,
                                        fg_color=BG, hover_color=BG2, text_color=DIM,
                                        command=self._skip_step)
        self._skip_btn.pack(side="right", padx=(0, 4), pady=12)

    def _show_step(self):
        total = len(self.STEPS)
        step = self.STEPS[self._step]
        self._progress.set((self._step + 1) / total)
        self._step_label.configure(text=f"Step {self._step + 1} of {total}")
        self._title.configure(text=step["title"])

        desc = step["desc"]
        if step["title"] == "Ollama Setup" and self._detected_profile:
            p = self._detected_profile
            if p["eco_mode"]:
                desc = (
                    "Ollama is the local AI engine that powers your fleet workers.\n\n"
                    f"Based on your system ({p['ram_gb']} GB RAM, "
                    f"{'no GPU' if not p['gpu_name'] else p['gpu_name']}), "
                    f"Eco Mode has been enabled with {p['model']} as the default model.\n\n"
                    "Dr. Ders, the hardware supervisor, monitors your system in real-time "
                    "and will scale between model tiers automatically if needed.\n\n"
                    "You can adjust these settings later in fleet.toml."
                )
            elif p["tier"] != "full":
                desc = (
                    "Ollama is the local AI engine that powers your fleet workers.\n\n"
                    f"Based on your system ({p['ram_gb']} GB RAM, "
                    f"{p['gpu_name']} with {p['vram_gb']} GB VRAM), "
                    f"the default model has been set to {p['model']}.\n\n"
                    "Dr. Ders, the hardware supervisor, monitors your GPU in real-time "
                    "and automatically scales between model tiers if needed.\n\n"
                    "You can adjust these settings later in fleet.toml."
                )
        self._desc.configure(text=desc)

        if step.get("has_auto_detect") and self._detected_profile:
            self._detect_result.pack(padx=20, pady=(6, 0), anchor="w")
        else:
            self._detect_result.pack_forget()

        if "action_label" in step:
            self._action_btn.configure(text=step["action_label"])
            self._action_btn.pack(padx=20, pady=(8, 0), anchor="w")
        else:
            self._action_btn.pack_forget()

        if self._step == total - 1:
            self._next_btn.configure(text="Finish ✓")
            self._skip_btn.pack_forget()
        else:
            self._next_btn.configure(text="Next →")

    def _next(self):
        if self._step >= len(self.STEPS) - 1:
            self._finish()
        else:
            self._step += 1
            self._show_step()

    def _skip_step(self):
        self._skipped.append(self._step + 1)
        self._next()

    def _skip_all(self):
        self._skipped.extend(range(self._step + 1, len(self.STEPS) + 1))
        self._finish()

    def _on_action(self):
        step = self.STEPS[self._step]
        if step.get("action_label") == "Open Key Manager":
            if KeyManagerDialog:
                try:
                    KeyManagerDialog(self._parent)
                except Exception:
                    pass
        elif step.get("action_label") == "Detect & Adjust":
            self._run_auto_detect()

    def _run_auto_detect(self):
        self._action_btn.configure(state="disabled", text="Detecting...")
        self.update_idletasks()
        try:
            profile = _detect_system_profile()
            self._detected_profile = profile

            gpu_str = (f"{profile['gpu_name']} ({profile['vram_gb']} GB VRAM)"
                       if profile["gpu_name"] else "None detected")
            tier_labels = {"minimal": "Minimal", "light": "Light",
                           "standard": "Standard", "full": "Full"}

            result_text = (
                f"  RAM: {profile['ram_gb']} GB  |  CPU: {profile['cpu_cores']} cores  |  GPU: {gpu_str}\n"
                f"  Tier: {tier_labels.get(profile['tier'], profile['tier'])}\n"
                f"  Model: {profile['model']}  |  Workers: {profile['max_workers']}"
                f"  |  Eco: {'ON' if profile['eco_mode'] else 'OFF'}"
            )

            applied = _apply_system_profile(profile)
            if applied:
                result_text += "\n  Settings applied to fleet.toml"
                self._detect_result.configure(text=result_text, text_color=GREEN)
            else:
                result_text += "\n  Could not write to fleet.toml — apply manually"
                self._detect_result.configure(text=result_text, text_color=ORANGE)

            self._detect_result.pack(padx=20, pady=(6, 0), anchor="w")
            self._action_btn.configure(text="Re-detect", state="normal")
        except Exception as e:
            self._detect_result.configure(
                text=f"  Detection failed: {e}", text_color=RED)
            self._detect_result.pack(padx=20, pady=(6, 0), anchor="w")
            self._action_btn.configure(text="Retry", state="normal")

    def _finish(self):
        if self._no_show_var.get():
            self._persist_completed()
        self.destroy()

    def _persist_completed(self):
        try:
            doc = tomlkit.parse(FLEET_TOML.read_text(encoding="utf-8"))
            now = datetime.now().isoformat(timespec="seconds")
            wt = doc.setdefault("walkthrough", {})
            wt["completed"] = True
            wt["skipped_steps"] = list(self._skipped) if self._skipped else []
            wt["completed_at"] = now
            FLEET_TOML.write_text(tomlkit.dumps(doc), encoding="utf-8")
        except Exception:
            pass
        # Also persist to settings.json so the VS Code README gate can check it
        try:
            import launcher as _mod
            data = _mod._load_settings()
            data["walkthrough_completed"] = True
            _mod._save_settings(data)
        except Exception:
            pass
