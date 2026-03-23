# r/LocalLLaMA Post Draft

**Title:** Built an open plugin for managing local model lifecycles (Ollama/llama.cpp/vLLM) — looking for feedback before I call it v1.0

---

Been building a local-first AI fleet manager called BigEd CC — 92 skills, dual-supervisor architecture (Dr. Ders handles thermals/VRAM, separate supervisor handles worker processes), runs on Ollama + Claude/Gemini fallback.

Along the way I ended up extracting all the model management patterns into a standalone Claude Code plugin called **model-manager**. It's framework-agnostic and covers:

- **Three-tier model architecture** — small (heartbeat/CPU, always-on), medium (conductor/CPU, never evicted), large (GPU, evictable)
- **Thermal-aware scaling** — park-and-guard governor that steps down one tier at a time with anti-thrash cooldown. GPU sustained/burst/emergency thresholds, VRAM pressure thresholds, CPU thermal limits. All configurable, never hardcoded.
- **VRAM-aware device routing** — pre-load headroom check, multi-GPU topology discovery, best-fit placement, LRU eviction of non-pinned models, OOM fallback to CPU
- **Zombie handle cleanup** — three-layer detection (health ping, process liveness, TTL+refcount), forced cleanup with CUDA cache flush, automatic reload of pinned supervisor tiers
- **Circuit breaker for providers** — per-provider failure counting, exponential backoff cooldown (60s→120s→240s...cap 600s), HA fallback chain
- **Cost-aware routing** — skill complexity classification (simple/medium/complex), budget enforcement (warn/throttle/block modes), quality-gated premium lock

13-category audit checklist, 14 documented anti-patterns with detection hints, framework-specific cleanup routines for PyTorch, Ollama, llama.cpp, and vLLM.

The thermal governor is the piece I'm most proud of — Dr. Ders in BigEd parks on the configured model and only scales down under pressure, one tier at a time, with a 120s cooldown between swaps. No auto-upscale (operator decision only). Came out of getting burned by thrashing when temps hovered near thresholds.

**What I'm looking for:**

- Are there edge cases in the VRAM routing I'm not handling? Especially around fragmentation after repeated load/unload cycles
- Anyone running mixed vendor GPUs (NVIDIA + AMD on same box)? The plugin has vendor abstraction but I've only tested NVIDIA
- Is the zombie detection approach overkill or underkill? Three layers felt right but I'm open to hearing otherwise
- Multi-GPU users — is best-fit-by-free-VRAM the right default, or do you prefer round-robin / affinity-based?

Plugin repo: [link]
BigEd: https://github.com/SwiftWing21/BigEd

Happy to take it apart. Roast welcome.
