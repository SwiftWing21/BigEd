# Model Backend Abstraction — Design Spec
**Date:** 2026-03-31
**Status:** Spec only — no implementation
**Supersedes:** Inline Ollama coupling in `fleet/providers.py`

---

## Problem

`providers.py` and `hw_supervisor.py` are tightly coupled to Ollama's REST API.
Every call to load/unload/generate goes through Ollama-specific HTTP calls.
Adding vLLM, llama.cpp server, or LM Studio requires forking logic in multiple files.

---

## Goal

Define a `LocalModelManager` ABC that all local inference backends implement.
`providers.py` routes calls through the ABC; backends are pluggable.

---

## ABC Definition

```python
# fleet/local_model_manager.py
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class ModelInfo:
    name: str
    size_gb: float
    vram_used_gb: float
    is_loaded: bool
    quantization: Optional[str] = None


@dataclass
class GenerateResult:
    text: str
    input_tokens: int
    output_tokens: int
    tokens_per_sec: float
    model: str
    provider: str


class LocalModelManager(ABC):
    """Abstract base for local LLM inference backends."""

    @abstractmethod
    def load_model(self, model_name: str, keep_alive: int = 300) -> bool:
        """Pre-load model into VRAM. Returns True on success."""
        ...

    @abstractmethod
    def unload_model(self, model_name: str) -> bool:
        """Evict model from VRAM (keep_alive=0). Returns True on success."""
        ...

    @abstractmethod
    def list_models(self) -> list[ModelInfo]:
        """Return all available models (disk + VRAM state)."""
        ...

    @abstractmethod
    def get_status(self) -> dict:
        """Return backend health dict: running, url, version, loaded_count."""
        ...

    @abstractmethod
    def generate(
        self,
        model: str,
        system: str,
        user: str,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 120,
    ) -> GenerateResult:
        """Run a single inference call. Raises on failure."""
        ...
```

---

## Concrete Implementations

### OllamaManager (current — refactor, not rewrite)

```python
# fleet/backends/ollama_manager.py
import json
import urllib.request
from local_model_manager import LocalModelManager, ModelInfo, GenerateResult


class OllamaManager(LocalModelManager):
    def __init__(self, host: str = "http://localhost:11434"):
        self._host = host.rstrip("/")

    def load_model(self, model_name: str, keep_alive: int = 300) -> bool:
        payload = json.dumps({"model": model_name, "keep_alive": keep_alive}).encode()
        try:
            with urllib.request.urlopen(
                urllib.request.Request(
                    f"{self._host}/api/generate",
                    data=payload,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                ),
                timeout=30,
            ) as r:
                return r.status == 200
        except Exception:
            return False

    def unload_model(self, model_name: str) -> bool:
        return self.load_model(model_name, keep_alive=0)

    def list_models(self) -> list[ModelInfo]:
        try:
            with urllib.request.urlopen(f"{self._host}/api/tags", timeout=10) as r:
                tags = json.loads(r.read()).get("models", [])
            with urllib.request.urlopen(f"{self._host}/api/ps", timeout=10) as r:
                loaded = {m["name"] for m in json.loads(r.read()).get("models", [])}
            return [
                ModelInfo(
                    name=m["name"],
                    size_gb=round(m.get("size", 0) / 1024**3, 2),
                    vram_used_gb=0.0,
                    is_loaded=m["name"] in loaded,
                    quantization=m.get("details", {}).get("quantization_level"),
                )
                for m in tags
            ]
        except Exception:
            return []

    def get_status(self) -> dict:
        try:
            with urllib.request.urlopen(f"{self._host}/api/version", timeout=5) as r:
                ver = json.loads(r.read()).get("version", "unknown")
            return {"running": True, "url": self._host, "version": ver}
        except Exception:
            return {"running": False, "url": self._host}

    def generate(self, model, system, user, temperature=0.7, max_tokens=2048, timeout=120) -> GenerateResult:
        payload = json.dumps({
            "model": model,
            "system": system,
            "prompt": user,
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }).encode()
        req = urllib.request.Request(
            f"{self._host}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read())
        eval_ms = d.get("eval_duration", 1) / 1e6
        out_toks = d.get("eval_count", 0)
        return GenerateResult(
            text=d.get("response", ""),
            input_tokens=d.get("prompt_eval_count", 0),
            output_tokens=out_toks,
            tokens_per_sec=round(out_toks / eval_ms, 1) if eval_ms > 0 else 0.0,
            model=model,
            provider="ollama",
        )
```

### VllmManager (planned)

```python
# fleet/backends/vllm_manager.py
# vLLM exposes an OpenAI-compatible API at /v1/completions
# Implement LocalModelManager using httpx against vLLM's /v1 endpoint.
# load_model / unload_model map to vLLM's --model flag and /v1/models admin API.
```

### LlamaCppManager (planned)

```python
# fleet/backends/llamacpp_manager.py
# llama.cpp server at /completion endpoint.
# Model loading = server restart with different --model flag (managed by subprocess).
# list_models reads the single loaded model from /v1/models.
```

### LmStudioManager (planned)

```python
# fleet/backends/lm_studio_manager.py
# LM Studio exposes OpenAI-compatible API at localhost:1234.
# list_models reads /v1/models; generate uses /v1/chat/completions.
# load/unload controlled via LM Studio REST management API.
```

---

## Backend Registry

```python
# fleet/local_model_manager.py  (extended)
_REGISTRY: dict[str, type[LocalModelManager]] = {}

def register_backend(name: str, cls: type[LocalModelManager]) -> None:
    _REGISTRY[name] = cls

def get_backend(name: str, **kwargs) -> LocalModelManager:
    if name not in _REGISTRY:
        raise ValueError(f"Unknown backend: {name}. Available: {list(_REGISTRY)}")
    return _REGISTRY[name](**kwargs)
```

Registered at import time in each backend module:
```python
# In ollama_manager.py
from local_model_manager import register_backend
register_backend("ollama", OllamaManager)
```

---

## Migration Path from providers.py

### Current state
`providers.py` calls Ollama directly:
```python
# providers.py — current
import urllib.request
resp = urllib.request.urlopen(f"{host}/api/generate", data=..., timeout=120)
```

### Phase 1 — Wrap existing calls (non-breaking)
- Create `fleet/local_model_manager.py` with ABC
- Create `fleet/backends/ollama_manager.py` wrapping current Ollama calls
- In `providers.py`, instantiate `OllamaManager` and delegate to it
- All existing behavior unchanged; backend is now swappable

### Phase 2 — hw_supervisor migration
- Replace `hw_supervisor.py` keepalive/eviction calls with `OllamaManager.load_model` / `unload_model`
- `hw_supervisor` reads backend name from `fleet.toml [models] backend = "ollama"`

### Phase 3 — Add vLLM / llama.cpp backends
- Implement `VllmManager` and `LlamaCppManager`
- Add `backend` key to `fleet.toml` and `config.py`
- Fleet selects backend at boot: `get_backend(cfg["models"]["backend"])`

### Phase 4 — Remove Ollama-specific code from providers.py
- All HTTP calls go through the ABC
- Ollama is one backend among equals

---

## Config Schema (fleet.toml)

```toml
[models]
backend = "ollama"          # "ollama" | "vllm" | "llamacpp" | "lm_studio"
ollama_host = "http://localhost:11434"
vllm_host   = "http://localhost:8000"   # only if backend = "vllm"
llamacpp_host = "http://localhost:8080" # only if backend = "llamacpp"
lm_studio_host = "http://localhost:1234"
```

---

## Testing Strategy

Each backend implements the same ABC → shared test suite:
```python
# tests/test_local_model_manager.py
class BackendContractTests:
    """Mixin: run against any LocalModelManager implementation."""
    def test_list_models_returns_list(self): ...
    def test_get_status_has_running_key(self): ...
    def test_generate_returns_result_dataclass(self): ...

class TestOllamaManager(BackendContractTests, unittest.TestCase):
    def setUp(self):
        self.mgr = OllamaManager("http://localhost:11434")
```

---

## Dependencies

- No new packages required for Ollama backend
- vLLM backend: `httpx` (already used in codebase)
- llama.cpp / LM Studio: `httpx`
- ABC uses stdlib `abc` only

---

## Not in Scope

- Streaming generation (future: add `generate_stream()` to ABC)
- Multi-GPU load balancing (tracked separately in geo_fleet.py)
- Model download/pull (stays in providers.py `_pull_model`)
